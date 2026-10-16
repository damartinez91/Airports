import asyncio
import json
from pathlib import Path

from flight_analyst.ingest import create_database, load_lookups
from flight_analyst.live import SpendLedger
from flight_analyst.llm import StubClient
from flight_analyst.mcp_server import (
    MAX_ROWS,
    QueryTimeout,
    ask_analyst,
    build_server,
    carrier_ranking,
    list_carriers,
    off_topic_reason,
    run_read,
)


def _fixture(path: Path) -> None:
    con = create_database(path)
    con.execute(
        """
        INSERT INTO flights (
            flight_date, year, month, day_of_month, day_of_week, airline_code,
            origin, dest, arr_delay, cancelled, diverted
        )
        SELECT DATE '2025-10-01', 2025, 10, 1, 3, 'AA', 'DFW', 'ORD', 5, FALSE, FALSE
        FROM range(4)
        """
    )
    con.execute(
        """
        INSERT INTO flights (
            flight_date, year, month, day_of_month, day_of_week, airline_code,
            origin, dest, arr_delay, cancelled, diverted
        ) VALUES (DATE '2025-10-01', 2025, 10, 1, 3, 'AA', 'DFW', 'ORD', NULL, TRUE, FALSE)
        """
    )
    con.execute(
        """
        INSERT INTO flights (
            flight_date, year, month, day_of_month, day_of_week, airline_code,
            origin, dest, arr_delay, cancelled, diverted
        )
        SELECT DATE '2025-11-01', 2025, 11, 1, 6, 'AA', 'DFW', 'ORD', 5, FALSE, FALSE
        FROM range(2)
        """
    )
    con.execute(
        """
        INSERT INTO flights (
            flight_date, year, month, day_of_month, day_of_week, airline_code,
            origin, dest, arr_delay, cancelled, diverted
        )
        SELECT DATE '2025-10-01', 2025, 10, 1, 3, 'AA', 'ORD', 'DFW', 20, FALSE, FALSE
        FROM range(2)
        """
    )
    con.execute(
        """
        INSERT INTO flights (
            flight_date, year, month, day_of_month, day_of_week, airline_code,
            origin, dest, arr_delay, cancelled, diverted
        )
        SELECT DATE '2025-10-01', 2025, 10, 1, 3, 'DL', 'DFW', 'ORD', 0, FALSE, FALSE
        FROM range(2)
        """
    )
    con.execute(
        """
        INSERT INTO flights (
            flight_date, year, month, day_of_month, day_of_week, airline_code,
            origin, dest, arr_delay, cancelled, diverted
        ) VALUES (DATE '2025-10-01', 2025, 10, 1, 3, 'DL', 'ORD', 'DFW', 30, FALSE, FALSE)
        """
    )
    con.execute(
        """
        INSERT INTO flights (
            flight_date, year, month, day_of_month, day_of_week, airline_code,
            origin, dest, arr_delay, cancelled, diverted
        )
        SELECT DATE '2025-10-01', 2025, 10, 1, 3, 'MQ', 'DFW', 'LGA', 0, FALSE, FALSE
        FROM range(100)
        """
    )
    con.execute(
        """
        INSERT INTO flights (
            flight_date, year, month, day_of_month, day_of_week, airline_code,
            origin, dest, arr_delay, cancelled, diverted
        )
        SELECT DATE '2025-10-01', 2025, 10, 1, 3, 'OO', 'DFW', 'DEN', 0, FALSE, FALSE
        FROM range(10)
        """
    )
    con.execute(
        """
        INSERT INTO flights (
            flight_date, year, month, day_of_month, day_of_week, airline_code,
            origin, dest, arr_delay, cancelled, diverted
        ) VALUES (DATE '2025-10-01', 2025, 10, 1, 3, 'YX', 'DFW', 'ORD', 4, FALSE, FALSE)
        """
    )
    airports = path.parent / "airports.csv"
    airports.write_text(
        "iata_code,name,municipality,iso_region,iso_country,scheduled_service,type\n"
        "DFW,Dallas Fort Worth International Airport,Dallas,US-TX,US,yes,large_airport\n"
        "ORD,Chicago O'Hare International Airport,Chicago,US-IL,US,yes,large_airport\n"
        "LGA,LaGuardia Airport,New York,US-NY,US,yes,large_airport\n"
        "DEN,Denver International Airport,Denver,US-CO,US,yes,large_airport\n"
    )
    load_lookups(con, airports)
    con.close()


