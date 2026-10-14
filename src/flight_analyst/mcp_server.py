"""Read-only MCP server for the flight-delay database.

Stdio transport for Claude Desktop, Cursor, and other MCP clients.
Tools run parameterized DuckDB queries on a read-only connection.
There is no tool that accepts arbitrary SQL.

    flight-analyst-mcp
    python -m flight_analyst.mcp_server
"""

from __future__ import annotations

import os
import re
import threading
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from flight_analyst.definitions import MIN_AIRLINE_AT_AIRPORT, MIN_AIRLINE_SYSTEMWIDE
from flight_analyst.ingest import connect_readonly
from flight_analyst.live import SpendLedger
from flight_analyst.paths import DATA_DIR, DB_PATH
from flight_analyst.schema import CARRIER_NAMES, render_schema_md

MAX_ROWS = 50
QUERY_TIMEOUT_SECONDS = 5.0
MAX_CARRIERS = 12
MAX_MODEL_CALLS = 1
MAX_SQL_ATTEMPTS = 1
DEFAULT_SPEND_CAP_USD = "5"
DEFAULT_USD_PER_CALL = "0.05"

_AIRPORT = re.compile(r"^[A-Z]{3}$")
_CARRIER = re.compile(r"^[A-Z0-9]{2,3}$")
_TOPIC = re.compile(
    r"\b("
    r"flight|flights|airline|airlines|airport|airports|delay|delays|on-time|ontime|"
    r"cancel|cancellation|cancellations|divert|diverted|route|departure|arrival|"
    r"carrier|bts|american|delta|united|southwest|skywest|envoy|republic|jetblue|"
    r"alaska|frontier|spirit|allegiant|hawaiian|piedmont|psa|horizon|endeavor"
    r")\b",
    re.IGNORECASE,
)

# Display labels for the carrier list. Republic flies American Eagle among
# other brands; this extract cannot assign those flights, so the row says so.
_BRAND = {
    "AA": ("American", "American", "mainline"),
    "MQ": ("American Eagle", "American", "single_partner"),
    "OH": ("American Eagle", "American", "single_partner"),
    "PT": ("American Eagle", "American", "single_partner"),
    "YX": ("American Eagle", "several airlines", "unassigned"),
    "DL": ("Delta", "Delta", "mainline"),
    "9E": ("Delta Connection", "Delta", "single_partner"),
    "UA": ("United", "United", "mainline"),
    "C5": ("United Express", "United", "single_partner"),
    "G7": ("United Express", "United", "single_partner"),
    "ZW": ("United Express", "United", "single_partner"),
    "AS": ("Alaska", "Alaska", "mainline"),
    "QX": ("Horizon", "Alaska", "single_partner"),
    "OO": ("SkyWest (flies for several airlines)", "several airlines", "unassigned"),
    "YV": ("Mesa (flies for several airlines)", "several airlines", "unassigned"),
    "WN": ("Southwest", "Southwest", "mainline"),
    "B6": ("JetBlue", "JetBlue", "mainline"),
    "NK": ("Spirit", "Spirit", "mainline"),
    "F9": ("Frontier", "Frontier", "mainline"),
    "G4": ("Allegiant", "Allegiant", "mainline"),
    "HA": ("Hawaiian", "Hawaiian", "mainline"),
}

_METRIC = {
    "on_time_rate": "AVG(CASE WHEN operated THEN CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END END)",
    "cancellation_rate": "AVG(CASE WHEN cancelled THEN 1.0 ELSE 0.0 END)",
    "avg_arrival_delay": "AVG(arr_delay) FILTER (WHERE operated)",
}
_METRIC_ORDER = {
    "on_time_rate": "DESC",
    "cancellation_rate": "ASC",
    "avg_arrival_delay": "ASC",
}

_EXAMPLES = [
    "Which airline is most on time out of DFW?",
    "What is the on-time rate at ORD?",
    "How do Delta and Southwest compare on ATL to LGA?",
]

_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
_ASK = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)


class QueryTimeout(TimeoutError):
    pass


