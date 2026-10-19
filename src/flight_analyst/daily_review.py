"""Daily review of new live questions and user-reported misses.

Reads one day's live-question log (route == "new"), dedupes, and for up to
REVIEW_MAX_QUESTIONS of them asks Sonnet to answer, then grade its answer
against Haiku's live answer and the answering principles. Output is a queue.

The same queue also takes that day's rows from data/feedback.jsonl. Those are
flagged as user-reported misses, ranked first, and are not sent to a model
while the queue is built. Daniel closes each one with a required category:
send-to-sonnet, fix-cached-answer, fix-routing, out-of-scope, or no-change.
send-to-sonnet re-answers and grades, then saves a PENDING preview. It does
not write the anticipated bank. A later ``review-promote --approve ID`` does.

Daniel's rule: nothing becomes approved without explicit categorization.
Default mode is QUEUE: candidates wait in data/review_queue/<day>.json until
`python -m flight_analyst review-promote --day D --id ID --category C` is run.

AUTO_PROMOTE=1 is an opt-in alternative: a candidate that (a) Sonnet graded as
correct, (b) agrees with Haiku's figures, and (c) ran SQL without error or
refusal is added to the anticipated bank with status "AUTO" and category
"auto-unreviewed". It is then served as a cached answer, but is never marked
APPROVED and never enters the graded bank (questions/bank.json). Off by default.
User-reported misses are never auto-promoted.

Spend: REVIEW_SPEND_CAP_USD (default 2.00) per run, estimated with
REVIEW_USD_PER_CALL (default 0.05; two Sonnet calls per question). The job
stops before the question that would cross the cap. Feedback rows do not
spend that budget.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Callable

from flight_analyst.feedback import (
    FEEDBACK_CATEGORIES,
    FEEDBACK_CATEGORY_LABELS,
    FeedbackLog,
    normalize_feedback_category,
    queue_item_from_feedback,
)
from flight_analyst.paths import DATA_DIR
from flight_analyst.routing import AnticipatedBank, BankEntry, QuestionLog, cosine

QUEUE_DIR = DATA_DIR / "review_queue"
PENDING_PATH = DATA_DIR / "pending_promotions.json"
REVIEW_MODEL = "claude-sonnet-4-5"
DEDUPE_THRESHOLD = 0.9

GRADE_PROMPT = (
    "You are grading an answer from a US flight on-time analyst. Reply with one JSON object: "
    '{"correct": true|false, "agrees_with_reference": true|false, "suggested_category": "<short>", "notes": "<one line>"}. '
    "correct means the figures answer the question as asked, with units, following the answering principles. "
    "agrees_with_reference means the reference answer's figures match within rounding."
)


def _env_money(name: str, default: str) -> Decimal:
    return Decimal(os.environ.get(name, default).strip() or default)


@dataclass
class ReviewConfig:
    max_questions: int = 25
    spend_cap_usd: Decimal = Decimal("2.00")
    usd_per_call: Decimal = Decimal("0.05")
    auto_promote: bool = False

    @classmethod
    def from_env(cls) -> "ReviewConfig":
        return cls(
            max_questions=int(os.environ.get("REVIEW_MAX_QUESTIONS", "25")),
            spend_cap_usd=_env_money("REVIEW_SPEND_CAP_USD", "2.00"),
            usd_per_call=_env_money("REVIEW_USD_PER_CALL", "0.05"),
            auto_promote=os.environ.get("AUTO_PROMOTE", "").strip() == "1",
        )


def _dedupe(records: list[dict], embedder) -> list[dict]:
    kept, vectors = [], []
    for rec in records:
        if rec.get("route") != "new" or rec.get("refused"):
            continue
        vec = embedder.embed(rec["question"])
        if any(cosine(vec, other) >= DEDUPE_THRESHOLD for other in vectors):
            continue
        kept.append(rec)
        vectors.append(vec)
    return kept


def _parse_grade(text: str) -> dict:
    match = re.search(r"\{.*\}", text or "", re.S)
    try:
        return json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        return {}


def run_review(
    day: dt.date,
    answer_fn: Callable[[str], dict],
    grade_fn: Callable[[str, str], str],
    *,
    log: QuestionLog | None = None,
    bank: AnticipatedBank | None = None,
    config: ReviewConfig | None = None,
    queue_dir: Path | None = None,
    bank_path: Path | None = None,
    feedback: FeedbackLog | None = None,
) -> dict:
    """answer_fn(question) -> analyst dict from Sonnet; grade_fn(system, user) -> Sonnet text.

    Feedback rows are copied into the queue first and do not call answer_fn or grade_fn.
    """
    config = config or ReviewConfig.from_env()
    log = log or QuestionLog()
    bank = bank or AnticipatedBank.load(bank_path, all_statuses=True)
    queue_dir = queue_dir or QUEUE_DIR
    feedback = feedback if feedback is not None else FeedbackLog()
    misses = [queue_item_from_feedback(row) for row in feedback.read_day(day)]
    records = _dedupe(log.read(day), bank.embedder)
    spent = Decimal("0")
    per_question = config.usd_per_call * 2
    candidates, skipped = [], 0
    for index, rec in enumerate(records):
        if index >= config.max_questions or spent + per_question > config.spend_cap_usd:
            skipped = len(records) - index
            break
        spent += per_question
        sonnet = answer_fn(rec["question"])
        grade = _parse_grade(grade_fn(GRADE_PROMPT, json.dumps({
            "question": rec["question"],
            "answer": sonnet.get("answer_text"),
            "reference_answer_from_haiku": rec.get("answer_text"),
        })))
        auto_ok = bool(grade.get("correct") and grade.get("agrees_with_reference")
                       and not sonnet.get("sql_error") and not sonnet.get("refused"))
        cand = {
            "id": f"live-{day.isoformat()}-{index:03d}",
            "question": rec["question"],
            "haiku_answer": rec.get("answer_text"),
            "sonnet_answer": sonnet.get("answer_text"),
            "sonnet_sql": sonnet.get("sql"),
            "grade": grade,
            "auto_eligible": auto_ok,
            "status": "CANDIDATE",
            "category": None,
        }
        if config.auto_promote and auto_ok:
            bank.add(BankEntry(id=cand["id"], question=cand["question"], answer_text=cand["sonnet_answer"] or "",
                               sql=cand["sonnet_sql"], model=REVIEW_MODEL, source=f"daily-review {day}",
                               status="AUTO", category="auto-unreviewed"))
            cand["status"] = "AUTO"
        candidates.append(cand)
    if config.auto_promote and any(c["status"] == "AUTO" for c in candidates):
        bank.save(bank_path)
    # Misses stay first even when auto-promote rewrites other rows. They are never auto-approved.
    candidates = misses + candidates
    queue = {
        "day": day.isoformat(),
        "mode": "auto-promote" if config.auto_promote else "queue-for-approval",
        "estimated_spend_usd": str(spent),
        "spend_cap_usd": str(config.spend_cap_usd),
        "skipped_for_cap": skipped,
        "user_reported_misses": len(misses),
        "ordering": "user-reported misses first, then new questions",
        "feedback_categories": [
            {"category": slug, "label": FEEDBACK_CATEGORY_LABELS[slug]} for slug in FEEDBACK_CATEGORIES
        ],
        "candidates": candidates,
    }
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / f"{day.isoformat()}.json").write_text(json.dumps(queue, indent=1))
    return queue


def _sonnet_reanswer(question: str, answer_fn: Callable[[str], dict] | None) -> dict:
    """One Sonnet answer. Used only after Daniel picks send-to-sonnet. Tests pass answer_fn."""
    if answer_fn is None:
        answer_fn = sonnet_functions()[0]
    result = answer_fn(question) or {}
    text = result.get("answer_text") or ""
    if not str(text).strip() or result.get("refused") or result.get("sql_error"):
        raise RuntimeError("Sonnet did not return an answer to promote. Nothing was marked approved.")
    return result


def _grade_reanswer(question: str, new_answer: str, old_answer: str, grade_fn: Callable[[str, str], str] | None) -> dict:
    """Same grader as daily review. Tests pass grade_fn; the real path calls Sonnet."""
    if grade_fn is None:
        grade_fn = sonnet_functions()[1]
    raw = grade_fn(GRADE_PROMPT, json.dumps({
        "question": question,
        "answer": new_answer,
        "reference_answer_from_haiku": old_answer,
    }))
    if isinstance(raw, dict):
        return raw
    return _parse_grade(str(raw or ""))


def _old_answer(cand: dict) -> str:
    return str(cand.get("answer_given") or cand.get("old_answer") or cand.get("haiku_answer") or "")


def load_pending(path: Path | None = None) -> dict:
    path = path or PENDING_PATH
    if not path.exists():
        return {}
    payload = json.loads(path.read_text())
    items = payload.get("items") if isinstance(payload, dict) else None
    return items if isinstance(items, dict) else {}


def save_pending(items: dict, path: Path | None = None) -> None:
    path = path or PENDING_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"items": items}, indent=1) + "\n")


def format_preview(item: dict) -> str:
    """What Daniel reads before approve: question, old answer, new answer, grader."""
    grade = item.get("grade") if isinstance(item.get("grade"), dict) else {}
    shown = {
        "correct": grade.get("correct"),
        "agrees_with_reference": grade.get("agrees_with_reference"),
        "suggested_category": grade.get("suggested_category"),
        "notes": grade.get("notes"),
    }
    return "\n".join([
        f"id: {item.get('id')}",
        f"status: {item.get('status')}",
        f"Question: {item.get('question')}",
        f"Old answer: {item.get('old_answer')}",
        f"Sonnet answer: {item.get('sonnet_answer')}",
        "Grader: " + json.dumps(shown, sort_keys=True),
    ])


def category_is_send_to_sonnet(category: str) -> bool:
    try:
        return normalize_feedback_category(category) == "send-to-sonnet"
    except ValueError:
        return False


def _apply_preview(cand: dict, item: dict) -> None:
    cand["status"] = "PENDING"
    cand["category"] = "send-to-sonnet"
    cand["approved"] = False
    cand["old_answer"] = item.get("old_answer")
    cand["sonnet_answer"] = item.get("sonnet_answer")
    cand["sonnet_sql"] = item.get("sonnet_sql")
    cand["grade"] = item.get("grade")
    cand["preview"] = {
        "question": item.get("question"),
        "old_answer": item.get("old_answer"),
        "sonnet_answer": item.get("sonnet_answer"),
        "grade": item.get("grade"),
    }
    cand["for_reviewer"] = format_preview(item)


def _mark_queue(queue_dir: Path, item: dict, status: str, approved: bool) -> None:
    day = item.get("day")
    if not day:
        return
    path = queue_dir / f"{day}.json"
    if not path.exists():
        return
    queue = json.loads(path.read_text())
    for cand in queue.get("candidates") or []:
        if cand.get("id") != item.get("id"):
            continue
        cand["status"] = status
        cand["approved"] = approved
        cand["category"] = "send-to-sonnet"
        if status == "REJECTED":
            cand["note"] = item.get("note") or ""
        if status == "APPROVED":
            cand["sonnet_answer"] = item.get("sonnet_answer")
            cand["for_reviewer"] = format_preview(item)
    path.write_text(json.dumps(queue, indent=1))


def _not_pending(ids: list[str], pending: dict) -> list[str]:
    missing = []
    for item_id in ids:
        row = pending.get(item_id)
        if not isinstance(row, dict) or row.get("status") != "PENDING":
            missing.append(item_id)
    return missing


def promote(day: dt.date, ids: list[str], category: str, *, queue_dir: Path | None = None,
            bank_path: Path | None = None, answer_fn: Callable[[str], dict] | None = None,
            grade_fn: Callable[[str, str], str] | None = None, pending_path: Path | None = None) -> int:
    """Daniel's explicit categorization.

    A category is required. New-question candidates become APPROVED in the anticipated
    bank. A user-reported miss must use a feedback category. send-to-sonnet stores a
    PENDING preview and does not write the bank. The other four categories are recorded
    and do not promote. The daily-review spend cap is unchanged: this command is not
    part of that run.
    """
    if not category or not str(category).strip():
        raise ValueError("A category is required to approve a question.")
    category = str(category).strip()
    queue_dir = queue_dir or QUEUE_DIR
    path = queue_dir / f"{day.isoformat()}.json"
    queue = json.loads(path.read_text())
    selected = [cand for cand in queue["candidates"] if cand["id"] in ids]
    misses = [cand for cand in selected if cand.get("user_reported_miss")]
    others = [cand for cand in selected if not cand.get("user_reported_miss")]
    if misses and others:
        raise ValueError("Promote feedback misses separately from new-question candidates.")
    feedback_category = normalize_feedback_category(category) if misses else None
    bank = None
    mutated = False
    done = 0
    if feedback_category == "send-to-sonnet":
        pending = load_pending(pending_path)
        staged: list[tuple[dict, dict]] = []
        for cand in misses:
            existing = pending.get(cand["id"])
            if isinstance(existing, dict) and existing.get("status") == "PENDING":
                staged.append((cand, existing))
                continue
            if isinstance(existing, dict) and existing.get("status") == "APPROVED":
                raise ValueError(f"{cand['id']} is already approved.")
            reanswer = _sonnet_reanswer(cand["question"], answer_fn)
            old = _old_answer(cand)
            grade = _grade_reanswer(cand["question"], str(reanswer.get("answer_text") or ""), old, grade_fn)
            staged.append((cand, {
                "id": cand["id"],
                "status": "PENDING",
                "question": cand["question"],
                "old_answer": old,
                "what_i_expected": cand.get("what_i_expected") or "",
                "sonnet_answer": reanswer.get("answer_text"),
                "sonnet_sql": reanswer.get("sql"),
                "grade": grade,
                "category": "send-to-sonnet",
                "model": REVIEW_MODEL,
                "day": queue["day"],
                "route": cand.get("route"),
                "matched_id": cand.get("matched_id"),
                "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            }))
        for cand, item in staged:
            pending[cand["id"]] = item
            _apply_preview(cand, item)
            done += 1
        if done:
            save_pending(pending, pending_path)
            path.write_text(json.dumps(queue, indent=1))
        return done
    if others:
        bank = AnticipatedBank.load(bank_path, all_statuses=True)
        for cand in others:
            bank.add(BankEntry(id=cand["id"], question=cand["question"], answer_text=cand.get("sonnet_answer") or "",
                               sql=cand.get("sonnet_sql"), model=REVIEW_MODEL, source=f"daily-review {queue['day']}",
                               status="APPROVED", category=category))
            cand["status"], cand["category"] = "APPROVED", category
            done += 1
            mutated = True
    if misses:
        for cand in misses:
            if cand.get("status") == "APPROVED":
                done += 1
                continue
            cand["status"] = "REVIEWED"
            cand["category"] = feedback_category
            cand["approved"] = False
            done += 1
    if mutated and bank is not None:
        bank.save(bank_path)
    if done:
        path.write_text(json.dumps(queue, indent=1))
    return done


def approve_pending(ids: list[str], *, pending_path: Path | None = None, bank_path: Path | None = None,
                    queue_dir: Path | None = None) -> int:
    """Move PENDING previews into the anticipated bank as APPROVED. Other ids are refused."""
    if not ids:
        raise ValueError("An id is required.")
    pending = load_pending(pending_path)
    missing = _not_pending(ids, pending)
    if missing:
        raise ValueError("Approve refuses ids that are not pending: " + ", ".join(missing))
    queue_dir = queue_dir or QUEUE_DIR
    bank = AnticipatedBank.load(bank_path, all_statuses=True)
    for item_id in ids:
        item = pending[item_id]
        bank.add(BankEntry(
            id=item["id"], question=item["question"], answer_text=item.get("sonnet_answer") or "",
            sql=item.get("sonnet_sql"), model=item.get("model") or REVIEW_MODEL,
            source=f"feedback {item.get('day')}", status="APPROVED", category="send-to-sonnet",
        ))
        item["status"] = "APPROVED"
        _mark_queue(queue_dir, item, "APPROVED", True)
    bank.save(bank_path)
    save_pending(pending, pending_path)
    return len(ids)


def reject_pending(ids: list[str], note: str = "", *, pending_path: Path | None = None,
                   queue_dir: Path | None = None) -> int:
    """Drop PENDING previews. Does not write the anticipated bank."""
    if not ids:
        raise ValueError("An id is required.")
    pending = load_pending(pending_path)
    missing = _not_pending(ids, pending)
    if missing:
        raise ValueError("Reject refuses ids that are not pending: " + ", ".join(missing))
    queue_dir = queue_dir or QUEUE_DIR
    cleaned = (note or "").replace("\x00", "").strip()[:1000]
    for item_id in ids:
        item = pending[item_id]
        item["status"] = "REJECTED"
        item["note"] = cleaned
        _mark_queue(queue_dir, item, "REJECTED", False)
    save_pending(pending, pending_path)
    return len(ids)


def sonnet_functions():
    """Real Sonnet wiring. Needs ANTHROPIC_API_KEY; pins the model so env defaults can't change it."""
    from flight_analyst.analyst import Analyst
    from flight_analyst.llm import AnthropicClient
    from flight_analyst.paths import DB_PATH

    client = AnthropicClient(os.environ["ANTHROPIC_API_KEY"], REVIEW_MODEL)
    analyst = Analyst(DB_PATH, client=client)

    def answer(question: str) -> dict:
        r = analyst.answer(question)
        return {"answer_text": r.answer_text, "sql": r.sql, "sql_error": r.sql_error, "refused": r.refused}

    def grade(system: str, user: str) -> str:
        return client.complete(system, user).text

    return answer, grade
