"""The checks an agent has to pass. A bad brand map or a lower score fails here."""

import json
from pathlib import Path

from flight_analyst.analyst import build_system_prompt
from flight_analyst.bank import load_bank
from flight_analyst.definitions import (
    LARGE_HUB_DEPARTURES,
    MIN_AIRLINE_AT_AIRPORT,
    MIN_AIRLINE_SYSTEMWIDE,
    sample_floor,
)
from flight_analyst.guardrails import (
    accuracy_problems,
    baseline_moved_down,
    brand_mapping_problems,
    check_brands,
    gitignore_problems,
    grade_stub,
    load_baseline,
    marker_present,
    ranking_floor_problems,
    review_marker_problems,
    route_shape_problems,
    tracked_file_problems,
    why_limit_problems,
    worst_airport_problems,
)
from flight_analyst.ingest import create_database, load_lookups
from flight_analyst.mcp_server import _BRAND, carrier_ranking
from flight_analyst.paths import BANK_PATH, ROOT
from flight_analyst.questions import build_questions
from flight_analyst.schema import CARRIER_FAMILIES, CARRIER_NAMES

STUB_FLOOR = 116
STUB_N = 120
STUB_MISSES = ("q020", "q037", "q079", "q101")


def _questions():
    return build_questions()


def test_stub_regrade_stays_at_or_above_the_baseline():
    baseline = load_baseline()
    assert baseline["min_correct"] == STUB_FLOOR
    assert baseline["n"] == STUB_N
    assert LARGE_HUB_DEPARTURES == 150_000
    bank = load_bank(BANK_PATH)
    document = json.loads((ROOT / "results" / "stub.json").read_text())
    n_correct, n, misses = grade_stub(bank, document)
    assert accuracy_problems(n_correct, n, baseline) == []
    assert tuple(misses) == STUB_MISSES


def test_a_changed_answer_key_row_drops_the_stub_below_the_baseline():
    bank = load_bank(BANK_PATH)
    document = json.loads((ROOT / "results" / "stub.json").read_text())
    target = next(item for item in bank["questions"] if item["id"] == "q001")
    target["expected"]["rows"] = [{"label": "ZZ", "on_time_rate": 0.01, "operated_flights": 1}]
    n_correct, n, misses = grade_stub(bank, document)
    assert n == STUB_N
    assert n_correct < STUB_FLOOR
    assert "q001" in misses
    assert accuracy_problems(n_correct, n, load_baseline())


def test_brand_map_matches_the_answering_rules():
    assert brand_mapping_problems() == []
    assert _BRAND["MQ"] == ("American Eagle", "American", "single_partner")
    assert _BRAND["OH"][0] == "American Eagle"
    assert _BRAND["PT"][0] == "American Eagle"
    assert _BRAND["YX"] == ("American Eagle", "several airlines", "unassigned")
    assert _BRAND["OO"] == ("SkyWest (flies for several airlines)", "several airlines", "unassigned")
    family_codes = {code for code, _family, _name, _role in CARRIER_FAMILIES}
    assert "OO" not in family_codes
    assert "YX" not in family_codes
    assert "YV" not in family_codes
    assert CARRIER_NAMES["MQ"] == "Envoy Air"
    assert CARRIER_NAMES["YX"] == "Republic Airways"


def test_a_broken_brand_mapping_fails():
    broken = dict(_BRAND)
    broken["MQ"] = ("Envoy", "American", "single_partner")
    broken["OO"] = ("American Eagle", "American", "single_partner")
    families = {code: family for code, family, _name, _role in CARRIER_FAMILIES}
    families["OO"] = "AA"
    problems = check_brands(broken, CARRIER_NAMES, families, "SELECT 1")
    text = " ".join(problems)
    assert "MQ" in text
    assert "OO" in text
    assert "Envoy (American Eagle)" in text


def test_open_rankings_keep_the_sample_floor():
    assert ranking_floor_problems(_questions()) == []


def test_stripping_a_systemwide_floor_fails():
    bare = {
        "question": "Which five airlines have the highest systemwide on-time arrival rate?",
        "answer_type": "ranking",
        "sql": "SELECT airline_code AS label, COUNT(*) AS n FROM flights_v WHERE operated GROUP BY airline_code",
    }
    problems = ranking_floor_problems([bare])
    assert problems
    assert "5000" in problems[0]


def test_sample_floors_stay_at_the_documented_cuts():
    from flight_analyst.definitions import (
        MIN_AIRPORT,
        MIN_AIRPORT_BUCKET,
        MIN_BUCKET_EACH,
        MIN_CANCELLATIONS,
        MIN_HOUR_SYSTEM,
        MIN_LATE_FLIGHTS,
        MIN_ROUTE,
        MIN_ROUTE_FROM_AIRPORT,
    )

    assert MIN_AIRLINE_AT_AIRPORT == 100
    assert MIN_AIRLINE_SYSTEMWIDE == 5000
    assert MIN_AIRPORT == 5000
    assert MIN_AIRPORT_BUCKET == 2000
    assert MIN_ROUTE == 500
    assert MIN_ROUTE_FROM_AIRPORT == 200
    assert MIN_LATE_FLIGHTS == 1000
    assert MIN_CANCELLATIONS == 200
    assert MIN_HOUR_SYSTEM == 10000
    assert MIN_BUCKET_EACH == 500


def test_sample_floor_can_only_rise():
    assert sample_floor("DFW", 1) == MIN_AIRLINE_AT_AIRPORT
    assert sample_floor("systemwide", 10) == MIN_AIRLINE_SYSTEMWIDE
    assert sample_floor("DFW", 150) == 150
    try:
        sample_floor("DFW", 0)
    except ValueError:
        return
    raise AssertionError("a floor of 0 should be rejected")