def _cents(raw: str) -> int:
    return int((Decimal(raw) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _pct(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(100.0 * numerator / denominator, 2)


def _airport(value: str) -> str:
    code = (value or "").strip().upper()
    if not _AIRPORT.fullmatch(code):
        raise ValueError("Airport must be a 3-letter IATA code.")
    return code


def _carrier(value: str) -> str:
    code = (value or "").strip().upper()
    if not _CARRIER.fullmatch(code):
        raise ValueError("Carrier must be a 2- or 3-character reporting code.")
    return code


def _parse_date(value: str | None) -> date | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ValueError("Dates must be YYYY-MM-DD.") from exc


def _jsonify(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def run_read(
    db_path: Path,
    sql: str,
    params: list,
    *,
    timeout_seconds: float = QUERY_TIMEOUT_SECONDS,
    max_rows: int = MAX_ROWS,
) -> dict:
    """Run one parameterized SELECT. Caps rows and interrupts on timeout."""
    limit = min(max(int(max_rows), 1), MAX_ROWS)
    con = connect_readonly(db_path)
    box: dict = {}

    def work() -> None:
        try:
            cursor = con.execute(sql, params)
            columns = [item[0] for item in cursor.description]
            fetched = cursor.fetchmany(limit + 1)
            box["columns"] = columns
            box["rows"] = fetched
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(timeout_seconds)
    if thread.is_alive():
        try:
            con.interrupt()
        finally:
            thread.join(2)
            con.close()
        raise QueryTimeout(f"Query exceeded {timeout_seconds:g} seconds and was interrupted.")
    con.close()
    if "error" in box:
        raise box["error"]
    rows = box["rows"]
    truncated = len(rows) > limit
    records = [
        {column: _jsonify(value) for column, value in zip(box["columns"], record)}
        for record in rows[:limit]
    ]
    return {"rows": records, "truncated": truncated}


def _fail(message: str) -> dict:
    return {"ok": False, "error": message}


def _date_clause(start: date | None, end: date | None) -> tuple[str, list]:
    clauses = []
    params: list = []
    if start is not None:
        clauses.append("flight_date >= ?")
        params.append(start)
    if end is not None:
        clauses.append("flight_date <= ?")
        params.append(end)
    if not clauses:
        return "", []
    return " AND " + " AND ".join(clauses), params


def list_carriers(db_path: Path | None = None) -> dict:
    """Reporting carriers in the extract, with the brand label for each code."""
    sql = """
        SELECT c.airline_code,
               c.airline_name,
               COUNT(v.flight_date) AS flights
        FROM carriers c
        LEFT JOIN flights_v v ON v.airline_code = c.airline_code
        GROUP BY c.airline_code, c.airline_name
        ORDER BY c.airline_code
        LIMIT ?
    """
    try:
        result = run_read(db_path or DB_PATH, sql, [MAX_ROWS])
    except FileNotFoundError as exc:
        return _fail(str(exc))
    except QueryTimeout as exc:
        return _fail(str(exc))
    rows = []
    for row in result["rows"]:
        code = row["airline_code"]
        brand, flies_for, assignment = _BRAND.get(code, (CARRIER_NAMES.get(code, code), code, "reporting_carrier"))
        note = None
        if code == "YX":
            note = (
                "Republic flies American Eagle, Delta Connection, and United Express. "
                "This extract has no marketing-airline column, so those flights stay unassigned."
            )
        elif code == "OO":
            note = "SkyWest flies for several airlines. Those flights stay unassigned."
        elif assignment == "single_partner" and brand == "American Eagle":
            note = f"{row['airline_name']} is American Eagle and flies for American."
        rows.append(
            {
                "airline_code": code,
                "airline_name": row["airline_name"],
                "brand": brand,
                "flies_for": flies_for,
                "assignment": assignment,
                "flights": int(row["flights"] or 0),
                "note": note,
            }
        )
    return {"ok": True, "carriers": rows, "truncated": result["truncated"]}


def airport_reliability(
    airport: str,
    start_date: str | None = None,
    end_date: str | None = None,
    db_path: Path | None = None,
) -> dict:
    """On-time percent, cancellation percent, and flight count for departures."""
    try:
        code = _airport(airport)
        start = _parse_date(start_date)
        end = _parse_date(end_date)
    except ValueError as exc:
        return _fail(str(exc))
    if start and end and start > end:
        return _fail("start_date must be on or before end_date.")
    date_sql, date_params = _date_clause(start, end)
    sql = f"""
        SELECT COUNT(*) AS flights,
               SUM(CASE WHEN operated THEN 1 ELSE 0 END) AS operated_flights,
               SUM(CASE WHEN operated AND on_time_arrival THEN 1 ELSE 0 END) AS on_time_flights,
               SUM(CASE WHEN cancelled THEN 1 ELSE 0 END) AS cancelled_flights
        FROM flights_v
        WHERE origin = ?{date_sql}
    """
    try:
        result = run_read(db_path or DB_PATH, sql, [code, *date_params], max_rows=1)
    except FileNotFoundError as exc:
        return _fail(str(exc))
    except QueryTimeout as exc:
        return _fail(str(exc))
    row = result["rows"][0] if result["rows"] else {}
    flights = int(row.get("flights") or 0)
    operated = int(row.get("operated_flights") or 0)
    return {
        "ok": True,
        "airport": code,
        "start_date": start.isoformat() if start else None,
        "end_date": end.isoformat() if end else None,
        "on_time_pct": _pct(int(row.get("on_time_flights") or 0), operated),
        "cancel_pct": _pct(int(row.get("cancelled_flights") or 0), flights),
        "flights": flights,
        "operated_flights": operated,
    }


def _accumulate(bucket: dict, row: dict) -> None:
    bucket["flights"] += int(row["flights"] or 0)
    bucket["operated"] += int(row["operated_flights"] or 0)
    bucket["on_time"] += int(row["on_time_flights"] or 0)
    bucket["cancelled"] += int(row["cancelled_flights"] or 0)
    delay = row.get("delay_sum")
    bucket["delay_sum"] += 0.0 if delay is None else float(delay)


def _blank() -> dict:
    return {"flights": 0, "operated": 0, "on_time": 0, "cancelled": 0, "delay_sum": 0.0}


def _public_row(label: str, carrier: str, bucket: dict) -> dict:
    operated = bucket["operated"]
    flights = bucket["flights"]
    delay = None
    if operated:
        delay = round(bucket["delay_sum"] / operated, 2)
    return {
        "label": label,
        "carrier": carrier,
        "on_time_pct": _pct(bucket["on_time"], operated),
        "cancel_pct": _pct(bucket["cancelled"], flights),
        "flights": flights,
        "operated_flights": operated,
        "avg_arrival_delay": delay,
    }


def route_compare(
    origin: str,
    dest: str,
    carriers: list[str],
    start_date: str | None = None,
    end_date: str | None = None,
    db_path: Path | None = None,
) -> dict:
    """Each named carrier, both directions, plus the two directions combined."""
    try:
        origin_code = _airport(origin)
        dest_code = _airport(dest)
        codes = [_carrier(code) for code in carriers]
        start = _parse_date(start_date)
        end = _parse_date(end_date)
    except ValueError as exc:
        return _fail(str(exc))
    if origin_code == dest_code:
        return _fail("Origin and destination must differ.")
    if not codes:
        return _fail("Pass at least one carrier code.")
    if len(codes) > MAX_CARRIERS:
        return _fail(f"At most {MAX_CARRIERS} carriers.")
    if start and end and start > end:
        return _fail("start_date must be on or before end_date.")
    date_sql, date_params = _date_clause(start, end)
    sql = f"""
        SELECT airline_code,
               CASE WHEN origin = ? AND dest = ? THEN 'out' ELSE 'back' END AS direction,
               COUNT(*) AS flights,
               SUM(CASE WHEN operated THEN 1 ELSE 0 END) AS operated_flights,
               SUM(CASE WHEN operated AND on_time_arrival THEN 1 ELSE 0 END) AS on_time_flights,
               SUM(CASE WHEN cancelled THEN 1 ELSE 0 END) AS cancelled_flights,
               SUM(arr_delay) FILTER (WHERE operated) AS delay_sum
        FROM flights_v
        WHERE airline_code IN (SELECT UNNEST(?))
          AND ((origin = ? AND dest = ?) OR (origin = ? AND dest = ?)){date_sql}
        GROUP BY 1, 2
        LIMIT ?
    """
    params = [
        origin_code,
        dest_code,
        codes,
        origin_code,
        dest_code,
        dest_code,
        origin_code,
        *date_params,
        MAX_ROWS,
    ]
    try:
        result = run_read(db_path or DB_PATH, sql, params)
    except FileNotFoundError as exc:
        return _fail(str(exc))
    except QueryTimeout as exc:
        return _fail(str(exc))
    found: dict[tuple[str, str], dict] = {}
    for row in result["rows"]:
        found[(row["airline_code"], row["direction"])] = row
    outbound = f"{origin_code}-{dest_code}"
    inbound = f"{dest_code}-{origin_code}"
    rows = []
    everyone = _blank()
    for code in codes:
        out_bucket = _blank()
        back_bucket = _blank()
        if (code, "out") in found:
            _accumulate(out_bucket, found[(code, "out")])
        if (code, "back") in found:
            _accumulate(back_bucket, found[(code, "back")])
        both = _blank()
        for piece in (out_bucket, back_bucket):
            both["flights"] += piece["flights"]
            both["operated"] += piece["operated"]
            both["on_time"] += piece["on_time"]
            both["cancelled"] += piece["cancelled"]
            both["delay_sum"] += piece["delay_sum"]
        everyone["flights"] += both["flights"]
        everyone["operated"] += both["operated"]
        everyone["on_time"] += both["on_time"]
        everyone["cancelled"] += both["cancelled"]
        everyone["delay_sum"] += both["delay_sum"]
        rows.append(_public_row(outbound, code, out_bucket))
        rows.append(_public_row(inbound, code, back_bucket))
        rows.append(_public_row("both-directions", code, both))
    if len(codes) > 1:
        rows.append(_public_row("both-directions", "all", everyone))
    return {"ok": True, "origin": origin_code, "dest": dest_code, "rows": rows}


def carrier_ranking(
    metric: str,
    scope: str,
    min_flights: int | None = None,
    db_path: Path | None = None,
) -> dict:
    """Rank reporting carriers. The sample floor cannot be lowered."""
    name = (metric or "").strip()
    if name not in _METRIC:
        return _fail("metric must be on_time_rate, cancellation_rate, or avg_arrival_delay.")
    raw_scope = (scope or "").strip()
    try:
        if raw_scope.lower() == "systemwide":
            where = "TRUE"
            params: list = []
            floor = MIN_AIRLINE_SYSTEMWIDE
            scope_label = "systemwide"
        else:
            airport = _airport(raw_scope)
            where = "origin = ?"
            params = [airport]
            floor = MIN_AIRLINE_AT_AIRPORT
            scope_label = airport
        if min_flights is not None:
            requested = int(min_flights)
            if requested < 1:
                raise ValueError("min_flights must be at least 1.")
            floor = max(floor, requested)
    except ValueError as exc:
        return _fail(str(exc))
    direction = _METRIC_ORDER[name]
    expression = _METRIC[name]
    sql = f"""
        SELECT airline_code AS label,
               {expression} AS metric,
               COUNT(*) AS flights,
               SUM(CASE WHEN operated THEN 1 ELSE 0 END) AS operated_flights
        FROM flights_v
        WHERE {where}
        GROUP BY airline_code
        HAVING SUM(CASE WHEN operated THEN 1 ELSE 0 END) >= ?
        ORDER BY metric {direction} NULLS LAST, label ASC
        LIMIT ?
    """
    try:
        result = run_read(db_path or DB_PATH, sql, [*params, floor, MAX_ROWS])
    except FileNotFoundError as exc:
        return _fail(str(exc))
    except QueryTimeout as exc:
        return _fail(str(exc))
    rows = []
    for row in result["rows"]:
        value = row["metric"]
        if name in {"on_time_rate", "cancellation_rate"} and value is not None:
            value = round(float(value) * 100, 2)
        elif value is not None:
            value = round(float(value), 2)
        rows.append(
            {
                "airline_code": row["label"],
                "metric": name,
                "value": value,
                "flights": int(row["flights"] or 0),
                "operated_flights": int(row["operated_flights"] or 0),
            }
        )
    return {
        "ok": True,
        "metric": name,
        "scope": scope_label,
        "min_operated_flights": floor,
        "rows": rows,
        "truncated": result["truncated"],
    }


def off_topic_reason(question: str) -> str | None:
    """Local check before any model call. None means the question can proceed."""
    text = (question or "").strip()
    if _TOPIC.search(text):
        return None
    if re.search(r"\b([A-Z]{3})\b", text) and re.search(r"\b([A-Z0-9]{2})\b", text or ""):
        return None
    examples = " ".join(_EXAMPLES)
    return (
        "That question is outside this US flight-delay database. "
        f"Ask about an airline, an airport, or a route. For example: {examples}"
    )


def ask_analyst(
    question: str,
    *,
    db_path: Path | None = None,
    client=None,
    ledger: SpendLedger | None = None,
) -> dict:
    """One plain-English question. Disabled when ANTHROPIC_API_KEY is absent."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if client is None and not key:
        return {
            "ok": False,
            "enabled": False,
            "error": "ask_analyst is disabled because ANTHROPIC_API_KEY is not set. No model request was sent.",
        }
    reason = off_topic_reason(question)
    if reason:
        return {"ok": False, "enabled": True, "refused": True, "error": reason, "examples": _EXAMPLES}
    cap = _cents(os.environ.get("MCP_SPEND_CAP_USD", DEFAULT_SPEND_CAP_USD))
    each = _cents(os.environ.get("MCP_USD_PER_CALL", DEFAULT_USD_PER_CALL))
    if ledger is None:
        ledger = SpendLedger(DATA_DIR / "mcp_spend.json", cap)
    if not ledger.charge(each):
        return {
            "ok": False,
            "enabled": True,
            "refused": True,
            "error": "Spend cap reached. No model request was sent.",
            "spend": ledger.view(),
        }
    if client is None:
        from flight_analyst.llm import AnthropicClient

        model = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5")
        client = AnthropicClient(key, model)
    from flight_analyst.analyst import Analyst

    outcome = Analyst(db_path or DB_PATH, client=client).answer(question)
    failed = bool(outcome.sql_error or outcome.infrastructure_error)
    return {
        "ok": not failed and not outcome.refused,
        "enabled": True,
        "refused": outcome.refused,
        "answer": outcome.answer_text,
        "sql": outcome.sql,
        "sql_error": outcome.sql_error,
        "model_calls": MAX_MODEL_CALLS,
        "sql_attempts": 0 if outcome.refused else MAX_SQL_ATTEMPTS,
        "input_tokens": outcome.input_tokens,
        "output_tokens": outcome.output_tokens,
        "spend": ledger.view(),
    }


def build_server(db_path: Path | None = None) -> MCPServer:
    """MCP server bound to one database. Tools do not accept raw SQL."""
    path = Path(db_path) if db_path is not None else DB_PATH
    server = MCPServer("flight-analyst")

    @server.tool(annotations=_READ_ONLY)
    def list_carriers() -> dict:
        """List reporting carriers in the database and the brand each code flies for.

        Envoy is American Eagle. Republic is labeled American Eagle and also
        marked unassigned, because it flies for several airlines. SkyWest is
        labeled as flying for several airlines.
        """
        return list_carriers_query(path)

    @server.tool(annotations=_READ_ONLY)
    def airport_reliability(
        airport: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        """On-time percent, cancellation percent, and departure count for one airport.

        Dates are optional YYYY-MM-DD bounds on the scheduled flight date.
        On-time percent uses operated flights. Cancellation percent uses every scheduled flight.
        """
        return airport_reliability_query(path, airport, start_date, end_date)

    @server.tool(annotations=_READ_ONLY)
    def route_compare(
        origin: str,
        dest: str,
        carriers: list[str],
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        """Compare carriers on a route: each direction, then both directions combined."""
        return route_compare_query(path, origin, dest, carriers, start_date, end_date)

    @server.tool(annotations=_READ_ONLY)
    def carrier_ranking(
        metric: str,
        scope: str,
        min_flights: int | None = None,
    ) -> dict:
        """Rank carriers by on_time_rate, cancellation_rate, or avg_arrival_delay.

        scope is systemwide or a 3-letter airport. A carrier under the sample
        floor is left out. min_flights can raise that floor and cannot lower it.
        """
        return carrier_ranking_query(path, metric, scope, min_flights)

    @server.tool(annotations=_ASK)
    def ask_analyst(question: str) -> dict:
        """Ask one plain-English question about US flights. Needs ANTHROPIC_API_KEY.

        The tool is disabled, and sends nothing, when that key is absent.
        Off-topic questions are refused before any model call. One model call
        and at most one SQL attempt. A spend cap can refuse the call.
        """
        return ask_analyst_query(path, question)

    @server.resource(
        "flight://schema",
        name="schema",
        title="Flight database schema",
        description="Tables and columns the tools query. There is no arbitrary SQL tool.",
        mime_type="text/markdown",
    )
    def schema() -> str:
        return render_schema_md()

    return server


def list_carriers_query(db_path: Path) -> dict:
    return list_carriers(db_path)


def airport_reliability_query(db_path: Path, airport: str, start_date: str | None, end_date: str | None) -> dict:
    return airport_reliability(airport, start_date, end_date, db_path)


def route_compare_query(
    db_path: Path,
    origin: str,
    dest: str,
    carriers: list[str],
    start_date: str | None,
    end_date: str | None,
) -> dict:
    return route_compare(origin, dest, carriers, start_date, end_date, db_path)


def carrier_ranking_query(db_path: Path, metric: str, scope: str, min_flights: int | None) -> dict:
    return carrier_ranking(metric, scope, min_flights, db_path)


def ask_analyst_query(db_path: Path, question: str) -> dict:
    return ask_analyst(question, db_path=db_path)


def main() -> None:
    """Serve the flight tools on stdin/stdout. Logs stay off stdout."""
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
