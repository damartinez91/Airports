"""Execute reference SQL and write the draft question bank.

The analyst package does not import this module.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from flight_analyst.compare import METRIC_ALIASES
from flight_analyst.paths import BANK_PATH, DATA_WINDOW_PATH, DB_PATH
from flight_analyst.questions import build_questions

REVIEW_NOTE = "candidate answers pending Daniel's review"


def _jsonify(value):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        if value.is_integer() and abs(value) < 1e15:
            return int(value)
        return round(value, 6)
    if isinstance(value, int):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def run_sql(con, sql: str) -> tuple[list[str], list[dict]]:
    cursor = con.execute(sql)
    columns = [item[0] for item in cursor.description]
    rows = []
    for record in cursor.fetchall():
        rows.append({column: _jsonify(value) for column, value in zip(columns, record)})
    return columns, rows


def load_bank(path: Path | None = None) -> dict:
    bank_path = path or BANK_PATH
    return json.loads(bank_path.read_text())


def _attach_aliases(question: dict) -> None:
    for spec in question.get("values") or []:
        spec["aliases"] = list(dict.fromkeys([spec["name"], *METRIC_ALIASES.get(spec["name"], [])]))


def build_bank(con, data_window: dict | None = None) -> dict:
    questions = build_questions()
    texts = [item["question"] for item in questions]
    if len(texts) != len(set(texts)):
        raise RuntimeError("Duplicate question text in the bank.")
    if len(questions) < 100:
        raise RuntimeError(f"Expected at least 100 questions, found {len(questions)}.")
    built = []
    for index, question in enumerate(questions, start=1):
        question = dict(question)
        question["id"] = f"q{index:03d}"
        _attach_aliases(question)
        if question["answer_type"] == "refusal":
            question["expected"] = {"behavior": "refuse", "rows": []}
            built.append(question)
            continue
        _columns, rows = run_sql(con, question["sql"])
        if not rows:
            raise RuntimeError(f"{question['id']} returned no rows: {question['question']}")
        for row in rows:
            for spec in question["values"]:
                if row.get(spec["name"]) is None:
                    raise RuntimeError(f"{question['id']} has a null {spec['name']}: {question['question']}")
        question["expected"] = {"rows": rows}
        built.append(question)
    return {
        "status": "DRAFT",
        "review_note": REVIEW_NOTE,
        "scoring_basis": "DRAFT candidate answers computed from reference SQL. Not an approved standard.",
        "data_window": data_window,
        "question_count": len(built),
        "questions": built,
    }


def write_bank(payload: dict, path: Path | None = None) -> Path:
    bank_path = path or BANK_PATH
    bank_path.parent.mkdir(parents=True, exist_ok=True)
    bank_path.write_text(json.dumps(payload, indent=2) + "\n")
    return bank_path


def build_and_write(db_path: Path | None = None, bank_path: Path | None = None) -> Path:
    from flight_analyst.ingest import connect_readonly

    path = db_path or DB_PATH
    window = None
    if DATA_WINDOW_PATH.exists():
        window = json.loads(DATA_WINDOW_PATH.read_text())
    con = connect_readonly(path)
    try:
        if window is None:
            raw = con.execute("SELECT value FROM metadata WHERE key = 'data_window'").fetchone()
            window = json.loads(raw[0]) if raw else None
        payload = build_bank(con, window)
    finally:
        con.close()
    return write_bank(payload, bank_path)
