"""Eval harness. It holds the candidate answers. The analyst never receives them."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from flight_analyst.analyst import Analyst
from flight_analyst.bank import load_bank
from flight_analyst.compare import Comparison, EntityResolver, categorize, compare_result
from flight_analyst.ingest import connect_readonly
from flight_analyst.paths import BANK_PATH, DB_PATH, REPORTS_DIR
from flight_analyst.report import write_reports


def _resolver(con) -> EntityResolver:
    pairs = []
    pairs.extend(con.execute("SELECT airline_code, airline_name FROM carriers").fetchall())
    pairs.extend(con.execute("SELECT airport_code, airport_name FROM airports").fetchall())
    return EntityResolver.from_pairs([(str(code), str(name)) for code, name in pairs if code])


def _trim_rows(rows: list[dict] | None, limit: int = 12) -> list[dict]:
    return list(rows or [])[:limit]


def run_evaluation(
    db_path: Path | None = None,
    bank_path: Path | None = None,
    client=None,
    out_dir: Path | None = None,
) -> dict:
    db_path = db_path or DB_PATH
    bank = load_bank(bank_path or BANK_PATH)
    analyst = Analyst(db_path, client=client)
    con = connect_readonly(db_path)
    try:
        resolver = _resolver(con)
    finally:
        con.close()

    results = []
    for item in bank["questions"]:
        # The boundary is the question string. Notes, SQL, and candidate rows stay here.
        outcome = analyst.answer(item["question"])
        comparison: Comparison | None
        if outcome.infrastructure_error and not outcome.sql and not outcome.refused:
            comparison = Comparison(False, "error", outcome.infrastructure_error)
        else:
            comparison = compare_result(item, outcome, resolver)
        failure = categorize(item, outcome, comparison)
        results.append(
            {
                "id": item["id"],
                "question": item["question"],
                "difficulty": item["difficulty"],
                "category": item["category"],
                "ambiguous": item.get("ambiguous", False),
                "passed": failure is None,
                "failure_category": failure,
                "detail": comparison.detail if comparison else outcome.infrastructure_error,
                "analyst_sql": outcome.sql,
                "reference_sql": item.get("sql"),
                "answer_text": outcome.answer_text,
                "refusal_reason": outcome.refusal_reason,
                "expected_rows": _trim_rows(item.get("expected", {}).get("rows")),
                "actual_rows": _trim_rows(outcome.rows),
                "chart": outcome.chart,
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
    report = {
        "status": "DRAFT",
        "review_note": bank.get("review_note"),
        "scoring_basis": "Scored against DRAFT candidate answers. Not an approved standard.",
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "provider": getattr(analyst.client, "name", "unknown"),
        "model": getattr(analyst.client, "model", None),
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
        },
        "results": results,
    }
    write_reports(report, out_dir or REPORTS_DIR)
    return report
