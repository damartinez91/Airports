"""Daily review of new live questions and user-reported misses.

Reads one day's live-question log (route == "new"), dedupes, and for up to
REVIEW_MAX_QUESTIONS of them asks Sonnet to answer, then grade its answer
against Haiku's live answer and the answering principles. Output is a queue.

The same queue also takes that day's rows from data/feedback.jsonl. Those are
flagged as user-reported misses, ranked first, and are not sent to a model
while the queue is built. Daniel closes each one with a required category:
send-to-sonnet, fix-cached-answer, fix-routing, out-of-scope, or no-change.
Only send-to-sonnet re-answers (one Sonnet call at promote time) and then
marks the entry APPROVED in the anticipated bank.

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


def promote(day: dt.date, ids: list[str], category: str, *, queue_dir: Path | None = None,
            bank_path: Path | None = None, answer_fn: Callable[[str], dict] | None = None) -> int:
    """Daniel's explicit categorization.

    A category is required. New-question candidates become APPROVED in the anticipated
    bank. A user-reported miss must use a feedback category. Only send-to-sonnet
    re-answers and is marked APPROVED. The other four categories are recorded and
    do not promote.
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
    prepared: list[tuple[dict, dict | None]] = []
    if feedback_category == "send-to-sonnet":
        for cand in misses:
            if cand.get("status") == "APPROVED":
                prepared.append((cand, None))
                continue
            prepared.append((cand, _sonnet_reanswer(cand["question"], answer_fn)))
    bank = None
    mutated = False
    done = 0
    if others:
        bank = AnticipatedBank.load(bank_path, all_statuses=True)
        for cand in others:
            bank.add(BankEntry(id=cand["id"], question=cand["question"], answer_text=cand.get("sonnet_answer") or "",
                               sql=cand.get("sonnet_sql"), model=REVIEW_MODEL, source=f"daily-review {queue['day']}",
                               status="APPROVED", category=category))
            cand["status"], cand["category"] = "APPROVED", category
            done += 1
            mutated = True
    if misses and feedback_category == "send-to-sonnet":
        bank = bank or AnticipatedBank.load(bank_path, all_statuses=True)
        for cand, reanswer in prepared:
            if reanswer is None:
                done += 1
                continue
            cand["sonnet_answer"] = reanswer.get("answer_text")
            cand["sonnet_sql"] = reanswer.get("sql")
            cand["status"] = "APPROVED"
            cand["category"] = feedback_category
            cand["approved"] = True
            bank.add(BankEntry(
                id=cand["id"], question=cand["question"], answer_text=cand["sonnet_answer"] or "",
                sql=cand.get("sonnet_sql"), model=REVIEW_MODEL, source=f"feedback {queue['day']}",
                status="APPROVED", category=feedback_category,
            ))
            done += 1
            mutated = True
    elif misses:
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
