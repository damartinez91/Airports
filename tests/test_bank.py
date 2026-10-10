import json
from pathlib import Path

from flight_analyst.bank import REVIEW_NOTE, load_bank, run_sql
from flight_analyst.compare import EntityResolver, compare_result
from flight_analyst.paths import BANK_PATH, DB_PATH
from flight_analyst.questions import build_questions

ROOT = Path(__file__).resolve().parents[1]


def test_question_specs_cover_the_week_one_themes():
    questions = build_questions()
    assert len(questions) >= 100
    difficulties = {item["difficulty"] for item in questions}
    categories = {item["category"] for item in questions}
    assert difficulties == {"easy", "medium", "hard"}
    assert {"airline_reliability", "airport_reliability", "routes", "time_of_day", "seasonality", "delay_causes", "ambiguous_or_trick"} <= categories
    assert any(item["answer_type"] == "refusal" for item in questions)
    assert any(item["ambiguous"] for item in questions)
    assert len({item["question"] for item in questions}) == len(questions)


def test_committed_bank_is_marked_draft():
    bank = load_bank(BANK_PATH)
    assert bank["status"] == "DRAFT"
    assert bank["review_note"] == REVIEW_NOTE
    assert bank["question_count"] == len(bank["questions"]) >= 100
    for question in bank["questions"]:
        assert question["status"] == "DRAFT"
        assert question["id"]
        assert question["notes"]
        if question["answer_type"] == "refusal":
            assert question["sql"] is None
            assert question["expected"]["behavior"] == "refuse"
        else:
            assert question["sql"]
            assert question["expected"]["rows"]


def test_reference_sql_recomputes_the_stored_rows():
    if not DB_PATH.exists():
        return
    bank = json.loads((ROOT / "questions" / "bank.json").read_text())
    from flight_analyst.ingest import connect_readonly

    con = connect_readonly(DB_PATH)
    try:
        pairs = con.execute("SELECT airline_code, airline_name FROM carriers").fetchall()
        pairs += con.execute("SELECT airport_code, airport_name FROM airports").fetchall()
        resolver = EntityResolver.from_pairs([(str(code), str(name)) for code, name in pairs])
        for question in bank["questions"]:
            if question["answer_type"] == "refusal":
                continue
            columns, rows = run_sql(con, question["sql"])

            class Result:
                refused = False
                sql_error = None
                infrastructure_error = None
                sql = question["sql"]

            result = Result()
            result.rows = rows
            result.columns = columns
            comparison = compare_result(question, result, resolver)
            assert comparison.passed, f"{question['id']} {comparison.detail}"
    finally:
        con.close()
