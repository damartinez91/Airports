"""Miss reports on live answers.

A visitor who got a cached Sonnet answer or a live Haiku answer can say it
didn't answer the question, and optionally write what they expected. The
record lands in gitignored data/feedback.jsonl. It stores the question, the
answer shown, the model, the route, the match score when the route was
cached, the matched bank id when there was one, the free text, and a
timestamp. No client address, account, or contact field is stored.

The free text is capped at 1,000 characters. A cached-Sonnet miss also flags
that bank id in data/cached_recheck.json, because the embedding match may
have been wrong.

The static site has no live question box yet. ``feedback_hook_html`` and
``wrap_live_answer`` are the markup the box should include, and the live
process serves the same fragment at GET /feedback-hook. POST /feedback and
``python -m flight_analyst feedback`` record a report without a model call.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import re
import threading
from pathlib import Path
from typing import Callable

from flight_analyst.paths import DATA_DIR

FEEDBACK_PATH = DATA_DIR / "feedback.jsonl"
RECHECK_PATH = DATA_DIR / "cached_recheck.json"
EXPECTED_CAP = 1000
QUESTION_CAP = 2000
ANSWER_CAP = 12000

ROUTE_CACHED = "cached-sonnet"
ROUTE_HAIKU = "haiku"
ROUTES = (ROUTE_CACHED, ROUTE_HAIKU)

# Daniel picks one of these before a miss can be closed. Only send-to-sonnet
# re-answers and, once that answer exists, promotes into the anticipated bank.
FEEDBACK_CATEGORIES = (
    "send-to-sonnet",
    "fix-cached-answer",
    "fix-routing",
    "out-of-scope",
    "no-change",
)

FEEDBACK_CATEGORY_LABELS = {
    "send-to-sonnet": "send to Sonnet (re-answer and, once approved, promote to the anticipated bank)",
    "fix-cached-answer": "fix the cached answer",
    "fix-routing": "fix the routing or match threshold",
    "out-of-scope": "out of scope",
    "no-change": "no change",
}

_CATEGORY_ALIASES = {
    "send-to-sonnet": "send-to-sonnet",
    "send to sonnet": "send-to-sonnet",
    "fix-cached-answer": "fix-cached-answer",
    "fix the cached answer": "fix-cached-answer",
    "fix-routing": "fix-routing",
    "fix the routing or match threshold": "fix-routing",
    "fix routing": "fix-routing",
    "out-of-scope": "out-of-scope",
    "out of scope": "out-of-scope",
    "no-change": "no-change",
    "no change": "no-change",
}

_ROUTE_ALIASES = {
    "cached-sonnet": ROUTE_CACHED,
    "cached": ROUTE_CACHED,
    "cache": ROUTE_CACHED,
    "sonnet": ROUTE_CACHED,
    "haiku": ROUTE_HAIKU,
    "new": ROUTE_HAIKU,
    "live": ROUTE_HAIKU,
}

_SECRET_FIELDS = {
    "api_key",
    "apikey",
    "authorization",
    "openai_api_key",
    "anthropic_api_key",
    "token",
    "secret",
    "password",
    "provider",
}

STORED_FIELDS = (
    "id",
    "ts",
    "question",
    "answer_text",
    "model",
    "route",
    "match_score",
    "matched_id",
    "expected",
    "expected_truncated",
    "recheck_cached",
    "recheck_note",
)


def normalize_feedback_category(category: str | None) -> str:
    """Map a reviewer's words to a feedback slug. Empty and unknown values are refused."""
    if category is None or not str(category).strip():
        raise ValueError("A category is required to approve a question.")
    key = " ".join(str(category).strip().lower().split())
    slug = _CATEGORY_ALIASES.get(key)
    if slug is None:
        allowed = ", ".join(FEEDBACK_CATEGORIES)
        raise ValueError(f"A feedback miss needs a category: {allowed}.")
    return slug


def _clean_text(value: object, *, cap: int, label: str, required: bool) -> str:
    if value is None:
        text = ""
    elif not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    else:
        text = value.replace("\x00", "").strip()
    if required and not text:
        raise ValueError(f"{label} must be 1 to {cap} characters")
    if len(text) > cap:
        raise ValueError(f"{label} must be 1 to {cap} characters")
    return text


def _clean_expected(value: object) -> tuple[str, bool]:
    if value is None:
        return "", False
    if not isinstance(value, str):
        raise ValueError("expected must be text")
    text = value.replace("\x00", "").strip()
    if len(text) <= EXPECTED_CAP:
        return text, False
    return text[:EXPECTED_CAP], True