def _call(server, name: str, arguments: dict | None = None) -> dict:
    result = asyncio.run(server.call_tool(name, arguments or {}))
    assert result.is_error is False, result
    return json.loads(result.content[0].text)


def test_tools_are_read_only_and_there_is_no_sql_tool(tmp_path: Path):
    db_path = tmp_path / "fixture.duckdb"
    _fixture(db_path)
    server = build_server(db_path)
    tools = asyncio.run(server.list_tools())
    names = {tool.name for tool in tools}
    assert names == {"list_carriers", "airport_reliability", "route_compare", "carrier_ranking", "ask_analyst"}
    assert not any("sql" in name for name in names)
    by_name = {tool.name: tool for tool in tools}
    assert by_name["list_carriers"].annotations.read_only_hint is True
    assert by_name["ask_analyst"].annotations.open_world_hint is True
    resources = asyncio.run(server.list_resources())
    assert [str(item.uri) for item in resources] == ["flight://schema"]
    contents = list(asyncio.run(server.read_resource("flight://schema")))
    text = contents[0].content
    assert "flights_v" in text
    assert "`origin`" in text
    assert "carrier_families" in text


def test_list_carriers_labels_envoy_republic_and_skywest(tmp_path: Path):
    db_path = tmp_path / "fixture.duckdb"
    _fixture(db_path)
    payload = _call(build_server(db_path), "list_carriers")
    assert payload["ok"] is True
    by_code = {row["airline_code"]: row for row in payload["carriers"]}
    assert by_code["MQ"]["brand"] == "American Eagle"
    assert by_code["MQ"]["flies_for"] == "American"
    assert by_code["MQ"]["assignment"] == "single_partner"
    assert by_code["YX"]["brand"] == "American Eagle"
    assert by_code["YX"]["flies_for"] == "several airlines"
    assert by_code["YX"]["assignment"] == "unassigned"
    assert "unassigned" in by_code["YX"]["note"]
    assert by_code["OO"]["brand"] == "SkyWest (flies for several airlines)"
    assert by_code["OO"]["flights"] == 10


def test_airport_reliability_honors_the_date_range(tmp_path: Path):
    db_path = tmp_path / "fixture.duckdb"
    _fixture(db_path)
    server = build_server(db_path)
    october = _call(
        server,
        "airport_reliability",
        {"airport": "dfw", "start_date": "2025-10-01", "end_date": "2025-10-31"},
    )
    # October DFW departures: AA 5 (1 cancelled), DL 2, MQ 100, OO 10, YX 1.
    assert october["flights"] == 118
    assert october["operated_flights"] == 117
    assert october["cancel_pct"] == 0.85
    assert october["on_time_pct"] == 100.0
    whole = _call(server, "airport_reliability", {"airport": "DFW"})
    assert whole["flights"] == 120
    assert _call(server, "airport_reliability", {"airport": "Dallas"})["ok"] is False


def test_route_compare_returns_both_directions_and_the_combined_row(tmp_path: Path):
    db_path = tmp_path / "fixture.duckdb"
    _fixture(db_path)
    payload = _call(
        build_server(db_path),
        "route_compare",
        {"origin": "DFW", "dest": "ORD", "carriers": ["AA", "DL"]},
    )
    assert payload["ok"] is True
    rows = {(row["carrier"], row["label"]): row for row in payload["rows"]}
    aa_out = rows[("AA", "DFW-ORD")]
    assert aa_out["flights"] == 7
    assert aa_out["on_time_pct"] == 100.0
    assert aa_out["cancel_pct"] == 14.29
    aa_back = rows[("AA", "ORD-DFW")]
    assert aa_back["flights"] == 2
    assert aa_back["on_time_pct"] == 0.0
    aa_both = rows[("AA", "both-directions")]
    assert aa_both["flights"] == 9
    assert aa_both["on_time_pct"] == 75.0
    dl_both = rows[("DL", "both-directions")]
    assert dl_both["flights"] == 3
    assert dl_both["on_time_pct"] == 66.67
    assert rows[("all", "both-directions")]["flights"] == 12
    rejected = _call(
        build_server(db_path),
        "route_compare",
        {"origin": "DFW", "dest": "ORD", "carriers": ["AA'; DROP TABLE flights;--"]},
    )
    assert rejected["ok"] is False


