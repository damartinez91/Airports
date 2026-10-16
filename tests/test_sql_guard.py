from flight_analyst.sql_guard import SqlGuardError, prepare_sql
import pytest


def test_select_is_allowed():
    sql = prepare_sql("SELECT airline_code AS label FROM flights_v WHERE origin = 'DFW'")
    assert sql.lower().startswith("select")


def test_with_and_trailing_semicolon_are_allowed():
    sql = prepare_sql("WITH s AS (SELECT 1 AS n FROM flights_v) SELECT n FROM s;")
    assert ";" not in sql
    assert sql.lower().startswith("with")


def test_compare_values_join_is_allowed():
    sql = """SELECT codes.airline_code AS label, COUNT(f.flight_date) AS scheduled_flights
FROM (VALUES ('AA'), ('WN')) AS codes(airline_code)
LEFT JOIN flights_v f ON f.airline_code = codes.airline_code
GROUP BY codes.airline_code"""
    assert "flights_v" in prepare_sql(sql)


def test_delete_is_rejected():
    with pytest.raises(SqlGuardError):
        prepare_sql("DELETE FROM flights_v")


def test_second_statement_is_rejected():
    with pytest.raises(SqlGuardError):
        prepare_sql("SELECT 1 FROM flights_v; SELECT 2 FROM flights_v")


def test_read_csv_is_rejected():
    with pytest.raises(SqlGuardError):
        prepare_sql("SELECT * FROM read_csv('airports.csv')")


def test_disallowed_table_is_rejected():
    with pytest.raises(SqlGuardError):
        prepare_sql("SELECT * FROM secrets")