def _clean_model(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("model must be a short model id such as claude-haiku-4-5")
    text = value.strip()
    if not text or len(text) > 64 or not re.fullmatch(r"[A-Za-z0-9._:-]+", text):
        raise ValueError("model must be a short model id such as claude-haiku-4-5")
    return text


def _clean_route(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("route must be cached-sonnet or haiku")
    key = value.strip().lower().replace("_", "-")
    route = _ROUTE_ALIASES.get(key)
    if route is None:
        raise ValueError("route must be cached-sonnet or haiku")
    return route


def _clean_score(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("match_score must be a number from 0 to 1") from exc
    if not 0.0 <= score <= 1.0:
        raise ValueError("match_score must be a number from 0 to 1")
    return round(score, 4)


def _clean_matched_id(value: object) -> str | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", text):
        raise ValueError("matched_id must be a bank question id")
    return text


def build_record(payload: dict, *, now: dt.datetime, seq: int) -> dict:
    """Validate one report. Unknown keys (email, ip, and the like) are dropped."""
    if not isinstance(payload, dict):
        raise ValueError("body must be a JSON object")
    if _SECRET_FIELDS.intersection(payload):
        raise ValueError("credentials are not accepted")
    route = _clean_route(payload.get("route"))
    question = _clean_text(payload.get("question"), cap=QUESTION_CAP, label="question", required=True)
    answer = _clean_text(payload.get("answer_text", payload.get("answer")), cap=ANSWER_CAP, label="answer", required=True)
    model = _clean_model(payload.get("model"))
    expected, truncated = _clean_expected(payload.get("expected"))
    cached = route == ROUTE_CACHED
    matched_id = _clean_matched_id(payload.get("matched_id")) if cached else None
    match_score = _clean_score(payload.get("match_score")) if cached else None
    if cached and matched_id:
        note = f"Cached item {matched_id} is flagged for re-check; the embedding match may have been wrong."
    elif cached:
        note = "Cached Sonnet answer was reported as a miss; no matched question id was recorded."
    else:
        note = ""
    return {
        "id": f"fb-{now.date().isoformat()}-{seq:03d}",
        "ts": now.isoformat(),
        "question": question,
        "answer_text": answer,
        "model": model,
        "route": route,
        "match_score": match_score,
        "matched_id": matched_id,
        "expected": expected,
        "expected_truncated": truncated,
        "recheck_cached": cached,
        "recheck_note": note,
    }


class FeedbackLog:
    """Append-only JSONL at data/feedback.jsonl, plus the cached-item recheck index."""

    def __init__(
        self,
        path: Path | None = None,
        recheck_path: Path | None = None,
        now: Callable[[], dt.datetime] | None = None,
    ):
        self.path = path or FEEDBACK_PATH
        self.recheck_path = recheck_path or (self.path.parent / "cached_recheck.json")
        self.now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self._lock = threading.Lock()

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Unreadable feedback line in {self.path}") from exc
        return rows

    def read_day(self, day: dt.date) -> list[dict]:
        prefix = day.isoformat()
        return [row for row in self.read_all() if str(row.get("ts", "")).startswith(prefix)]

    def record(self, payload: dict) -> dict:
        with self._lock:
            stamp = self.now()
            seq = sum(1 for row in self.read_all() if str(row.get("ts", "")).startswith(stamp.date().isoformat()))
            record = build_record(payload, now=stamp, seq=seq)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
            if record["recheck_cached"] and record["matched_id"]:
                self._flag_recheck(record["matched_id"], record["id"], record["ts"], record["question"])
            return record

    def _flag_recheck(self, matched_id: str, report_id: str, ts: str, question: str) -> None:
        payload: dict = {}
        if self.recheck_path.exists():
            try:
                loaded = json.loads(self.recheck_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                loaded = {}
            if isinstance(loaded, dict):
                payload = loaded
        item = payload.get(matched_id)
        if not isinstance(item, dict):
            item = {
                "matched_id": matched_id,
                "question": question,
                "reason": "user-reported miss; the embedding match may have been wrong",
                "flagged_at": ts,
                "report_ids": [],
            }
        ids = item.setdefault("report_ids", [])
        if report_id not in ids:
            ids.append(report_id)
        item["question"] = item.get("question") or question
        item["flagged_at"] = item.get("flagged_at") or ts
        payload[matched_id] = item
        self.recheck_path.parent.mkdir(parents=True, exist_ok=True)
        self.recheck_path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")


def record_feedback(payload: dict, *, log: FeedbackLog | None = None) -> dict:
    """Append one report. Creates the default log under data/ when log is omitted."""
    return (log or FeedbackLog()).record(payload)


def queue_item_from_feedback(row: dict) -> dict:
    """One review-queue row. Rank these ahead of ordinary new questions."""
    expected = row.get("expected") or ""
    answer = row.get("answer_text") or ""
    question = row.get("question") or ""
    lines = [
        "Flag: user-reported miss",
        f"Question: {question}",
        f"Answer given: {answer}",
        f"What I expected: {expected}",
        f"Route: {row.get('route')}  Model: {row.get('model')}",
    ]
    if row.get("match_score") is not None:
        lines.append(f"Match score: {row.get('match_score')}")
    if row.get("matched_id"):
        lines.append(f"Matched anticipated question: {row.get('matched_id')}")
    if row.get("recheck_note"):
        lines.append(str(row["recheck_note"]))
    lines.append(
        "Category required: " + " | ".join(f"{slug} ({FEEDBACK_CATEGORY_LABELS[slug]})" for slug in FEEDBACK_CATEGORIES)
    )
    return {
        "id": row["id"],
        "user_reported_miss": True,
        "flag": "user-reported miss",
        "question": question,
        "answer_given": answer,
        "what_i_expected": expected,
        "model": row.get("model"),
        "route": row.get("route"),
        "match_score": row.get("match_score"),
        "matched_id": row.get("matched_id"),
        "recheck_cached": bool(row.get("recheck_cached")),
        "recheck_note": row.get("recheck_note") or "",
        "reported_at": row.get("ts"),
        "for_reviewer": "\n".join(lines),
        "category_choices": [
            {"category": slug, "label": FEEDBACK_CATEGORY_LABELS[slug]} for slug in FEEDBACK_CATEGORIES
        ],
        "status": "CANDIDATE",
        "category": None,
        "approved": False,
    }


def _attr(value: str) -> str:
    return html.escape(value, quote=True).replace("\n", "&#10;").replace("\r", "")


def feedback_hook_html(endpoint: str = "/feedback") -> str:
    """Button and optional box. Place it inside an element with data-live-answer."""
    action = _attr(endpoint)
    return f"""<form class="live-feedback" data-endpoint="{action}">
<button type="button" data-action="miss">This didn't answer my question</button>
<label data-expected hidden>What I expected
<textarea name="expected" maxlength="{EXPECTED_CAP}"></textarea>
</label>
<button type="submit" data-action="send" hidden>Send feedback</button>
<p class="feedback-status" role="status"></p>
<script>
(function () {{
  var form = document.currentScript && document.currentScript.parentElement;
  if (!form) return;
  var box = form.closest("[data-live-answer]");
  var miss = form.querySelector("[data-action=miss]");
  var expectedWrap = form.querySelector("[data-expected]");
  var send = form.querySelector("[data-action=send]");
  var status = form.querySelector(".feedback-status");
  var area = form.querySelector("textarea");
  miss.addEventListener("click", function () {{
    expectedWrap.hidden = false;
    send.hidden = false;
    if (area) area.focus();
  }});
  form.addEventListener("submit", function (event) {{
    event.preventDefault();
    if (!box) {{
      status.textContent = "No live answer is attached to this form.";
      return;
    }}
    var payload = {{
      question: box.getAttribute("data-question") || "",
      answer_text: box.getAttribute("data-answer") || "",
      route: box.getAttribute("data-route") || "",
      model: box.getAttribute("data-model") || "",
      expected: (area && area.value || "").slice(0, {EXPECTED_CAP})
    }};
    var score = box.getAttribute("data-match-score");
    var matched = box.getAttribute("data-matched-id");
    if (score) payload.match_score = Number(score);
    if (matched) payload.matched_id = matched;
    status.textContent = "Sending.";
    fetch(form.getAttribute("data-endpoint") || "/feedback", {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify(payload)
    }}).then(function (res) {{
      return res.json().then(function (body) {{ return {{ok: res.ok, body: body}}; }});
    }}).then(function (result) {{
      status.textContent = result.ok ? "Recorded. Thank you." : ((result.body && result.body.error) || "Could not record feedback.");
      if (result.ok) form.querySelectorAll("button,textarea").forEach(function (el) {{ el.disabled = true; }});
    }}).catch(function () {{
      status.textContent = "Could not record feedback.";
    }});
  }});
}})();
</script>
</form>"""


def wrap_live_answer(
    question: str,
    answer_text: str,
    *,
    route: str,
    model: str,
    match_score: float | None = None,
    matched_id: str | None = None,
    endpoint: str = "/feedback",
) -> str:
    """Live answer plus the miss button. The static pages do not call this yet."""
    attrs = [
        f'data-question="{_attr(question)}"',
        f'data-answer="{_attr(answer_text)}"',
        f'data-route="{_attr(route)}"',
        f'data-model="{_attr(model)}"',
    ]
    if match_score is not None:
        attrs.append(f'data-match-score="{_attr(str(match_score))}"')
    if matched_id:
        attrs.append(f'data-matched-id="{_attr(matched_id)}"')
    shown = html.escape(answer_text)
    return (
        f'<div class="live-answer" data-live-answer {" ".join(attrs)}>'
        f'<div class="live-answer-text">{shown}</div>'
        f"{feedback_hook_html(endpoint)}</div>"
    )
