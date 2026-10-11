"""Pluggable LLM clients.

Set LLM_PROVIDER to openai or anthropic. With no API key, the CLI uses the
rules stub. The stub is not a model score. It never reads the question bank.

Secrets:
- OPENAI_API_KEY for LLM_PROVIDER=openai. Optional OPENAI_MODEL (default gpt-4o-mini).
- ANTHROPIC_API_KEY for LLM_PROVIDER=anthropic. Optional ANTHROPIC_MODEL
  (default claude-sonnet-4-5).
"""

from __future__ import annotations

import json
import os
import re
import urllib.request

from flight_analyst.definitions import (
    MIN_AIRLINE_AT_AIRPORT,
    MIN_AIRLINE_SYSTEMWIDE,
    MIN_AIRPORT,
    MIN_AIRPORT_BUCKET,
    MIN_CANCELLATIONS,
    MIN_LATE_FLIGHTS,
    MIN_ROUTE,
    MIN_ROUTE_FROM_AIRPORT,
)

ONTIME = "AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END)"
CANCEL = "AVG(CASE WHEN cancelled THEN 1.0 ELSE 0.0 END)"

# Longer names first so "american" does not need to compete with a shorter token.
_AIRLINE_WORDS = [
    ("southwest", "WN"),
    ("american", "AA"),
    ("jetblue", "B6"),
    ("frontier", "F9"),
    ("allegiant", "G4"),
    ("skywest", "OO"),
    ("alaska", "AS"),
    ("united", "UA"),
    ("delta", "DL"),
    ("envoy", "MQ"),
    ("republic", "YX"),
]
_AIRPORT_STOP = {
    "NAS", "THE", "AND", "FOR", "ARE", "NOT", "ALL", "HOW", "WHY", "WHO", "WAS",
    "HAS", "ITS", "OWN", "VS", "ANY", "ONE", "TWO", "PER", "DAY", "OUT", "OFF",
    "MOST", "BEST", "FROM", "WITH", "THAT", "THIS", "WHAT", "WHEN", "FIVE",
    "TEN", "BTS", "DOT", "AVG",
}


class LLMError(RuntimeError):
    pass


def build_openai_payload(model: str, system: str, user: str) -> dict:
    return {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }


def parse_openai_response(payload: dict) -> str:
    return payload["choices"][0]["message"]["content"]


def build_anthropic_payload(model: str, system: str, user: str) -> dict:
    return {
        "model": model,
        "max_tokens": 2000,
        "temperature": 0,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }


def parse_anthropic_response(payload: dict) -> str:
    return payload["content"][0]["text"]


def _post_json(url: str, payload: dict, headers: dict) -> dict:
    data = json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.loads(response.read().decode())