def test_carrier_ranking_ignores_a_request_to_lower_the_floor(tmp_path: Path):
    db_path = tmp_path / "floor.duckdb"
    con = create_database(db_path)
    con.execute(
        """
        INSERT INTO flights (
            flight_date, year, month, day_of_month, day_of_week, airline_code,
            origin, dest, arr_delay, cancelled, diverted
        )
        SELECT DATE '2025-10-01', 2025, 10, 1, 3, 'MQ', 'DFW', 'ORD', 5, FALSE, FALSE
        FROM range(100)
        """
    )
    con.execute(
        """
        INSERT INTO flights (
            flight_date, year, month, day_of_month, day_of_week, airline_code,
            origin, dest, arr_delay, cancelled, diverted
        )
        SELECT DATE '2025-10-01', 2025, 10, 1, 3, 'AA', 'DFW', 'ORD', 5, FALSE, FALSE
        FROM range(3)
        """
    )
    load_lookups(con, None)
    con.close()
    lowered = carrier_ranking("on_time_rate", "DFW", min_flights=1, db_path=db_path)
    assert lowered["ok"] is True
    assert lowered["min_operated_flights"] == 100
    assert [row["airline_code"] for row in lowered["rows"]] == ["MQ"]
    systemwide = carrier_ranking("on_time_rate", "systemwide", min_flights=1, db_path=db_path)
    assert systemwide["min_operated_flights"] == 5000
    assert systemwide["rows"] == []


def test_routes_keep_both_directions_and_the_worst_hub_stays():
    questions = _questions()
    assert route_shape_problems(questions) == []
    assert worst_airport_problems(questions) == []
    broken_route = {
        "question": "Are flights between DFW and ORD on time? Return the on-time arrival rate.",
        "sql": "SELECT 'DFW-ORD' AS label",
    }
    assert route_shape_problems([broken_route])
    broken_airport = {"question": "Which airport is the worst?", "sql": "SELECT 'overall_worst' AS label"}
    assert any("major hub" in problem for problem in worst_airport_problems([broken_airport]))


def test_delay_codes_still_admit_they_cannot_say_why():
    assert why_limit_problems(_questions(), build_system_prompt("the loaded window")) == []
    stripped = [dict(item) for item in _questions()]
    for item in stripped:
        if "Does weather explain most delays?" in item["question"]:
            item["why_limit"] = ""
    problems = why_limit_problems(stripped, build_system_prompt("the loaded window"))
    assert problems
    assert any("weather" in problem.lower() for problem in problems)


def test_answer_key_and_baseline_need_an_explicit_marker():
    assert marker_present("+BANK-EDIT: Envoy label corrected after review.\n", "BANK-EDIT")
    assert not marker_present(" BANK-EDIT: already in the file\n+The token is BANK-EDIT: in a sentence.\n", "BANK-EDIT")
    quiet = review_marker_problems(
        key_paths_changed=["questions/bank.json"],
        baseline_lowered=False,
        edits_diff="",
    )
    assert quiet and "BANK-EDIT" in quiet[0]
    marked = review_marker_problems(
        key_paths_changed=["src/flight_analyst/questions.py"],
        baseline_lowered=False,
        edits_diff="+++ docs/bank-edits.md\n+BANK-EDIT: reference SQL for q096 gained an ORDER BY. Rows unchanged.\n",
    )
    assert marked == []
    lowered = review_marker_problems(
        key_paths_changed=[],
        baseline_lowered=True,
        edits_diff="+The floor is lower now.\n",
    )
    assert lowered and "BASELINE-EDIT" in lowered[0]
    assert baseline_moved_down('{"min_correct": 116, "n": 120}\n', '{"min_correct": 100, "n": 120}\n')
    assert not baseline_moved_down(None, '{"min_correct": 116, "n": 120}\n')
    assert not baseline_moved_down(
        '{"min_correct": 116, "n": 120}\n',
        '{"min_correct": 117, "n": 120}\n',
    )


def test_data_files_stay_out_of_git():
    ignore = (ROOT / ".gitignore").read_text()
    assert gitignore_problems(ignore) == []
    assert gitignore_problems("*.py\n") 
    problems = tracked_file_problems(
        [
            ("data/flights.duckdb", 100),
            ("extract.parquet", 100),
            ("notes.csv", 10),
            ("other.duckdb", 10),
            ("tests/fixtures/sample_flights.duckdb", 4_200_000),
            ("huge.bin", 5_000_001),
        ]
    )
    text = " ".join(problems)
    assert "data/flights.duckdb" in text
    assert "extract.parquet" in text
    assert "notes.csv" in text
    assert "other.duckdb" in text
    assert "huge.bin" in text
    assert not any(problem.startswith("tests/fixtures/sample_flights.duckdb") for problem in problems)


def test_agents_doc_and_readme_and_workflow_are_present():
    agents = (ROOT / "AGENTS.md").read_text()
    readme = (ROOT / "README.md").read_text()
    workflow = (ROOT / ".github" / "workflows" / "guardrails.yml").read_text()
    pages = (ROOT / ".github" / "workflows" / "pages.yml").read_text()
    assert "docs/definitions.md" in agents
    assert "BANK-EDIT" in agents
    assert "questions/bank.json" in agents
    assert "## How agents work in this repo" in readme
    assert "pull_request:" in workflow
    assert "check_guardrails.py" in workflow
    assert 'ANTHROPIC_API_KEY: ""' in workflow
    assert "deploy-pages" not in workflow
    assert "upload-pages-artifact" not in workflow
    assert "upload-pages-artifact" in pages
