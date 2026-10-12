"""Eval harness. It holds the candidate answers. The analyst never receives them."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from flight_analyst.analyst import Analyst
from flight_analyst.bank import load_bank
from flight_analyst.compare import Comparison, EntityResolver, categorize, compare_result, prose_rubric
from flight_analyst.definitions import GRADER_RUBRIC
from flight_analyst.ingest import connect_readonly
from flight_analyst.paths import BANK_PATH, DB_PATH, REPORTS_DIR, RESULTS_DIR
from flight_analyst.pricing import SCHEMA_VERSION, UnpricedModel, over_budget, price_row, quote
from flight_analyst.report import write_reports


def _resolver(con) -> EntityResolver:
    pairs = []
    pairs.extend(con.execute("SELECT airline_code, airline_name FROM carriers").fetchall())
    pairs.extend(con.execute("SELECT airport_code, airport_name FROM airports").fetchall())
    return EntityResolver.from_pairs([(str(code), str(name)) for code, name in pairs if code])


def _trim_rows(rows: list[dict] | None, limit: int = 12) -> list[dict]:
    return list(rows or [])[:limit]


def _block(rows: list[dict]) -> dict:
    n = len(rows)
    n_correct = sum(int(row["passed"]) for row in rows)
    return {
        "n": n,
        "n_correct": n_correct,
        "accuracy": round(n_correct / n, 6) if n else None,
    }


def model_run_document(report: dict) -> dict:
    """Provider-neutral file the site reads. OpenAI, xAI, and a local model use the same keys."""
    summary = report["summary"]
    price = report.get("price") or {}
    results = []
    for row in report["results"]:
        results.append(
            {
                "id": row["id"],
                "question": row["question"],
                "status": row.get("status") or "DRAFT",
                "difficulty": row.get("difficulty"),
                "answer": row.get("answer_text") or "",
                "grade": {
                    "passed": bool(row.get("passed")),
                    "failure_category": row.get("failure_category"),
                    "detail": row.get("detail"),
                },
                "input_tokens": int(row.get("input_tokens") or 0),
                "output_tokens": int(row.get("output_tokens") or 0),
                "cost_usd": row.get("cost_usd"),
                "latency_seconds": row.get("latency_seconds"),
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "provider": report.get("provider"),
        "model": report.get("model") or report.get("provider"),
        "created_at": report.get("created_at"),
        "price": {
            "input_per_million_usd": price.get("input_per_million_usd"),
            "output_per_million_usd": price.get("output_per_million_usd"),
            "source": price.get("source"),
            "as_of": price.get("as_of"),
        },
        "budget_cap_usd": summary.get("budget_cap_usd"),
        "spent_usd": summary.get("spent_usd"),
        "stopped_for_budget": bool(summary.get("stopped_for_budget")),
        "summary": {
            "all": summary.get("all_questions") or _block(report["results"]),
            "approved": summary.get("approved_questions") or _block(
                [row for row in report["results"] if row.get("status") == "APPROVED"]
            ),
            "cost_usd": summary.get("spent_usd"),
            "cost_per_100_usd": summary.get("cost_per_100_usd"),
            "input_tokens": summary.get("input_tokens"),
            "output_tokens": summary.get("output_tokens"),
            "latency_seconds": summary.get("latency_seconds"),
            "n_in_bank": summary.get("n_in_bank"),
            "n_unscored": summary.get("n_unscored"),
        },
        "results": results,
    }


def write_model_run(report: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model_run_document(report), indent=2) + "\n")
    return path


def run_evaluation(
    db_path: Path | None = None,
    bank_path: Path | None = None,
    client=None,
    out_dir: Path | None = None,
    budget_usd: float | None = None,
    model_out: Path | None = None,
) -> dict:
    db_path = db_path or DB_PATH
    bank = load_bank(bank_path or BANK_PATH)
    analyst = Analyst(db_path, client=client)
    con = connect_readonly(db_path)
    try:
        resolver = _resolver(con)
    finally:
        con.close()

    model_name = getattr(analyst.client, "model", None) or "stub"
    try:
        priced = price_row(model_name)
    except UnpricedModel:
        if budget_usd is not None:
            raise
        priced = None

    questions = list(bank["questions"])
    results = []
    spent = 0.0
    stopped = False
    for item in questions:
        if over_budget(spent, budget_usd):
            stopped = True
            break
        # The boundary is the question string. Notes, SQL, and candidate rows stay here.
        outcome = analyst.answer(item["question"])
        comparison: Comparison | None
        if outcome.infrastructure_error and not outcome.sql and not outcome.refused:
            comparison = Comparison(False, "error", outcome.infrastructure_error)
        else:
            comparison = compare_result(item, outcome, resolver)
        prose_ok, prose_detail, nudge = prose_rubric(item, outcome.answer_text)
        failure = categorize(item, outcome, comparison)
        detail = comparison.detail if comparison else outcome.infrastructure_error
        if failure is None and not prose_ok:
            failure = "misread_question"
            detail = prose_detail
        cost = quote(model_name, outcome.input_tokens, outcome.output_tokens) if priced else None
        if cost is not None:
            spent += cost
        results.append(
            {
                "id": item["id"],
                "question": item["question"],
                "status": item.get("status") or "DRAFT",
                "difficulty": item["difficulty"],
                "category": item["category"],
                "ambiguous": item.get("ambiguous", False),
                "passed": failure is None,
                "failure_category": failure,
                "detail": detail,
                "nudge_offered": nudge,
                "analyst_sql": outcome.sql,
                "reference_sql": item.get("sql"),
                "answer_text": outcome.answer_text,
                "refusal_reason": outcome.refusal_reason,
                "expected_rows": _trim_rows(item.get("expected", {}).get("rows")),
                "actual_rows": _trim_rows(outcome.rows),
                "chart": outcome.chart,
                "input_tokens": outcome.input_tokens,
                "output_tokens": outcome.output_tokens,
                "cost_usd": cost,
                "latency_seconds": round(outcome.latency_seconds, 4),
            }
        )

    by_difficulty: dict[str, dict] = {}
    by_category: dict[str, dict] = {}
    by_failure: dict[str, int] = {}
    for row in results:
        for bucket, key in ((by_difficulty, row["difficulty"]), (by_category, row["category"])):
            slot = bucket.setdefault(key, {"n": 0, "n_correct": 0})
            slot["n"] += 1
            slot["n_correct"] += int(row["passed"])
        if row["failure_category"]:
            by_failure[row["failure_category"]] = by_failure.get(row["failure_category"], 0) + 1
    for bucket in (by_difficulty, by_category):
        for slot in bucket.values():
            slot["accuracy"] = round(slot["n_correct"] / slot["n"], 6) if slot["n"] else None

    n = len(results)
    n_correct = sum(int(row["passed"]) for row in results)
    n_nudge = sum(int(row.get("nudge_offered")) for row in results)
    all_questions = _block(results)
    approved_questions = _block([row for row in results if row.get("status") == "APPROVED"])
    input_tokens = sum(int(row.get("input_tokens") or 0) for row in results)
    output_tokens = sum(int(row.get("output_tokens") or 0) for row in results)
    latency_seconds = round(sum(float(row.get("latency_seconds") or 0) for row in results), 4)
    cost_per_100 = round(spent / n * 100, 8) if n else None
    report = {
        "status": "DRAFT",
        "review_note": bank.get("review_note"),
        "scoring_basis": "Scored against DRAFT candidate answers where a question is still draft. " + GRADER_RUBRIC,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "provider": getattr(analyst.client, "name", "unknown"),
        "model": model_name,
        "price": priced,
        "data_window": {
            "flight_date_min": (bank.get("data_window") or {}).get("flight_date_min"),
            "flight_date_max": (bank.get("data_window") or {}).get("flight_date_max"),
            "row_count": (bank.get("data_window") or {}).get("row_count"),
            "months": [
                f"{item['year']}-{int(item['month']):02d}"
                for item in (bank.get("data_window") or {}).get("months") or []
            ],
        },
        "summary": {
            "n": n,
            "n_correct": n_correct,
            "accuracy": round(n_correct / n, 6) if n else None,
            "by_difficulty": by_difficulty,
            "by_category": by_category,
            "by_failure": by_failure,
            "advisory_nudge": {
                "n": n,
                "n_offered": n_nudge,
                "rate": round(n_nudge / n, 6) if n else None,
            },
            "all_questions": all_questions,
            "approved_questions": approved_questions,
            "budget_cap_usd": budget_usd,
            "spent_usd": round(spent, 8),
            "cost_per_100_usd": cost_per_100,
            "stopped_for_budget": stopped,
            "n_in_bank": len(questions),
            "n_unscored": len(questions) - n,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency_seconds": latency_seconds,
        },
        "results": results,
    }
    destination = out_dir or REPORTS_DIR
    write_reports(report, destination)
    if model_out is None:
        safe = "".join(ch if ch.isalnum() or ch in "-._" else "-" for ch in str(model_name)) or "model"
        folder = RESULTS_DIR if destination == REPORTS_DIR else destination
        model_out = folder / f"{safe}.json"
    report["model_run_path"] = str(write_model_run(report, model_out))
    return report