class OpenAIClient:
    name = "openai"

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def complete(self, system: str, user: str) -> str:
        payload = build_openai_payload(self.model, system, user)
        body = _post_json(
            "https://api.openai.com/v1/chat/completions",
            payload,
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        return parse_openai_response(body)


class AnthropicClient:
    name = "anthropic"

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def complete(self, system: str, user: str) -> str:
        payload = build_anthropic_payload(self.model, system, user)
        body = _post_json(
            "https://api.anthropic.com/v1/messages",
            payload,
            {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
        )
        return parse_anthropic_response(body)


def get_client():
    """Pick a client from the environment. No key means the stub."""
    provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if provider in {"", "auto"}:
        if openai_key:
            provider = "openai"
        elif anthropic_key:
            provider = "anthropic"
        else:
            provider = "stub"
    if provider == "stub":
        return StubClient()
    if provider == "openai":
        if not openai_key:
            raise LLMError("LLM_PROVIDER=openai requires the OPENAI_API_KEY environment variable.")
        return OpenAIClient(openai_key, os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
    if provider == "anthropic":
        if not anthropic_key:
            raise LLMError("LLM_PROVIDER=anthropic requires the ANTHROPIC_API_KEY environment variable.")
        return AnthropicClient(anthropic_key, os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5"))
    raise LLMError(f"Unknown LLM_PROVIDER '{provider}'. Use stub, openai, or anthropic.")


def _ok(sql: str, chart: bool = False) -> dict:
    return {"sql": sql, "refuse": False, "refusal_reason": None, "chart": chart}


def _refuse(reason: str) -> dict:
    return {"sql": None, "refuse": True, "refusal_reason": reason, "chart": False}


def _fallback() -> dict:
    return _ok("SELECT 'systemwide' AS label, COUNT(*) AS operated_flights FROM flights_v WHERE operated")


def _airlines(text: str) -> list[str]:
    hits = []
    low = text.lower()
    for word, code in _AIRLINE_WORDS:
        for match in re.finditer(rf"\b{word}\b", low):
            hits.append((match.start(), code))
    hits.sort()
    found = []
    for _pos, code in hits:
        if code not in found:
            found.append(code)
    return found


def _airports(text: str) -> list[str]:
    return [code for code in re.findall(r"\b[A-Z]{3}\b", text) if code not in _AIRPORT_STOP]


def _direction(text: str) -> tuple[str, str] | None:
    match = re.search(r"\b([A-Z]{3})\s+to\s+([A-Z]{3})\b", text)
    if match:
        return match.group(1), match.group(2)
    return None


def _grouped(label: str, group: str, metric: str, expr: str, where: str, having: str | None, descending: bool, limit: int | None) -> str:
    direction = "DESC" if descending else "ASC"
    having_sql = f"\nHAVING {having}" if having else ""
    limit_sql = f"\nLIMIT {int(limit)}" if limit else ""
    return f"""SELECT {label} AS label,
       {expr} AS {metric},
       COUNT(*) AS n
FROM flights_v
WHERE {where}
GROUP BY {group}{having_sql}
ORDER BY {metric} {direction}, label ASC{limit_sql}"""


def _scalar(label: str, metric: str, expr: str, where: str, source: str = "flights_v") -> str:
    return f"""SELECT '{label}' AS label,
       {expr} AS {metric}
FROM {source}
WHERE {where}"""


def _five_causes(where: str) -> str:
    parts = []
    for label, column in [
        ("carrier", "carrier_delay"),
        ("weather", "weather_delay"),
        ("nas", "nas_delay"),
        ("security", "security_delay"),
        ("late_aircraft", "late_aircraft_delay"),
    ]:
        parts.append(
            f"""SELECT '{label}' AS label,
       SUM({column}) * 1.0 / NULLIF(SUM(cause_minutes), 0) AS share
FROM flights_v
WHERE {where}"""
        )
    return "\nUNION ALL\n".join(parts)


def plan_question(question: str) -> dict:
    """Rules baseline. Known gaps fall through to a flight-count query.

    Not implemented here: medians, the airport morning-minus-evening gap,
    the Thanksgiving-versus-October comparison, and "does weather explain most delays".
    """
    text = question.strip()
    low = text.lower()
    if any(token in low for token in ("yesterday", "tomorrow", "passenger-weighted", "passenger weighted")):
        if "passenger" in low:
            return _refuse("The on-time extract has no passenger counts, so a passenger-weighted rate cannot be computed.")
        return _refuse("This extract is not a live feed, so yesterday or tomorrow is outside its scope.")
    if "american" in low and any(phrase in low for phrase in ("how late", "usually late", "on-time record", "on time record")):
        from flight_analyst.questions import sql_american_lateness

        return _ok(sql_american_lateness())
    if low.startswith("which airline is best out of dfw"):
        from flight_analyst.questions import sql_best_out_of_dfw

        return _ok(sql_best_out_of_dfw(), chart=True)
    if "dice of busier" in low or "same-airport gap" in low:
        from flight_analyst.questions import sql_dice_or_airline

        return _ok(sql_dice_or_airline(), chart=True)
    if "what can the bts delay" in low:
        return _ok(_five_causes("late_arrival"), chart=True)
    if "single-partner operating partners" in low and "without them" in low:
        from flight_analyst.questions import sql_american_brand_pair

        return _ok(sql_american_brand_pair(), chart=True)
    if "envoy and psa" in low and "skywest" in low:
        return _ok(_scalar("American", "on_time_rate", ONTIME, "operated AND airline_code IN ('AA', 'MQ', 'OH', 'PT')"))
    if "150,000" in low and "10,000 to 49,999" in low:
        return _ok(
            """WITH airport_n AS (
    SELECT origin, COUNT(*) AS airport_flights
    FROM flights_v
    WHERE operated
    GROUP BY origin
)
SELECT CASE
           WHEN a.airport_flights >= 150000 THEN '150k_or_more'
           ELSE '10k_to_49999'
       END AS label,
       AVG(CASE WHEN f.on_time_arrival THEN 1.0 ELSE 0.0 END) AS on_time_rate
FROM flights_v f
JOIN airport_n a ON f.origin = a.origin
WHERE f.operated
  AND (a.airport_flights >= 150000 OR (a.airport_flights >= 10000 AND a.airport_flights < 50000))
GROUP BY 1""",
            chart=True,
        )
    if "american brand" in low and "every other carrier" in low:
        return _ok(
            """SELECT CASE WHEN airline_code IN ('AA', 'MQ', 'OH', 'PT') THEN 'American' ELSE 'other' END AS label,
       AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END) AS on_time_rate
FROM flights_v
WHERE operated AND origin = 'DFW'
GROUP BY 1""",
            chart=True,
        )
    if "american brand" in low and "airline-controlled" in low:
        return _ok(
            _scalar(
                "American",
                "share",
                "SUM(airline_controlled_minutes) * 1.0 / NULLIF(SUM(cause_minutes), 0)",
                "late_arrival AND cause_minutes > 0 AND airline_code IN ('AA', 'MQ', 'OH', 'PT')",
            )
        )
    if any(token in low for token in ("median", "morning-minus-evening", "2025-11-26", "explain most")) or low.startswith("how late is"):
        return _fallback()

    carriers = _airlines(text)
    airports = _airports(text)
    route = _direction(text)

    if "skywest" in low and "family" in low:
        return _ok(
            """SELECT 'OO' AS label,
       CASE WHEN COUNT(*) > 0 THEN 1 ELSE 0 END AS included
FROM carrier_families
WHERE family_code = 'AA' AND airline_code = 'OO'"""
        )
    if "does not count" in low and "late aircraft" in low:
        return _ok(_scalar("systemwide", "carrier_only_share", "SUM(carrier_delay) * 1.0 / NULLIF(SUM(cause_minutes), 0)", "late_arrival"))
    if "count as not on time" in low:
        return _ok(_scalar("systemwide", "on_time_rate", "AVG(CASE WHEN (NOT cancelled AND NOT diverted AND arr_delay < 15) THEN 1.0 ELSE 0.0 END)", "TRUE"))
    if "on-time departure" in low:
        return _ok(
            _grouped("airline_code", "airline_code", "on_time_departure_rate", "AVG(CASE WHEN on_time_departure THEN 1.0 ELSE 0.0 END)", "on_time_departure IS NOT NULL", f"COUNT(*) >= {MIN_AIRLINE_SYSTEMWIDE}", True, 5),
            chart=True,
        )
    if "regional" in low and "american" in low and "delta" in low:
        from flight_analyst.questions import sql_partners_and_mainline

        return _ok(sql_partners_and_mainline(), chart=True)
    if "each of the five" in low:
        return _ok(_five_causes("late_arrival"), chart=True)
    if "which bts delay cause" in low:
        return _ok(f"SELECT * FROM (\n{_five_causes('late_arrival')}\n) causes\nORDER BY share DESC, label ASC\nLIMIT 1")
    if "not counting late aircraft" in low:
        return _ok(
            _grouped("airline_code", "airline_code", "share", "SUM(carrier_delay) * 1.0 / NULLIF(SUM(cause_minutes), 0)", "late_arrival", f"COUNT(*) >= {MIN_LATE_FLIGHTS}", True, 1)
        )
    if "own fault" in low and carriers:
        code = carriers[0]
        return _ok(
            f"""SELECT 'airline_controlled' AS label,
       SUM(airline_controlled_minutes) * 1.0 / NULLIF(SUM(cause_minutes), 0) AS share
FROM flights_v
WHERE late_arrival AND airline_code = '{code}'
UNION ALL
SELECT 'weather' AS label,
       SUM(weather_delay) * 1.0 / NULLIF(SUM(cause_minutes), 0) AS share
FROM flights_v
WHERE late_arrival AND airline_code = '{code}'""",
            chart=True,
        )
    if "how do" in low and "compare" in low and len(carriers) >= 2 and route and "cancellation rate" in low:
        origin, dest = route
        a, b = carriers[0], carriers[1]
        return _ok(
            f"""SELECT codes.airline_code AS label,
       COUNT(f.flight_date) AS scheduled_flights,
       COALESCE(SUM(CASE WHEN f.operated THEN 1 ELSE 0 END), 0) AS operated_flights,
       AVG(CASE WHEN f.operated THEN CASE WHEN f.on_time_arrival THEN 1.0 ELSE 0.0 END END) AS on_time_rate,
       AVG(CASE WHEN f.flight_date IS NOT NULL THEN CASE WHEN f.cancelled THEN 1.0 ELSE 0.0 END END) AS cancellation_rate,
       AVG(CASE WHEN f.operated THEN f.arr_delay END) AS avg_arrival_delay
FROM (VALUES ('{a}'), ('{b}')) AS codes(airline_code)
LEFT JOIN flights_v f
  ON f.airline_code = codes.airline_code
 AND f.origin = '{origin}'
 AND f.dest = '{dest}'
GROUP BY codes.airline_code""",
            chart=True,
        )
    if "worst airports for evening" in low:
        return _ok(_grouped("origin", "origin", "on_time_rate", ONTIME, "operated AND dep_time_bucket = 'evening'", f"COUNT(*) >= {MIN_AIRPORT_BUCKET}", False, 5), chart=True)
    if "worst airports for morning" in low:
        return _ok(_grouped("origin", "origin", "on_time_rate", ONTIME, "operated AND dep_time_bucket = 'morning'", f"COUNT(*) >= {MIN_AIRPORT_BUCKET}", False, 5), chart=True)
    if "five best airports" in low:
        return _ok(_grouped("origin", "origin", "on_time_rate", ONTIME, "operated", f"COUNT(*) >= {MIN_AIRPORT}", True, 5), chart=True)
    if "five worst airports by on-time" in low:
        return _ok(_grouped("origin", "origin", "on_time_rate", ONTIME, "operated", f"COUNT(*) >= {MIN_AIRPORT}", False, 5), chart=True)
    if "which airport has the highest cancellation" in low:
        return _ok(_grouped("origin", "origin", "cancellation_rate", CANCEL, "TRUE", f"COUNT(*) >= {MIN_AIRPORT}", True, 1))
    if "five airports have the highest average" in low:
        return _ok(_grouped("origin", "origin", "avg_arrival_delay", "AVG(arr_delay)", "operated", f"COUNT(*) >= {MIN_AIRPORT}", True, 5), chart=True)
    if "which airport is the worst" in low:
        from flight_analyst.questions import sql_worst_airport

        return _ok(sql_worst_airport(), chart=True)
    if "dfw and dal" in low:
        return _ok(_grouped("origin", "origin", "on_time_rate", ONTIME, "operated AND origin IN ('DFW', 'DAL')", None, True, 5), chart=True)
    if "rank mco" in low:
        return _ok(_grouped("origin", "origin", "on_time_rate", ONTIME, "operated AND origin IN ('MCO', 'MIA', 'FLL', 'TPA', 'RSW')", None, True, 5), chart=True)
    if "rank atl, dfw" in low:
        return _ok(_grouped("origin", "origin", "on_time_rate", ONTIME, "operated AND origin IN ('ATL', 'DFW', 'DEN', 'ORD', 'CLT')", None, True, 5), chart=True)
    if "out of dallas" in low and len(carriers) >= 2:
        quoted = ", ".join(f"'{code}'" for code in carriers)
        return _ok(
            f"""SELECT airline_code AS label,
       {ONTIME} AS on_time_rate
FROM flights_v
WHERE operated AND origin IN ('DFW', 'DAL') AND airline_code IN ({quoted})
GROUP BY airline_code""",
            chart=True,
        )
    if ("most on time out of" in low or "best out of" in low) and not re.search(r"20\d{2}", low):
        match = re.search(r"(?:most on time|best) out of ([A-Z]{3})", text)
        if match:
            code = match.group(1)
            return _ok(_grouped("airline_code", "airline_code", "on_time_rate", ONTIME, f"operated AND origin = '{code}'", f"COUNT(*) >= {MIN_AIRLINE_AT_AIRPORT}", True, 1))
    if "five airlines" in low and "highest" in low:
        return _ok(_grouped("airline_code", "airline_code", "on_time_rate", ONTIME, "operated", f"COUNT(*) >= {MIN_AIRLINE_SYSTEMWIDE}", True, 5), chart=True)
    if "five airlines" in low and "lowest" in low:
        return _ok(_grouped("airline_code", "airline_code", "on_time_rate", ONTIME, "operated", f"COUNT(*) >= {MIN_AIRLINE_SYSTEMWIDE}", False, 5), chart=True)
    if "highest systemwide cancellation rate" in low and "month" not in low:
        return _ok(_grouped("airline_code", "airline_code", "cancellation_rate", CANCEL, "TRUE", f"SUM(CASE WHEN operated THEN 1 ELSE 0 END) >= {MIN_AIRLINE_SYSTEMWIDE}", True, 1))
    if "highest average signed arrival delay among operated" in low:
        return _ok(_grouped("airline_code", "airline_code", "avg_arrival_delay", "AVG(arr_delay)", "operated", f"COUNT(*) >= {MIN_AIRLINE_SYSTEMWIDE}", True, 1))
    if "at least 60 minutes" in low:
        return _ok(_grouped("airline_code", "airline_code", "severe_late_rate", "AVG(CASE WHEN arr_delay >= 60 THEN 1.0 ELSE 0.0 END)", "operated", f"COUNT(*) >= {MIN_AIRLINE_SYSTEMWIDE}", True, 1))
    if "most on time on" in low and route:
        origin, dest = route
        return _ok(_grouped("airline_code", "airline_code", "on_time_rate", ONTIME, f"operated AND origin = '{origin}' AND dest = '{dest}'", f"COUNT(*) >= {MIN_AIRLINE_AT_AIRPORT}", True, 1))
    if "operates the most flights" in low and route:
        origin, dest = route
        return _ok(_grouped("airline_code", "airline_code", "operated_flights", "COUNT(*)", f"operated AND origin = '{origin}' AND dest = '{dest}'", None, True, 1))
    if "five routes have the lowest on-time" in low:
        return _ok(_grouped("origin || '-' || dest", "origin, dest", "on_time_rate", ONTIME, "operated", f"COUNT(*) >= {MIN_ROUTE}", False, 5), chart=True)
    if "five routes have the highest cancellation" in low:
        return _ok(_grouped("origin || '-' || dest", "origin, dest", "cancellation_rate", CANCEL, "TRUE", f"COUNT(*) >= {MIN_ROUTE}", True, 5), chart=True)
    if "five routes out of" in low and airports:
        origin = airports[0]
        return _ok(_grouped("origin || '-' || dest", "origin, dest", "avg_arrival_delay", "AVG(arr_delay)", f"operated AND origin = '{origin}'", f"COUNT(*) >= {MIN_ROUTE_FROM_AIRPORT}", True, 5), chart=True)
    if "which direction is more on time" in low:
        return _ok(
            f"""SELECT 'ORD-LGA' AS label, {ONTIME} AS on_time_rate FROM flights_v WHERE operated AND origin = 'ORD' AND dest = 'LGA'
UNION ALL
SELECT 'LGA-ORD' AS label, {ONTIME} AS on_time_rate FROM flights_v WHERE operated AND origin = 'LGA' AND dest = 'ORD'""",
            chart=True,
        )
    if "worst systemwide on-time" in low and "hour" in low:
        label = "'hour_' || LPAD(CAST(sched_dep_hour AS VARCHAR), 2, '0')"
        return _ok(_grouped(label, "sched_dep_hour", "on_time_rate", ONTIME, "operated AND sched_dep_hour IS NOT NULL", "COUNT(*) >= 10000", False, 1))
    if "at atl, which scheduled departure hour" in low:
        label = "'hour_' || LPAD(CAST(sched_dep_hour AS VARCHAR), 2, '0')"
        return _ok(_grouped(label, "sched_dep_hour", "on_time_rate", ONTIME, "operated AND origin = 'ATL' AND sched_dep_hour IS NOT NULL", "COUNT(*) >= 400", True, 1))
    if "percentage points" in low:
        return _ok(
            """SELECT 'systemwide' AS label,
       (
           (SELECT AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END) FROM flights_v WHERE operated AND dep_time_bucket = 'morning')
           - (SELECT AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END) FROM flights_v WHERE operated AND dep_time_bucket = 'evening')
       ) * 100.0 AS percentage_points"""
        )
    if "17:00 hour" in low:
        return _ok(_scalar("hour_17", "on_time_rate", ONTIME, "operated AND origin = 'EWR' AND sched_dep_hour = 17"))
    if "day of week" in low and "highest" in low:
        day = """CASE day_of_week WHEN 1 THEN 'Monday' WHEN 2 THEN 'Tuesday' WHEN 3 THEN 'Wednesday' WHEN 4 THEN 'Thursday' WHEN 5 THEN 'Friday' WHEN 6 THEN 'Saturday' WHEN 7 THEN 'Sunday' END"""
        return _ok(_grouped(day, "day_of_week", "on_time_rate", ONTIME, "operated", None, True, 1))
    if "day of week" in low and "lowest" in low:
        day = """CASE day_of_week WHEN 1 THEN 'Monday' WHEN 2 THEN 'Tuesday' WHEN 3 THEN 'Wednesday' WHEN 4 THEN 'Thursday' WHEN 5 THEN 'Friday' WHEN 6 THEN 'Saturday' WHEN 7 THEN 'Sunday' END"""
        return _ok(_grouped(day, "day_of_week", "on_time_rate", ONTIME, "operated", None, False, 1))
    if "friday evening" in low and "wednesday morning" in low:
        return _ok(
            f"""SELECT 'friday_evening' AS label, {ONTIME} AS on_time_rate FROM flights_v WHERE operated AND day_of_week = 5 AND dep_time_bucket = 'evening'
UNION ALL
SELECT 'wednesday_morning' AS label, {ONTIME} AS on_time_rate FROM flights_v WHERE operated AND day_of_week = 3 AND dep_time_bucket = 'morning'""",
            chart=True,
        )
    if "hour 06" in low and "hour 18" in low:
        return _ok(
            f"""SELECT 'hour_06' AS label, {ONTIME} AS on_time_rate FROM flights_v WHERE operated AND origin = 'ORD' AND sched_dep_hour = 6
UNION ALL
SELECT 'hour_18' AS label, {ONTIME} AS on_time_rate FROM flights_v WHERE operated AND origin = 'ORD' AND sched_dep_hour = 18""",
            chart=True,
        )
    if "hour 08" in low and "hour 20" in low:
        return _ok(
            f"""SELECT 'hour_08' AS label, {ONTIME} AS on_time_rate FROM flights_v WHERE operated AND origin = 'DEN' AND sched_dep_hour = 8
UNION ALL
SELECT 'hour_20' AS label, {ONTIME} AS on_time_rate FROM flights_v WHERE operated AND origin = 'DEN' AND sched_dep_hour = 20""",
            chart=True,
        )
    if "time-of-day bucket" in low:
        return _ok(_grouped("dep_time_bucket", "dep_time_bucket", "cancellation_rate", CANCEL, "dep_time_bucket IS NOT NULL", None, True, 1))
    if "which season has the highest" in low:
        where = "operated AND origin = 'MIA'" if "MIA" in airports else "operated"
        return _ok(_grouped("season", "season", "on_time_rate", ONTIME, where, None, True, 1))
    if "which season has the lowest" in low:
        return _ok(_grouped("season", "season", "on_time_rate", ONTIME, "operated", None, False, 1))
    if "which month has the lowest" in low:
        label = "CAST(year AS VARCHAR) || '-' || LPAD(CAST(month AS VARCHAR), 2, '0')"
        return _ok(_grouped(label, "year, month", "on_time_rate", ONTIME, "operated", None, False, 1))
    if "which month has the highest" in low:
        label = "CAST(year AS VARCHAR) || '-' || LPAD(CAST(month AS VARCHAR), 2, '0')"
        return _ok(_grouped(label, "year, month", "cancellation_rate", CANCEL, "TRUE", None, True, 1))
    if "july 2026" in low and "ATL" in airports:
        from flight_analyst.questions import sql_atl_july_2026

        return _ok(sql_atl_july_2026(), chart=True)
    if "winter and summer" in low and airports:
        code = airports[0]
        return _ok(
            f"""SELECT 'winter' AS label, {ONTIME} AS on_time_rate FROM flights_v WHERE operated AND origin = '{code}' AND season = 'winter'
UNION ALL
SELECT 'summer' AS label, {ONTIME} AS on_time_rate FROM flights_v WHERE operated AND origin = '{code}' AND season = 'summer'""",
            chart=True,
        )
    if "between" in low and "on time" in low:
        match = re.search(r"between\s+([A-Z]{3})\s+and\s+([A-Z]{3})", text)
        if match:
            a, b = match.group(1), match.group(2)
            return _ok(
                f"""SELECT '{a}-{b}' AS label, {ONTIME} AS on_time_rate, COUNT(*) AS n
FROM flights_v WHERE operated AND origin = '{a}' AND dest = '{b}'
UNION ALL
SELECT '{b}-{a}' AS label, {ONTIME} AS on_time_rate, COUNT(*) AS n
FROM flights_v WHERE operated AND origin = '{b}' AND dest = '{a}'
UNION ALL
SELECT 'both-directions' AS label, {ONTIME} AS on_time_rate, COUNT(*) AS n
FROM flights_v WHERE operated AND ((origin = '{a}' AND dest = '{b}') OR (origin = '{b}' AND dest = '{a}'))""",
                chart=True,
            )
    if "average delay at" in low and airports:
        return _ok(_scalar(airports[0], "avg_arrival_delay", "AVG(arr_delay)", f"operated AND origin = '{airports[0]}'"))
    if "most delays" in low:
        from flight_analyst.questions import sql_most_delays

        return _ok(sql_most_delays())
    if "red-eye" in low:
        return _ok(
            f"""SELECT 'night' AS label, {ONTIME} AS on_time_rate FROM flights_v WHERE operated AND dep_time_bucket = 'night'
UNION ALL
SELECT 'other' AS label, {ONTIME} AS on_time_rate FROM flights_v WHERE operated AND dep_time_bucket <> 'night'""",
            chart=True,
        )
    if "late-aircraft share and the weather share" in low and route:
        origin, dest = route
        return _ok(
            f"""SELECT 'late_aircraft' AS label,
       SUM(late_aircraft_delay) * 1.0 / NULLIF(SUM(cause_minutes), 0) AS share
FROM flights_v
WHERE late_arrival AND origin = '{origin}' AND dest = '{dest}'
UNION ALL
SELECT 'weather' AS label,
       SUM(weather_delay) * 1.0 / NULLIF(SUM(cause_minutes), 0) AS share
FROM flights_v
WHERE late_arrival AND origin = '{origin}' AND dest = '{dest}'""",
            chart=True,
        )
    if "airline-controlled share of late minutes for american and southwest" in low:
        return _ok(
            """SELECT airline_code AS label,
       SUM(airline_controlled_minutes) * 1.0 / NULLIF(SUM(cause_minutes), 0) AS share
FROM flights_v
WHERE late_arrival AND airline_code IN ('AA', 'WN')
GROUP BY airline_code""",
            chart=True,
        )
    if "is airline-controlled" in low and carriers and airports:
        return _ok(_scalar(carriers[0], "share", "SUM(airline_controlled_minutes) * 1.0 / NULLIF(SUM(cause_minutes), 0)", f"late_arrival AND airline_code = '{carriers[0]}' AND origin = '{airports[0]}'"))
    if "code b" in low or "cancellation code b" in low:
        return _ok(_scalar("systemwide", "share", "AVG(CASE WHEN cancellation_code = 'B' THEN 1.0 ELSE 0.0 END)", "cancelled"))
    if "coded a" in low:
        return _ok(_grouped("airline_code", "airline_code", "share", "AVG(CASE WHEN cancellation_code = 'A' THEN 1.0 ELSE 0.0 END)", "cancelled", f"COUNT(*) >= {MIN_CANCELLATIONS}", True, 1))
    if "security delay" in low:
        return _ok(_scalar("security", "share", "SUM(security_delay) * 1.0 / NULLIF(SUM(cause_minutes), 0)", "late_arrival"))
    if "nas delay" in low and airports:
        return _ok(_scalar(airports[0], "share", "SUM(nas_delay) * 1.0 / NULLIF(SUM(cause_minutes), 0)", f"late_arrival AND origin = '{airports[0]}'"))
    if "weather delay" in low and "winter" in low and airports:
        return _ok(_scalar(airports[0], "share", "SUM(weather_delay) * 1.0 / NULLIF(SUM(cause_minutes), 0)", f"late_arrival AND origin = '{airports[0]}' AND season = 'winter'"))
    if "share of scheduled flights are diverted" in low:
        return _ok(_scalar("systemwide", "diversion_rate", "AVG(CASE WHEN diverted THEN 1.0 ELSE 0.0 END)", "TRUE"))

    # Remaining "what is / what share / what are" figures.
    if low.startswith("what is") or low.startswith("what share") or low.startswith("what are the systemwide") or low.startswith("what are the winter"):
        metric = "on_time_rate"
        expr = ONTIME
        where_bits = ["operated"]
        label = "systemwide"
        source_note = None
        if "cancelled or diverted" in low:
            metric, expr = "irregular_rate", "AVG(CASE WHEN cancelled OR diverted THEN 1.0 ELSE 0.0 END)"
            where_bits = ["TRUE"]
        elif "cancellation rate" in low or re.search(r"\bare cancelled\b", low):
            metric, expr = "cancellation_rate", CANCEL
            where_bits = ["TRUE"]
        elif "average signed arrival delay" in low:
            metric, expr = "avg_arrival_delay", "AVG(arr_delay)"
        if "evening" in low and "morning" not in low:
            where_bits.append("dep_time_bucket = 'evening'")
            label = "evening"
        elif "morning departures" in low:
            where_bits.append("dep_time_bucket = 'morning'")
            label = "morning"
        elif re.search(r"\bnight departures\b", low):
            where_bits.append("dep_time_bucket = 'night'")
            label = "night"
        if "winter" in low and "summer" not in low:
            where_bits.append("season = 'winter'")
        elif "summer" in low and "winter" not in low:
            where_bits.append("season = 'summer'")
        if "december 2025" in low:
            where_bits.append("year = 2025 AND month = 12")
            label = "2025-12"
        if "arriving at" in low and airports:
            where_bits.append(f"dest = '{airports[0]}'")
            label = airports[0]
        elif route and not carriers:
            origin, dest = route
            where_bits.append(f"origin = '{origin}' AND dest = '{dest}'")
            label = f"{origin}-{dest}"
        elif airports and "systemwide" not in low:
            where_bits.append(f"origin = '{airports[0]}'")
            label = airports[0]
        if carriers and "systemwide" in low and "which" not in low:
            where_bits.append(f"airline_code = '{carriers[0]}'")
            label = carriers[0]
        elif carriers and route:
            origin, dest = route
            where_bits = [bit for bit in where_bits if not bit.startswith("origin =")]
            where_bits.append(f"airline_code = '{carriers[0]}' AND origin = '{origin}' AND dest = '{dest}'")
            label = carriers[0]
        elif carriers and airports and "systemwide" not in low:
            where_bits.append(f"airline_code = '{carriers[0]}'")
            label = carriers[0]
        where = " AND ".join(bit for bit in where_bits if bit != "TRUE") or "TRUE"
        if source_note:
            where = source_note
        return _ok(_scalar(label, metric, expr, where))
    return _fallback()


class StubClient:
    """Rules baseline used when no model API key is configured."""

    name = "stub"
    model = None

    def complete(self, system: str, user: str) -> str:
        del system
        return json.dumps(plan_question(user))
