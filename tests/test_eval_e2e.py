import json
from pathlib import Path

from flight_analyst.bank import REVIEW_NOTE, _attach_aliases, run_sql
from flight_analyst.evaluate import run_evaluation
from flight_analyst.ingest import create_database, load_lookups
from flight_analyst.llm import StubClient
from flight_analyst.questions import _q, _rank, _vals
from flight_analyst.scoreboard import build_scoreboard


def _fixture(path: Path) -> None:
    con = create_database(path)
    con.execute(
        """
        INSERT INTO flights (
            flight_date, year, month, day_of_month, day_of_week, airline_code,
            origin, dest, arr_delay, cancelled, diverted, sched_dep_hour, season
        )
        SELECT DATE '2025-10-01', 2025, 10, 1, 3, 'AA', 'DFW', 'ORD', 5,
               FALSE, FALSE, 8, 'fall'
        FROM range(120)
        """
    )
    con.execute(
        """
        INSERT INTO flights (
            flight_date, year, month, day_of_month, day_of_week, airline_code,
            origin, dest, arr_delay, cancelled, diverted, sched_dep_hour, season,
            carrier_delay, weather_delay, nas_delay, security_delay, late_aircraft_delay
        )
        SELECT DATE '2025-10-01', 2025, 10, 1, 3, 'WN', 'DFW', 'ORD', 40,
               FALSE, FALSE, 18, 'fall', 30, 0, 0, 0, 10
        FROM range(120)
        """
    )
    airports = path.parent / "airports.csv"
    airports.write_text(
        "iata_code,name,municipality,iso_region,iso_country,scheduled_service,type\n"
        "DFW,Dallas Fort Worth International Airport,Dallas,US-TX,US,yes,large_airport\n"
        "ORD,Chicago O'Hare International Airport,Chicago,US-IL,US,yes,large_airport\n"
    )
    load_lookups(con, airports)
    con.close()


def test_harness_scores_the_stub_and_writes_a_scoreboard(tmp_path: Path):
    db_path = tmp_path / "fixture.duckdb"
    _fixture(db_path)
    from flight_analyst.ingest import connect_readonly
    from flight_analyst.questions import build_questions

    con = connect_readonly(db_path)
    dfw = next(item for item in build_questions() if item["question"] == "Which airline is most on time out of DFW?")
    median = _q(
        "Which airline has the lowest median arrival delay for operated flights out of DFW?",
        "hard",
        "airline_reliability",
        _rank("airline_code", "airline_code", "median_arrival_delay", "MEDIAN(arr_delay)", "operated AND origin = 'DFW'", None, False, 1),
        "ranking",
        _vals("median_arrival_delay"),
        notes="Fixture question the rules stub does not implement.",
    )
    refuse = next(item for item in build_questions() if item["question"] == "What was the on-time rate yesterday?")
    questions = []
    for index, item in enumerate((dfw, median, refuse), start=1):
        question = dict(item)
        question["id"] = f"q{index:03d}"
        _attach_aliases(question)
        if question["answer_type"] == "refusal":
            question["expected"] = {"behavior": "refuse", "rows": []}
        else:
            _columns, rows = run_sql(con, question["sql"])
            question["expected"] = {"rows": rows}
        questions.append(question)
    con.close()
    bank = {
        "status": "DRAFT",
        "review_note": REVIEW_NOTE,
        "data_window": {"flight_date_min": "2025-10-01", "flight_date_max": "2025-10-01", "row_count": 240, "months": [{"year": 2025, "month": 10}]},
        "questions": questions,
    }
    bank_path = tmp_path / "bank.json"
    bank_path.write_text(json.dumps(bank))
    out = tmp_path / "reports"
    report = run_evaluation(db_path=db_path, bank_path=bank_path, client=StubClient(), out_dir=out)
    assert report["status"] == "DRAFT"
    assert report["review_note"] == REVIEW_NOTE
    assert report["provider"] == "stub"
    assert report["summary"]["n"] == 3
    assert report["summary"]["n_correct"] == 2
    failures = {row["id"]: row["failure_category"] for row in report["results"] if not row["passed"]}
    assert failures == {"q002": "misread_question"}
    assert (out / "latest.json").exists()
    assert (out / "latest.md").exists()
    html_path = build_scoreboard(out / "latest.json", tmp_path / "index.html")
    page = html_path.read_text()
    assert "DRAFT" in page
    assert REVIEW_NOTE in page
    assert "66.7%" in page
    assert "misread_question" in page
    assert "Analyst SQL" in page
    assert "Reference SQL" in page
    missed = next(row for row in report["results"] if row["id"] == "q002")
    assert missed["reference_sql"] in page
    assert missed["analyst_sql"] in page