def test_carrier_ranking_keeps_the_sample_floor(tmp_path: Path):
    db_path = tmp_path / "fixture.duckdb"
    _fixture(db_path)
    server = build_server(db_path)
    ranked = _call(server, "carrier_ranking", {"metric": "on_time_rate", "scope": "DFW"})
    assert ranked["min_operated_flights"] == 100
    assert [row["airline_code"] for row in ranked["rows"]] == ["MQ"]
    assert ranked["rows"][0]["value"] == 100.0
    raised = _call(
        server,
        "carrier_ranking",
        {"metric": "on_time_rate", "scope": "DFW", "min_flights": 150},
    )
    assert raised["min_operated_flights"] == 150
    assert raised["rows"] == []
    systemwide = _call(server, "carrier_ranking", {"metric": "on_time_rate", "scope": "systemwide"})
    assert systemwide["min_operated_flights"] == 5000
    assert systemwide["rows"] == []
    assert _call(server, "carrier_ranking", {"metric": "median", "scope": "DFW"})["ok"] is False


def test_row_limit_and_query_timeout(tmp_path: Path):
    db_path = tmp_path / "fixture.duckdb"
    _fixture(db_path)
    capped = run_read(db_path, "SELECT i FROM range(1000) t(i)", [], max_rows=MAX_ROWS)
    assert len(capped["rows"]) == MAX_ROWS
    assert capped["truncated"] is True
    try:
        run_read(db_path, "SELECT SUM(i) FROM range(5000000000) t(i)", [], timeout_seconds=0.2)
    except QueryTimeout:
        return
    raise AssertionError("the long query should have been interrupted")


def test_ask_analyst_is_disabled_without_a_key_and_refuses_off_topic(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "fixture.duckdb"
    _fixture(db_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    disabled = _call(build_server(db_path), "ask_analyst", {"question": "Which airline is most on time out of DFW?"})
    assert disabled["enabled"] is False
    assert "ANTHROPIC_API_KEY" in disabled["error"]
    assert off_topic_reason("What is the capital of France?")
    client = StubClient()
    refused = ask_analyst("What is the capital of France?", db_path=db_path, client=client)
    assert refused["refused"] is True
    assert client.__dict__.get("calls", 0) == 0
    ledger = SpendLedger(tmp_path / "spend.json", cap_cents=0)
    blocked = ask_analyst(
        "Which airline is most on time out of DFW?",
        db_path=db_path,
        client=client,
        ledger=ledger,
    )
    assert "Spend cap" in blocked["error"]
    answered = ask_analyst(
        "Which airline is most on time out of DFW?",
        db_path=db_path,
        client=StubClient(),
        ledger=SpendLedger(tmp_path / "open.json", cap_cents=100),
    )
    assert answered["enabled"] is True
    assert answered["model_calls"] == 1
    assert answered["sql_attempts"] == 1
    assert "MQ" in answered["answer"] or "Envoy" in answered["answer"] or answered["ok"] is True


def test_direct_helpers_match_the_brand_list(tmp_path: Path):
    db_path = tmp_path / "fixture.duckdb"
    _fixture(db_path)
    payload = list_carriers(db_path)
    assert any(row["airline_code"] == "MQ" and row["brand"] == "American Eagle" for row in payload["carriers"])
    ranked = carrier_ranking("cancellation_rate", "systemwide", db_path=db_path)
    assert ranked["rows"] == []
