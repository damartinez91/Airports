"""Plain-English questions and the reference SQL behind the draft candidate answers.

Nothing in this module is an approved standard. `bank.py` executes the SQL and
stores the results as candidate answers for review.
"""

from __future__ import annotations

from flight_analyst.definitions import (
    MIN_AIRLINE_AT_AIRPORT,
    MIN_AIRLINE_SYSTEMWIDE,
    MIN_AIRPORT,
    MIN_AIRPORT_BUCKET,
    MIN_BUCKET_EACH,
    MIN_CANCELLATIONS,
    MIN_LATE_FLIGHTS,
    MIN_ROUTE,
    MIN_ROUTE_FROM_AIRPORT,
    MINIMUM_RULE,
    TOLERANCE_FRACTION,
    TOLERANCE_MINUTES,
    TOLERANCE_PERCENTAGE_POINTS,
    WHY_LIMIT,
)

ONTIME = "AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END)"
CANCEL = "AVG(CASE WHEN cancelled THEN 1.0 ELSE 0.0 END)"
DEP_ONTIME = "AVG(CASE WHEN on_time_departure THEN 1.0 ELSE 0.0 END)"

_UNITS = {
    "on_time_rate": ("fraction", TOLERANCE_FRACTION),
    "on_time_departure_rate": ("fraction", TOLERANCE_FRACTION),
    "cancellation_rate": ("fraction", TOLERANCE_FRACTION),
    "diversion_rate": ("fraction", TOLERANCE_FRACTION),
    "irregular_rate": ("fraction", TOLERANCE_FRACTION),
    "severe_late_rate": ("fraction", TOLERANCE_FRACTION),
    "share": ("fraction", TOLERANCE_FRACTION),
    "carrier_only_share": ("fraction", TOLERANCE_FRACTION),
    "weather_share": ("fraction", TOLERANCE_FRACTION),
    "nas_share": ("fraction", TOLERANCE_FRACTION),
    "rate_gap": ("fraction", TOLERANCE_FRACTION),
    "avg_arrival_delay": ("minutes", TOLERANCE_MINUTES),
    "median_arrival_delay": ("minutes", TOLERANCE_MINUTES),
    "percentage_points": ("percentage_points", TOLERANCE_PERCENTAGE_POINTS),
    "operated_flights": ("count", 0),
    "scheduled_flights": ("count", 0),
    "late_flights": ("count", 0),
    "late_rate": ("fraction", TOLERANCE_FRACTION),
    "figure": ("fraction", TOLERANCE_FRACTION),
    "included": ("flag", 0),
    "weather_is_majority": ("flag", 0),
}

DAY_NAME = """CASE day_of_week
    WHEN 1 THEN 'Monday' WHEN 2 THEN 'Tuesday' WHEN 3 THEN 'Wednesday'
    WHEN 4 THEN 'Thursday' WHEN 5 THEN 'Friday' WHEN 6 THEN 'Saturday'
    WHEN 7 THEN 'Sunday' END"""
MONTH_LABEL = "CAST(year AS VARCHAR) || '-' || LPAD(CAST(month AS VARCHAR), 2, '0')"
HOUR_LABEL = "'hour_' || LPAD(CAST(sched_dep_hour AS VARCHAR), 2, '0')"


def _vals(*names: str) -> list[dict]:
    specs = []
    for name in names:
        unit, tolerance = _UNITS[name]
        specs.append({"name": name, "tolerance": tolerance, "unit": unit})
    return specs


def _q(
    question: str,
    difficulty: str,
    category: str,
    sql: str | None,
    answer_type: str,
    values: list[dict] | None = None,
    notes: str = "Candidate interpretation follows the project definitions.",
    ambiguous: bool = False,
    key_columns: list[str] | None = None,
    interpretation: str = "",
    follow_up: str = "",
    alternatives: str = "",
    why_limit: str = "",
) -> dict:
    minimum_rule = MINIMUM_RULE if answer_type == "ranking" else ""
    return {
        "status": "DRAFT",
        "question": question,
        "difficulty": difficulty,
        "category": category,
        "ambiguous": ambiguous,
        "answer_type": answer_type,
        "sql": sql.strip() if sql else None,
        "key_columns": ["label"] if key_columns is None and answer_type != "refusal" else (key_columns or []),
        "values": values or [],
        "notes": notes,
        "interpretation": interpretation,
        "follow_up": follow_up,
        "alternatives": alternatives,
        "why_limit": why_limit,
        "minimum_rule": minimum_rule,
    }


def _rank(
    label: str,
    group: str,
    metric: str,
    expr: str,
    where: str,
    having: str | None,
    descending: bool,
    limit: int | None,
) -> str:
    direction = "DESC" if descending else "ASC"
    having_sql = f"\nHAVING {having}" if having else ""
    limit_sql = f"\nLIMIT {limit}" if limit else ""
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


def _pair(metric: str, expr: str, rows: list[tuple[str, str]]) -> str:
    parts = [
        f"SELECT '{label}' AS label, {expr} AS {metric} FROM flights_v WHERE {where}"
        for label, where in rows
    ]
    return "\nUNION ALL\n".join(parts)


def _cause_parts(where: str) -> str:
    columns = [
        ("carrier", "carrier_delay"),
        ("weather", "weather_delay"),
        ("nas", "nas_delay"),
        ("security", "security_delay"),
        ("late_aircraft", "late_aircraft_delay"),
    ]
    parts = []
    for label, column in columns:
        parts.append(
            f"""SELECT '{label}' AS label,
       SUM({column}) * 1.0 / NULLIF(SUM(cause_minutes), 0) AS share
FROM flights_v
WHERE {where}"""
        )
    return "\nUNION ALL\n".join(parts)


def _own_fault(code: str) -> str:
    where = f"late_arrival AND airline_code = '{code}'"
    return f"""SELECT 'airline_controlled' AS label,
       SUM(airline_controlled_minutes) * 1.0 / NULLIF(SUM(cause_minutes), 0) AS share
FROM flights_v
WHERE {where}
UNION ALL
SELECT 'weather' AS label,
       SUM(weather_delay) * 1.0 / NULLIF(SUM(cause_minutes), 0) AS share
FROM flights_v
WHERE {where}"""


def _compare(a: str, b: str, origin: str, dest: str) -> str:
    """Both named airlines are returned, including a zero-flight row."""
    return f"""SELECT codes.airline_code AS label,
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
GROUP BY codes.airline_code"""


def sql_american_brand_pair() -> str:
    return f"""SELECT 'american_mainline' AS label, {ONTIME} AS on_time_rate, COUNT(*) AS n
FROM flights_v WHERE operated AND airline_code = 'AA'
UNION ALL
SELECT 'american_with_partners' AS label, {ONTIME} AS on_time_rate, COUNT(*) AS n
FROM flights_v WHERE operated AND airline_code IN ('AA', 'MQ', 'OH', 'PT')"""


def sql_american_lateness() -> str:
    """Same figures for every phrasing of the American lateness question."""
    return """SELECT 'AA' AS label,
       AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END) AS on_time_rate,
       AVG(arr_delay) AS avg_arrival_delay
FROM flights_v
WHERE operated AND airline_code = 'AA'"""


def sql_best_out_of_dfw() -> str:
    return f"""WITH carrier AS (
    SELECT airline_code,
           AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END) AS on_time_rate,
           COUNT(*) AS n
    FROM flights_v
    WHERE operated AND origin = 'DFW'
    GROUP BY airline_code
    HAVING COUNT(*) >= {MIN_AIRLINE_AT_AIRPORT}
),
best_reporting AS (
    SELECT * FROM carrier ORDER BY on_time_rate DESC, airline_code ASC LIMIT 1
),
best_mainline AS (
    SELECT * FROM carrier
    WHERE airline_code IN ('AA', 'DL', 'UA', 'WN', 'AS', 'B6', 'NK', 'F9', 'G4', 'HA')
    ORDER BY on_time_rate DESC, airline_code ASC
    LIMIT 1
),
branded AS (
    SELECT CASE
               WHEN airline_code IN ('AA', 'MQ', 'OH', 'PT') THEN 'brand_american'
               WHEN airline_code IN ('DL', '9E') THEN 'brand_delta'
               WHEN airline_code IN ('UA', 'C5', 'G7', 'ZW') THEN 'brand_united'
               WHEN airline_code IN ('AS', 'QX') THEN 'brand_alaska'
               WHEN airline_code = 'WN' THEN 'brand_southwest'
               WHEN airline_code = 'B6' THEN 'brand_jetblue'
               WHEN airline_code = 'NK' THEN 'brand_spirit'
               WHEN airline_code = 'F9' THEN 'brand_frontier'
               WHEN airline_code = 'G4' THEN 'brand_allegiant'
               WHEN airline_code = 'HA' THEN 'brand_hawaiian'
               WHEN airline_code = 'OO' THEN 'brand_skywest_unassigned'
               WHEN airline_code = 'YX' THEN 'brand_republic_unassigned'
               ELSE 'brand_' || lower(airline_code)
           END AS label,
           AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END) AS on_time_rate,
           COUNT(*) AS n
    FROM flights_v
    WHERE operated AND origin = 'DFW'
    GROUP BY 1
    HAVING COUNT(*) >= {MIN_AIRLINE_AT_AIRPORT}
)
SELECT * FROM (
    SELECT 'reporting_best' AS label,
           CASE airline_code WHEN 'MQ' THEN 'Envoy (American Eagle)' ELSE airline_code END AS who,
           on_time_rate,
           n
    FROM best_reporting
    UNION ALL
    SELECT 'mainline_best' AS label,
           airline_code AS who,
           on_time_rate,
           n
    FROM best_mainline
    UNION ALL
    SELECT label, label AS who, on_time_rate, n FROM branded
) cuts
ORDER BY CASE label
             WHEN 'reporting_best' THEN 0
             WHEN 'mainline_best' THEN 1
             ELSE 2
         END,
         label ASC"""


def sql_dfw_ord_both_ways() -> str:
    return f"""SELECT 'DFW-ORD' AS label,
       {ONTIME} AS on_time_rate,
       COUNT(*) AS n
FROM flights_v
WHERE operated AND origin = 'DFW' AND dest = 'ORD'
UNION ALL
SELECT 'ORD-DFW' AS label,
       {ONTIME} AS on_time_rate,
       COUNT(*) AS n
FROM flights_v
WHERE operated AND origin = 'ORD' AND dest = 'DFW'
UNION ALL
SELECT 'both-directions' AS label,
       {ONTIME} AS on_time_rate,
       COUNT(*) AS n
FROM flights_v
WHERE operated AND ((origin = 'DFW' AND dest = 'ORD') OR (origin = 'ORD' AND dest = 'DFW'))"""


def sql_worst_airport() -> str:
    return f"""WITH airport_n AS (
    SELECT origin, COUNT(*) AS airport_flights
    FROM flights_v
    WHERE operated
    GROUP BY origin
),
rates AS (
    SELECT f.origin,
           AVG(CASE WHEN f.on_time_arrival THEN 1.0 ELSE 0.0 END) AS on_time_rate,
           COUNT(*) AS n,
           a.airport_flights
    FROM flights_v f
    JOIN airport_n a ON f.origin = a.origin
    WHERE f.operated
    GROUP BY f.origin, a.airport_flights
)
SELECT * FROM (
    SELECT 'overall_worst' AS label, origin AS airport, on_time_rate, n
    FROM rates
    WHERE airport_flights >= {MIN_AIRPORT}
    ORDER BY on_time_rate ASC, origin ASC
    LIMIT 1
)
UNION ALL
SELECT * FROM (
    SELECT 'large_hub_worst' AS label, origin AS airport, on_time_rate, n
    FROM rates
    WHERE airport_flights >= 150000
    ORDER BY on_time_rate ASC, origin ASC
    LIMIT 1
)"""


def sql_most_delays() -> str:
    return f"""SELECT airline_code AS label,
       SUM(CASE WHEN late_arrival THEN 1 ELSE 0 END) AS late_flights,
       AVG(CASE WHEN late_arrival THEN 1.0 ELSE 0.0 END) AS late_rate,
       COUNT(*) AS n
FROM flights_v
WHERE operated
GROUP BY airline_code
HAVING COUNT(*) >= {MIN_AIRLINE_SYSTEMWIDE}
ORDER BY late_flights DESC, label ASC
LIMIT 1"""


def sql_named_routes(metric_sql: str, metric_name: str, where: str, having: int) -> str:
    return f"""SELECT origin || '-' || dest AS label,
       origin_city_name || ' to ' || dest_city_name AS route_name,
       {metric_sql} AS {metric_name},
       COUNT(*) AS n
FROM flights_v
WHERE {where}
GROUP BY origin, dest, origin_city_name, dest_city_name
HAVING COUNT(*) >= {having}
ORDER BY {metric_name} DESC, label ASC
LIMIT 5"""


def sql_most_on_time_out_of(origin: str, extra_where: str = "") -> str:
    """Literal winner, plus the major carriers at one airport.

    Majors are the top 3 by operated departures. If those 3 cover less than
    about 80 percent of operated departures, keep adding carriers by volume
    until the list crosses that share. The winner is included even when it
    is outside that volume list. SkyWest is labeled as a multi-airline regional.
    """
    if not extra_where:
        extra_where = ""
    elif not extra_where.startswith(" "):
        extra_where = " " + extra_where
    return f"""WITH base AS (
    SELECT airline_code,
           AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END) AS on_time_rate,
           COUNT(*) AS operated_flights
    FROM flights_v
    WHERE operated AND origin = '{origin}'{extra_where}
    GROUP BY airline_code
    HAVING COUNT(*) >= {MIN_AIRLINE_AT_AIRPORT}
),
ranked AS (
    SELECT airline_code,
           on_time_rate,
           operated_flights,
           ROW_NUMBER() OVER (ORDER BY on_time_rate DESC, airline_code ASC) AS rate_rank,
           ROW_NUMBER() OVER (ORDER BY operated_flights DESC, airline_code ASC) AS vol_rank,
           SUM(operated_flights) OVER () AS airport_flights,
           SUM(operated_flights) OVER (
               ORDER BY operated_flights DESC, airline_code ASC
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           ) AS cum_flights
    FROM base
)
SELECT CASE
           WHEN airline_code = 'OO' THEN 'SkyWest (flies for several airlines)'
           ELSE airline_code
       END AS label,
       on_time_rate,
       operated_flights
FROM ranked
WHERE rate_rank = 1
   OR vol_rank <= 3
   OR (cum_flights - operated_flights) < 0.80 * airport_flights
ORDER BY CASE WHEN rate_rank = 1 THEN 0 ELSE 1 END, operated_flights DESC, airline_code ASC"""


def sql_atl_july_2026() -> str:
    return sql_most_on_time_out_of("ATL", "AND year = 2026 AND month = 7")


def sql_partners_and_mainline() -> str:
    return f"""SELECT 'american_mainline' AS label, {ONTIME} AS on_time_rate, COUNT(*) AS n
FROM flights_v WHERE operated AND airline_code = 'AA'
UNION ALL
SELECT 'american_with_partners' AS label, {ONTIME} AS on_time_rate, COUNT(*) AS n
FROM flights_v WHERE operated AND airline_code IN ('AA', 'MQ', 'OH', 'PT')
UNION ALL
SELECT 'delta_mainline' AS label, {ONTIME} AS on_time_rate, COUNT(*) AS n
FROM flights_v WHERE operated AND airline_code = 'DL'
UNION ALL
SELECT 'delta_with_partners' AS label, {ONTIME} AS on_time_rate, COUNT(*) AS n
FROM flights_v WHERE operated AND airline_code IN ('DL', '9E')"""


def sql_dice_or_airline() -> str:
    """Volume buckets plus the same-airport gap for four brands."""
    return f"""WITH origin_n AS (
    SELECT origin, COUNT(*) AS airport_flights
    FROM flights_v
    WHERE operated
    GROUP BY origin
),
volume AS (
    SELECT CASE
               WHEN a.airport_flights >= 150000 THEN 'largest_airports'
               ELSE 'midsize_airports'
           END AS label,
           AVG(CASE WHEN f.on_time_arrival THEN 1.0 ELSE 0.0 END) AS figure
    FROM flights_v f
    JOIN origin_n a ON f.origin = a.origin
    WHERE f.operated
      AND (a.airport_flights >= 150000 OR (a.airport_flights >= 10000 AND a.airport_flights < 50000))
    GROUP BY 1
),
cell AS (
    SELECT CASE
               WHEN airline_code IN ('AA', 'MQ', 'OH', 'PT') THEN 'american'
               WHEN airline_code IN ('DL', '9E') THEN 'delta'
               WHEN airline_code IN ('UA', 'C5', 'G7', 'ZW') THEN 'united'
               WHEN airline_code = 'WN' THEN 'southwest'
               ELSE 'other'
           END AS brand,
           origin,
           COUNT(*) AS n,
           AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END) AS rate
    FROM flights_v
    WHERE operated
    GROUP BY 1, 2
),
totals AS (
    SELECT origin, SUM(n) AS n_all, SUM(n * rate) AS on_time_all
    FROM cell
    GROUP BY origin
),
gaps AS (
    SELECT c.brand AS label,
           SUM(c.n * (c.rate - (t.on_time_all - c.n * c.rate) / (t.n_all - c.n))) / SUM(c.n) AS figure
    FROM cell c
    JOIN totals t USING (origin)
    WHERE c.brand <> 'other'
      AND c.n >= {MIN_AIRLINE_AT_AIRPORT}
      AND (t.n_all - c.n) >= {MIN_AIRLINE_AT_AIRPORT}
    GROUP BY c.brand
)
SELECT label, figure FROM volume
UNION ALL
SELECT label || '_same_airport_gap' AS label, figure FROM gaps"""


def build_questions() -> list[dict]:
    questions: list[dict] = []
    add = questions.append

    for code, difficulty in [
        ("DFW", "easy"),
        ("ORD", "easy"),
        ("ATL", "easy"),
        ("DEN", "medium"),
        ("LAX", "easy"),
        ("LGA", "medium"),
        ("BOS", "medium"),
        ("SEA", "medium"),
        ("EWR", "medium"),
    ]:
        add(_q(
            f"Which airline is most on time out of {code}?",
            difficulty,
            "airline_reliability",
            sql_most_on_time_out_of(code),
            "ranking",
            _vals("on_time_rate", "operated_flights"),
            notes=(
                f"Batch 4, 2026-10-11: literal winner out of {code} after "
                f"{MIN_AIRLINE_AT_AIRPORT} operated flights, plus the major carriers at that airport. "
                "Majors are the top 3 by operated departures, extended until they cover about 80 percent of operated departures. "
                "Each row has the on-time arrival rate and the flight count. "
                "SkyWest is labeled SkyWest (flies for several airlines). "
                "The first row is the literal winner. The rest follow by volume. "
                "Daniel approved this question on 2026-10-13."
            ),
            ambiguous=True,
            interpretation=(
                f"I read this as the reporting carrier with the highest on-time arrival rate out of {code}, "
                "and the major carriers there: the top 3 by flights, or more if that is what it takes to cover about 80 percent."
            ),
            alternatives="Literal winner, plus major carriers at this airport, each with an on-time rate and a flight count.",
            follow_up="Want the same airport for American, Delta, and United, with and without operating partners?",
        ))

    add(_q(
        "Which five airlines have the highest systemwide on-time arrival rate?",
        "medium",
        "airline_reliability",
        _rank("airline_code", "airline_code", "on_time_rate", ONTIME, "operated", f"COUNT(*) >= {MIN_AIRLINE_SYSTEMWIDE}", True, 5),
        "ranking",
        _vals("on_time_rate"),
        notes=(
            "Five reporting carriers after 5,000 operated flights. "
            "Hawaiian benefits from island weather. Hawaiian also stops reporting after December 2025 in this extract, so the lead is a partial year. "
            "Daniel approved this question on 2026-10-13."
        ),
        interpretation="The five reporting carriers with the highest on-time arrival rate. Hawaiian benefits from island weather.",
        follow_up="Want the same five after a full year, or with the partial-year carriers set aside?",
    ))
    add(_q(
        "Which five airlines have the lowest systemwide on-time arrival rate?",
        "medium",
        "airline_reliability",
        _rank("airline_code", "airline_code", "on_time_rate", ONTIME, "operated", f"COUNT(*) >= {MIN_AIRLINE_SYSTEMWIDE}", False, 5),
        "ranking",
        _vals("on_time_rate"),
    ))
    add(_q(
        "What is Southwest's systemwide on-time arrival rate?",
        "easy",
        "airline_reliability",
        _scalar("WN", "on_time_rate", ONTIME, "operated AND airline_code = 'WN'"),
        "scalar",
        _vals("on_time_rate"),
    ))
    add(_q(
        "What is American's systemwide on-time arrival rate?",
        "easy",
        "airline_reliability",
        _scalar("AA", "on_time_rate", ONTIME, "operated AND airline_code = 'AA'"),
        "scalar",
        _vals("on_time_rate"),
        notes="American here is reporting carrier AA only, not Envoy, PSA, or Piedmont.",
    ))
    add(_q(
        "What is Delta's systemwide cancellation rate?",
        "easy",
        "airline_reliability",
        _scalar("DL", "cancellation_rate", CANCEL, "airline_code = 'DL'"),
        "scalar",
        _vals("cancellation_rate"),
        notes="Cancellation rate uses all scheduled Delta flights, including cancelled ones, as the denominator.",
    ))
    add(_q(
        "Which airline has the highest systemwide cancellation rate?",
        "medium",
        "airline_reliability",
        _rank(
            "airline_code",
            "airline_code",
            "cancellation_rate",
            CANCEL,
            "TRUE",
            f"SUM(CASE WHEN operated THEN 1 ELSE 0 END) >= {MIN_AIRLINE_SYSTEMWIDE}",
            True,
            1,
        ),
        "ranking",
        _vals("cancellation_rate"),
    ))
    add(_q(
        "Which airline has the highest average signed arrival delay among operated flights, systemwide?",
        "medium",
        "airline_reliability",
        _rank(
            "airline_code",
            "airline_code",
            "avg_arrival_delay",
            "AVG(arr_delay)",
            "operated",
            f"COUNT(*) >= {MIN_AIRLINE_SYSTEMWIDE}",
            True,
            1,
        ),
        "ranking",
        _vals("avg_arrival_delay"),
        notes="Mean of the signed arrival delay. Early arrivals reduce it.",
    ))
    add(_q(
        "What is American's on-time arrival rate for flights out of DFW?",
        "easy",
        "airline_reliability",
        _scalar("AA", "on_time_rate", ONTIME, "operated AND airline_code = 'AA' AND origin = 'DFW'"),
        "scalar",
        _vals("on_time_rate"),
    ))
    add(_q(
        "Which airline has the highest share of operated flights that arrive at least 60 minutes late, systemwide?",
        "medium",
        "airline_reliability",
        _rank(
            "airline_code",
            "airline_code",
            "severe_late_rate",
            "AVG(CASE WHEN arr_delay >= 60 THEN 1.0 ELSE 0.0 END)",
            "operated",
            f"COUNT(*) >= {MIN_AIRLINE_SYSTEMWIDE}",
            True,
            1,
        ),
        "ranking",
        _vals("severe_late_rate"),
    ))
    add(_q(
        "What share of scheduled flights are diverted, systemwide?",
        "easy",
        "airline_reliability",
        _scalar("systemwide", "diversion_rate", "AVG(CASE WHEN diverted THEN 1.0 ELSE 0.0 END)", "TRUE"),
        "scalar",
        _vals("diversion_rate"),
    ))
    add(_q(
        "Which airline has the lowest median arrival delay systemwide?",
        "hard",
        "airline_reliability",
        _rank(
            "airline_code",
            "airline_code",
            "median_arrival_delay",
            "MEDIAN(arr_delay)",
            "operated",
            f"COUNT(*) >= {MIN_AIRLINE_SYSTEMWIDE}",
            False,
            1,
        ),
        "ranking",
        _vals("median_arrival_delay"),
        notes="Median of the signed arrival delay. Lowest median is the best of this metric.",
    ))
    add(_q(
        "Which airline is most on time out of MIA?",
        "medium",
        "airline_reliability",
        sql_most_on_time_out_of("MIA"),
        "ranking",
        _vals("on_time_rate", "operated_flights"),
        notes=(
            "Batch 4, 2026-10-11: same major-carrier rule as the other 'most on time out of' questions. "
            "Literal winner plus the top 3 by volume, extended to about 80 percent of operated departures. "
            "SkyWest is labeled SkyWest (flies for several airlines). Daniel approved this question on 2026-10-13."
        ),
        ambiguous=True,
        interpretation=(
            "I read this as the reporting carrier with the highest on-time arrival rate out of MIA, "
            "and the major carriers there: the top 3 by flights, or more if that is what it takes to cover about 80 percent."
        ),
        alternatives="Literal winner, plus major carriers at this airport, each with an on-time rate and a flight count.",
        follow_up="Want the same airport for American, Delta, and United, with and without operating partners?",
    ))

    add(_q(
        "What are the five worst airports for evening delays?",
        "medium",
        "airport_reliability",
        _rank(
            "origin",
            "origin",
            "on_time_rate",
            ONTIME,
            "operated AND dep_time_bucket = 'evening'",
            f"COUNT(*) >= {MIN_AIRPORT_BUCKET}",
            False,
            5,
        ),
        "ranking",
        _vals("on_time_rate"),
        notes=(
            "Worst means the lowest on-time arrival rate for flights scheduled to depart "
            f"from 17:00 through 20:59, among airports with at least {MIN_AIRPORT_BUCKET} such operated flights."
        ),
    ))
    add(_q(
        "What are the five worst airports for morning delays?",
        "medium",
        "airport_reliability",
        _rank(
            "origin",
            "origin",
            "on_time_rate",
            ONTIME,
            "operated AND dep_time_bucket = 'morning'",
            f"COUNT(*) >= {MIN_AIRPORT_BUCKET}",
            False,
            5,
        ),
        "ranking",
        _vals("on_time_rate"),
    ))
    add(_q(
        "What are the five best airports by on-time arrival rate?",
        "medium",
        "airport_reliability",
        _rank("origin", "origin", "on_time_rate", ONTIME, "operated", f"COUNT(*) >= {MIN_AIRPORT}", True, 5),
        "ranking",
        _vals("on_time_rate"),
        notes="Best means highest on-time arrival rate for departures, after the airport minimum-sample rule.",
    ))
    add(_q(
        "What are the five worst airports by on-time arrival rate?",
        "medium",
        "airport_reliability",
        _rank("origin", "origin", "on_time_rate", ONTIME, "operated", f"COUNT(*) >= {MIN_AIRPORT}", False, 5),
        "ranking",
        _vals("on_time_rate"),
    ))
    add(_q(
        "What is the on-time arrival rate for flights departing JFK?",
        "easy",
        "airport_reliability",
        _scalar("JFK", "on_time_rate", ONTIME, "operated AND origin = 'JFK'"),
        "scalar",
        _vals("on_time_rate"),
    ))
    add(_q(
        "What is the cancellation rate for flights departing ORD?",
        "easy",
        "airport_reliability",
        _scalar("ORD", "cancellation_rate", CANCEL, "origin = 'ORD'"),
        "scalar",
        _vals("cancellation_rate"),
    ))
    add(_q(
        "What is the cancellation rate for flights departing LGA?",
        "easy",
        "airport_reliability",
        _scalar("LGA", "cancellation_rate", CANCEL, "origin = 'LGA'"),
        "scalar",
        _vals("cancellation_rate"),
    ))
    add(_q(
        "Which airport has the highest cancellation rate?",
        "medium",
        "airport_reliability",
        _rank("origin", "origin", "cancellation_rate", CANCEL, "TRUE", f"COUNT(*) >= {MIN_AIRPORT}", True, 1),
        "ranking",
        _vals("cancellation_rate"),
    ))
    add(_q(
        "Which five airports have the highest average signed arrival delay?",
        "medium",
        "airport_reliability",
        _rank("origin", "origin", "avg_arrival_delay", "AVG(arr_delay)", "operated", f"COUNT(*) >= {MIN_AIRPORT}", True, 5),
        "ranking",
        _vals("avg_arrival_delay"),
    ))
    add(_q(
        "How do DFW and DAL compare on on-time arrival rate for departures? Return both rates.",
        "medium",
        "airport_reliability",
        _rank("origin", "origin", "on_time_rate", ONTIME, "operated AND origin IN ('DFW', 'DAL')", None, True, None),
        "set",
        _vals("on_time_rate"),
        notes="Both Dallas/Fort Worth International and Dallas Love Field, departures only, no minimum beyond the airports named.",
    ))
    add(_q(
        "What is the evening on-time arrival rate for flights departing EWR?",
        "easy",
        "airport_reliability",
        _scalar("EWR", "on_time_rate", ONTIME, "operated AND origin = 'EWR' AND dep_time_bucket = 'evening'"),
        "scalar",
        _vals("on_time_rate"),
    ))
    add(_q(
        "What share of flights departing SFO are cancelled or diverted?",
        "easy",
        "airport_reliability",
        _scalar("SFO", "irregular_rate", "AVG(CASE WHEN cancelled OR diverted THEN 1.0 ELSE 0.0 END)", "origin = 'SFO'"),
        "scalar",
        _vals("irregular_rate"),
    ))
    add(_q(
        "Rank MCO, MIA, FLL, TPA, and RSW by on-time arrival rate for departures.",
        "medium",
        "airport_reliability",
        _rank("origin", "origin", "on_time_rate", ONTIME, "operated AND origin IN ('MCO', 'MIA', 'FLL', 'TPA', 'RSW')", None, True, 5),
        "ranking",
        _vals("on_time_rate"),
    ))
    add(_q(
        "What is the on-time arrival rate for flights arriving at DCA?",
        "medium",
        "airport_reliability",
        _scalar("DCA", "on_time_rate", ONTIME, "operated AND dest = 'DCA'"),
        "scalar",
        _vals("on_time_rate"),
        notes="This is arrivals at DCA, not departures from DCA.",
    ))
    add(_q(
        "Rank ATL, DFW, DEN, ORD, and CLT by on-time arrival rate for departures.",
        "easy",
        "airport_reliability",
        _rank("origin", "origin", "on_time_rate", ONTIME, "operated AND origin IN ('ATL', 'DFW', 'DEN', 'ORD', 'CLT')", None, True, 5),
        "ranking",
        _vals("on_time_rate"),
    ))
    add(_q(
        "Which airport has the largest morning-minus-evening on-time arrival gap?",
        "hard",
        "airport_reliability",
        f"""SELECT origin AS label,
       AVG(CASE WHEN dep_time_bucket = 'morning' THEN CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END END)
       - AVG(CASE WHEN dep_time_bucket = 'evening' THEN CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END END) AS rate_gap,
       COUNT(*) AS n
FROM flights_v
WHERE operated AND dep_time_bucket IN ('morning', 'evening')
GROUP BY origin
HAVING SUM(CASE WHEN dep_time_bucket = 'morning' THEN 1 ELSE 0 END) >= {MIN_BUCKET_EACH}
   AND SUM(CASE WHEN dep_time_bucket = 'evening' THEN 1 ELSE 0 END) >= {MIN_BUCKET_EACH}
ORDER BY rate_gap DESC, label ASC
LIMIT 1""",
        "ranking",
        _vals("rate_gap"),
        notes=(
            "The gap is the morning on-time arrival rate minus the evening rate. "
            f"Each bucket needs at least {MIN_BUCKET_EACH} operated departures. Largest means the most positive gap."
        ),
    ))

    add(_q(
        "How do American and Southwest compare on DFW to ORD? For each airline return scheduled flights and operated flights, plus the on-time arrival rate, cancellation rate, and average signed arrival delay when that airline flew the route.",
        "medium",
        "routes",
        _compare("AA", "WN", "DFW", "ORD"),
        "set",
        _vals("scheduled_flights", "operated_flights"),
        notes="Directional, DFW to ORD. Both carriers are listed even if one has no flights. Rates are null when the operated count is zero, and those rates are not scored; the counts are. In this extract Southwest's Dallas flights use DAL, not DFW, so its DFW-ORD counts are the finding.",
    ))
    add(_q(
        "How do United and American compare on EWR to SFO? For each airline return scheduled flights and operated flights, plus the on-time arrival rate, cancellation rate, and average signed arrival delay when that airline flew the route.",
        "medium",
        "routes",
        _compare("UA", "AA", "EWR", "SFO"),
        "set",
        _vals("scheduled_flights", "operated_flights"),
        notes="Directional, EWR to SFO. Both carriers are listed even if one has no flights. Counts are scored. A null rate means that carrier did not operate the route in this extract.",
    ))
    add(_q(
        "How do Delta and Southwest compare on ATL to LGA? For each airline return the on-time arrival rate, the cancellation rate, and the average signed arrival delay.",
        "medium",
        "routes",
        _compare("DL", "WN", "ATL", "LGA"),
        "set",
        _vals("on_time_rate", "cancellation_rate", "avg_arrival_delay"),
    ))
    add(_q(
        "What is the on-time arrival rate on LAX to JFK?",
        "easy",
        "routes",
        _scalar("LAX-JFK", "on_time_rate", ONTIME, "operated AND origin = 'LAX' AND dest = 'JFK'"),
        "scalar",
        _vals("on_time_rate"),
        notes="All reporting airlines, one direction.",
    ))
    add(_q(
        "Which airline is most on time on LAX to SFO?",
        "medium",
        "routes",
        _rank("airline_code", "airline_code", "on_time_rate", ONTIME, "operated AND origin = 'LAX' AND dest = 'SFO'", f"COUNT(*) >= {MIN_AIRLINE_AT_AIRPORT}", True, 1),
        "ranking",
        _vals("on_time_rate"),
    ))
    add(_q(
        "Which five routes have the lowest on-time arrival rate?",
        "hard",
        "routes",
        _rank("origin || '-' || dest", "origin, dest", "on_time_rate", ONTIME, "operated", f"COUNT(*) >= {MIN_ROUTE}", False, 5),
        "ranking",
        _vals("on_time_rate"),
    ))
    add(_q(
        "Which five routes have the highest cancellation rate?",
        "hard",
        "routes",
        sql_named_routes(CANCEL, "cancellation_rate", "TRUE", MIN_ROUTE),
        "ranking",
        _vals("cancellation_rate"),
        notes="Each direction is its own named route. The route_name column is the BTS city pair, for example Detroit, MI to Pellston, MI. The reverse is a different row.",
        alternatives="Both directions are listed when both clear the 500-flight floor. Neither direction stands in for the other.",
    ))
    add(_q(
        "What is the average signed arrival delay on DFW to ORD?",
        "easy",
        "routes",
        _scalar("DFW-ORD", "avg_arrival_delay", "AVG(arr_delay)", "operated AND origin = 'DFW' AND dest = 'ORD'"),
        "scalar",
        _vals("avg_arrival_delay"),
    ))
    add(_q(
        "How do Alaska and Delta compare on SEA to LAX? For each airline return the on-time arrival rate, the cancellation rate, and the average signed arrival delay.",
        "medium",
        "routes",
        _compare("AS", "DL", "SEA", "LAX"),
        "set",
        _vals("on_time_rate", "cancellation_rate", "avg_arrival_delay"),
    ))
    add(_q(
        "What share of DFW to ORD flights are cancelled?",
        "easy",
        "routes",
        _scalar("DFW-ORD", "cancellation_rate", CANCEL, "origin = 'DFW' AND dest = 'ORD'"),
        "scalar",
        _vals("cancellation_rate"),
    ))
    add(_q(
        "Which direction is more on time, ORD to LGA or LGA to ORD? Return both on-time arrival rates.",
        "medium",
        "routes",
        _pair("on_time_rate", ONTIME, [
            ("ORD-LGA", "operated AND origin = 'ORD' AND dest = 'LGA'"),
            ("LGA-ORD", "operated AND origin = 'LGA' AND dest = 'ORD'"),
        ]),
        "set",
        _vals("on_time_rate"),
    ))
    add(_q(
        "How do JetBlue and Delta compare on BOS to MCO? For each airline return the on-time arrival rate, the cancellation rate, and the average signed arrival delay.",
        "medium",
        "routes",
        _compare("B6", "DL", "BOS", "MCO"),
        "set",
        _vals("on_time_rate", "cancellation_rate", "avg_arrival_delay"),
    ))
    add(_q(
        "Which airline operates the most flights from DAL to HOU?",
        "easy",
        "routes",
        _rank("airline_code", "airline_code", "operated_flights", "COUNT(*)", "operated AND origin = 'DAL' AND dest = 'HOU'", None, True, 1),
        "ranking",
        _vals("operated_flights"),
        notes="Operated flights only. The scored figure is the flight count.",
    ))
    add(_q(
        "How do American and United compare on ORD to DFW? For each airline return the on-time arrival rate, the cancellation rate, and the average signed arrival delay.",
        "medium",
        "routes",
        _compare("AA", "UA", "ORD", "DFW"),
        "set",
        _vals("on_time_rate", "cancellation_rate", "avg_arrival_delay"),
    ))
    add(_q(
        "What are the five routes out of DFW with the highest average signed arrival delay?",
        "hard",
        "routes",
        sql_named_routes("AVG(arr_delay)", "avg_arrival_delay", "operated AND origin = 'DFW'", MIN_ROUTE_FROM_AIRPORT),
        "ranking",
        _vals("avg_arrival_delay"),
        notes="Every listed route has its mean signed arrival delay in minutes and its operated-flight count. Early arrivals pull the mean down. The minimum is 200 operated flights from DFW. route_name is the city pair.",
        alternatives="The minutes are on every route in the list, not only the first.",
    ))
    add(_q(
        "What is Frontier's on-time arrival rate on DEN to LAS?",
        "medium",
        "routes",
        _scalar("F9", "on_time_rate", ONTIME, "operated AND airline_code = 'F9' AND origin = 'DEN' AND dest = 'LAS'"),
        "scalar",
        _vals("on_time_rate"),
    ))

    add(_q(
        "Which scheduled departure hour has the worst systemwide on-time arrival rate, among hours with at least 10000 operated flights?",
        "medium",
        "time_of_day",
        _rank(HOUR_LABEL, "sched_dep_hour", "on_time_rate", ONTIME, "operated AND sched_dep_hour IS NOT NULL", "COUNT(*) >= 10000", False, 1),
        "ranking",
        _vals("on_time_rate"),
    ))
    add(_q(
        "At ATL, which scheduled departure hour is most on time among hours with at least 400 operated departures?",
        "medium",
        "time_of_day",
        _rank(HOUR_LABEL, "sched_dep_hour", "on_time_rate", ONTIME, "operated AND origin = 'ATL' AND sched_dep_hour IS NOT NULL", "COUNT(*) >= 400", True, 1),
        "ranking",
        _vals("on_time_rate"),
    ))
    add(_q(
        "What is the systemwide on-time arrival rate for morning departures?",
        "easy",
        "time_of_day",
        _scalar("morning", "on_time_rate", ONTIME, "operated AND dep_time_bucket = 'morning'"),
        "scalar",
        _vals("on_time_rate"),
    ))
    add(_q(
        "What is the systemwide on-time arrival rate for evening departures?",
        "easy",
        "time_of_day",
        _scalar("evening", "on_time_rate", ONTIME, "operated AND dep_time_bucket = 'evening'"),
        "scalar",
        _vals("on_time_rate"),
    ))
    add(_q(
        "What is the systemwide on-time arrival rate for night departures?",
        "easy",
        "time_of_day",
        _scalar("night", "on_time_rate", ONTIME, "operated AND dep_time_bucket = 'night'"),
        "scalar",
        _vals("on_time_rate"),
    ))
    add(_q(
        "By how many percentage points is the systemwide morning on-time arrival rate higher than the evening rate? Return morning minus evening.",
        "medium",
        "time_of_day",
        """SELECT 'systemwide' AS label,
       (
           (SELECT AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END) FROM flights_v WHERE operated AND dep_time_bucket = 'morning')
           - (SELECT AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END) FROM flights_v WHERE operated AND dep_time_bucket = 'evening')
       ) * 100.0 AS percentage_points""",
        "scalar",
        _vals("percentage_points"),
        notes="A negative number means evening departures were more on time than morning departures.",
    ))
    add(_q(
        "What is the on-time arrival rate for EWR departures scheduled in the 17:00 hour?",
        "medium",
        "time_of_day",
        _scalar("hour_17", "on_time_rate", ONTIME, "operated AND origin = 'EWR' AND sched_dep_hour = 17"),
        "scalar",
        _vals("on_time_rate"),
    ))
    add(_q(
        "Which day of week has the highest systemwide on-time arrival rate?",
        "easy",
        "time_of_day",
        _rank(DAY_NAME, "day_of_week", "on_time_rate", ONTIME, "operated", None, True, 1),
        "ranking",
        _vals("on_time_rate"),
    ))
    add(_q(
        "Which day of week has the lowest systemwide on-time arrival rate?",
        "easy",
        "time_of_day",
        _rank(DAY_NAME, "day_of_week", "on_time_rate", ONTIME, "operated", None, False, 1),
        "ranking",
        _vals("on_time_rate"),
    ))
    add(_q(
        "What are the systemwide on-time arrival rates for Friday evening departures and Wednesday morning departures?",
        "medium",
        "time_of_day",
        _pair("on_time_rate", ONTIME, [
            ("friday_evening", "operated AND day_of_week = 5 AND dep_time_bucket = 'evening'"),
            ("wednesday_morning", "operated AND day_of_week = 3 AND dep_time_bucket = 'morning'"),
        ]),
        "set",
        _vals("on_time_rate"),
    ))
    add(_q(
        "At ORD, what are the on-time arrival rates for departures in hour 06 and hour 18?",
        "hard",
        "time_of_day",
        _pair("on_time_rate", ONTIME, [
            ("hour_06", "operated AND origin = 'ORD' AND sched_dep_hour = 6"),
            ("hour_18", "operated AND origin = 'ORD' AND sched_dep_hour = 18"),
        ]),
        "set",
        _vals("on_time_rate"),
    ))
    add(_q(
        "What is the average signed arrival delay for evening departures from LGA?",
        "medium",
        "time_of_day",
        _scalar("LGA", "avg_arrival_delay", "AVG(arr_delay)", "operated AND origin = 'LGA' AND dep_time_bucket = 'evening'"),
        "scalar",
        _vals("avg_arrival_delay"),
    ))
    add(_q(
        "Which time-of-day bucket has the highest cancellation rate systemwide?",
        "medium",
        "time_of_day",
        _rank("dep_time_bucket", "dep_time_bucket", "cancellation_rate", CANCEL, "dep_time_bucket IS NOT NULL", None, True, 1),
        "ranking",
        _vals("cancellation_rate"),
    ))
    add(_q(
        "At DEN, what are the on-time arrival rates for departures in hour 08 and hour 20?",
        "hard",
        "time_of_day",
        _pair("on_time_rate", ONTIME, [
            ("hour_08", "operated AND origin = 'DEN' AND sched_dep_hour = 8"),
            ("hour_20", "operated AND origin = 'DEN' AND sched_dep_hour = 20"),
        ]),
        "set",
        _vals("on_time_rate"),
    ))

    add(_q(
        "Which season has the highest systemwide on-time arrival rate?",
        "easy",
        "seasonality",
        _rank("season", "season", "on_time_rate", ONTIME, "operated", None, True, 1),
        "ranking",
        _vals("on_time_rate"),
    ))
    add(_q(
        "Which season has the lowest systemwide on-time arrival rate?",
        "easy",
        "seasonality",
        _rank("season", "season", "on_time_rate", ONTIME, "operated", None, False, 1),
        "ranking",
        _vals("on_time_rate"),
    ))
    add(_q(
        "What is the winter on-time arrival rate for flights departing ORD?",
        "easy",
        "seasonality",
        _scalar("ORD", "on_time_rate", ONTIME, "operated AND origin = 'ORD' AND season = 'winter'"),
        "scalar",
        _vals("on_time_rate"),
    ))
    add(_q(
        "What is the summer on-time arrival rate for flights departing ORD?",
        "easy",
        "seasonality",
        _scalar("ORD", "on_time_rate", ONTIME, "operated AND origin = 'ORD' AND season = 'summer'"),
        "scalar",
        _vals("on_time_rate"),
    ))
    add(_q(
        "What is the summer cancellation rate for flights departing DFW?",
        "medium",
        "seasonality",
        _scalar("DFW", "cancellation_rate", CANCEL, "origin = 'DFW' AND season = 'summer'"),
        "scalar",
        _vals("cancellation_rate"),
    ))
    add(_q(
        "Which month has the lowest systemwide on-time arrival rate?",
        "medium",
        "seasonality",
        _rank(MONTH_LABEL, "year, month", "on_time_rate", ONTIME, "operated", None, False, 1),
        "ranking",
        _vals("on_time_rate"),
    ))
    add(_q(
        "Which month has the highest systemwide cancellation rate?",
        "medium",
        "seasonality",
        _rank(MONTH_LABEL, "year, month", "cancellation_rate", CANCEL, "TRUE", None, True, 1),
        "ranking",
        _vals("cancellation_rate"),
    ))
    add(_q(
        "At MIA, which season has the highest on-time arrival rate for departures?",
        "medium",
        "seasonality",
        _rank("season", "season", "on_time_rate", ONTIME, "operated AND origin = 'MIA'", None, True, 1),
        "ranking",
        _vals("on_time_rate"),
    ))
    add(_q(
        "What are the winter and summer on-time arrival rates for flights departing DEN?",
        "medium",
        "seasonality",
        _pair("on_time_rate", ONTIME, [
            ("winter", "operated AND origin = 'DEN' AND season = 'winter'"),
            ("summer", "operated AND origin = 'DEN' AND season = 'summer'"),
        ]),
        "set",
        _vals("on_time_rate"),
    ))
    add(_q(
        "In July 2026, which airline is most on time out of ATL?",
        "hard",
        "seasonality",
        sql_atl_july_2026(),
        "ranking",
        _vals("on_time_rate", "operated_flights"),
        notes=(
            "Batch 4 general rule, applied to July 2026 at ATL. Literal winner after 100 operated flights, "
            "plus the major carriers that month: top 3 by volume, extended until they cover about 80 percent of operated departures. "
            "Each row has the on-time rate and the flight count. SkyWest is labeled SkyWest (flies for several airlines). "
            "This replaces the earlier cut that always listed American, Delta, and United. Daniel approved this question on 2026-10-13."
        ),
        ambiguous=True,
        interpretation=(
            "I read this as July 2026 departures from ATL: the literal on-time winner, and the major carriers at ATL that month."
        ),
        alternatives="Literal winner, plus major carriers at ATL in July 2026, each with an on-time rate and a flight count.",
        follow_up="Want American, Delta, and United at ATL for the full year, with and without operating partners?",
    ))
    add(_q(
        "What is the December 2025 systemwide on-time arrival rate?",
        "easy",
        "seasonality",
        _scalar("2025-12", "on_time_rate", ONTIME, "operated AND year = 2025 AND month = 12"),
        "scalar",
        _vals("on_time_rate"),
    ))
    add(_q(
        "Which on-time arrival rate is lower: 2025-11-26, the Wednesday before Thanksgiving 2025, or Wednesdays in October 2025? Return both rates.",
        "hard",
        "seasonality",
        _pair("on_time_rate", ONTIME, [
            ("2025-11-26", "operated AND flight_date = DATE '2025-11-26'"),
            ("2025-10-wednesdays", "operated AND year = 2025 AND month = 10 AND day_of_week = 3"),
        ]),
        "set",
        _vals("on_time_rate"),
        notes="Thanksgiving 2025 fell on Thursday, November 27. The comparison day is Wednesday, November 26, against every Wednesday in October 2025.",
    ))

    for code, name in [("AA", "American"), ("WN", "Southwest"), ("DL", "Delta"), ("UA", "United"), ("B6", "JetBlue")]:
        add(_q(
            f"How much of {name}'s lateness is its own fault versus weather?",
            "medium",
            "delay_causes",
            _own_fault(code),
            "set",
            _vals("share"),
            notes=(
                f"Own fault is the draft convention: carrier delay plus late-aircraft delay, "
                f"as a share of the five BTS cause minutes, on late arrivals of reporting carrier {code}. "
                "Weather is only the weather-delay cause."
            ),
        ))
    add(_q(
        "Systemwide, what share of late-arrival minutes falls on each of the five BTS delay causes?",
        "medium",
        "delay_causes",
        _cause_parts("late_arrival"),
        "set",
        _vals("share"),
    ))
    add(_q(
        "Which BTS delay cause accounts for the largest share of late-arrival minutes systemwide?",
        "easy",
        "delay_causes",
        f"SELECT * FROM (\n{_cause_parts('late_arrival')}\n) causes\nORDER BY share DESC, label ASC\nLIMIT 1",
        "ranking",
        _vals("share"),
    ))
    add(_q(
        "For late departures from SFO, what share of late-arrival minutes is NAS delay?",
        "medium",
        "delay_causes",
        _scalar("SFO", "share", "SUM(nas_delay) * 1.0 / NULLIF(SUM(cause_minutes), 0)", "late_arrival AND origin = 'SFO'"),
        "scalar",
        _vals("share"),
        notes="Late arrivals among flights that departed SFO, not flights that arrived at SFO.",
    ))
    add(_q(
        "Which airline has the highest share of its late-arrival minutes labeled carrier delay, not counting late aircraft?",
        "hard",
        "delay_causes",
        _rank(
            "airline_code",
            "airline_code",
            "share",
            "SUM(carrier_delay) * 1.0 / NULLIF(SUM(cause_minutes), 0)",
            "late_arrival",
            f"COUNT(*) >= {MIN_LATE_FLIGHTS}",
            True,
            1,
        ),
        "ranking",
        _vals("share"),
        notes="Carrier delay only. Late-aircraft minutes stay in the denominator and are not treated as carrier delay. The winner is a share of coded minutes, not an explanation of the operation.",
        why_limit=WHY_LIMIT,
        follow_up="The BTS codes cannot say why those minutes were coded carrier. Want the five-cause split for that airline?",
        alternatives="The figure is the carrier-delay share only. Weather, NAS, security, and late aircraft stay out of the numerator.",
    ))
    add(_q(
        "On DFW to ORD, what are the late-aircraft share and the weather share of late-arrival minutes?",
        "medium",
        "delay_causes",
        """SELECT 'late_aircraft' AS label,
       SUM(late_aircraft_delay) * 1.0 / NULLIF(SUM(cause_minutes), 0) AS share
FROM flights_v
WHERE late_arrival AND origin = 'DFW' AND dest = 'ORD'
UNION ALL
SELECT 'weather' AS label,
       SUM(weather_delay) * 1.0 / NULLIF(SUM(cause_minutes), 0) AS share
FROM flights_v
WHERE late_arrival AND origin = 'DFW' AND dest = 'ORD'""",
        "set",
        _vals("share"),
    ))
    add(_q(
        "For American flights out of DFW, what share of late-arrival minutes is airline-controlled?",
        "medium",
        "delay_causes",
        _scalar("AA", "share", "SUM(airline_controlled_minutes) * 1.0 / NULLIF(SUM(cause_minutes), 0)", "late_arrival AND airline_code = 'AA' AND origin = 'DFW'"),
        "scalar",
        _vals("share"),
    ))
    add(_q(
        "Systemwide, what share of cancelled flights carry cancellation code B (weather)?",
        "easy",
        "delay_causes",
        _scalar("systemwide", "share", "AVG(CASE WHEN cancellation_code = 'B' THEN 1.0 ELSE 0.0 END)", "cancelled"),
        "scalar",
        _vals("share"),
    ))
    add(_q(
        "Which airline has the highest share of its cancellations coded A (carrier)?",
        "hard",
        "delay_causes",
        _rank(
            "airline_code",
            "airline_code",
            "share",
            "AVG(CASE WHEN cancellation_code = 'A' THEN 1.0 ELSE 0.0 END)",
            "cancelled",
            f"COUNT(*) >= {MIN_CANCELLATIONS}",
            True,
            1,
        ),
        "ranking",
        _vals("share"),
        notes="Cancellation code A means the reporting carrier coded the cancellation as its own. The minimum is 200 cancellations. The code does not say what failed.",
        why_limit=WHY_LIMIT,
        follow_up="Code A is the carrier's own cancellation code. It cannot say why the flight was cancelled. Want the A, B, C, and D shares side by side?",
        alternatives="This is the share of cancellations coded A, among airlines with at least 200 cancellations.",
    ))
    add(_q(
        "For winter departures from ORD, what share of late-arrival minutes is weather delay?",
        "hard",
        "delay_causes",
        _scalar("ORD", "share", "SUM(weather_delay) * 1.0 / NULLIF(SUM(cause_minutes), 0)", "late_arrival AND origin = 'ORD' AND season = 'winter'"),
        "scalar",
        _vals("share"),
    ))
    add(_q(
        "Compare the airline-controlled share of late minutes for American and Southwest, systemwide.",
        "medium",
        "delay_causes",
        """SELECT airline_code AS label,
       SUM(airline_controlled_minutes) * 1.0 / NULLIF(SUM(cause_minutes), 0) AS share
FROM flights_v
WHERE late_arrival AND airline_code IN ('AA', 'WN')
GROUP BY airline_code""",
        "set",
        _vals("share"),
    ))
    add(_q(
        "Systemwide, what share of late-arrival minutes is security delay?",
        "easy",
        "delay_causes",
        _scalar("security", "share", "SUM(security_delay) * 1.0 / NULLIF(SUM(cause_minutes), 0)", "late_arrival"),
        "scalar",
        _vals("share"),
    ))

    add(_q(
        "Which airline is best out of DFW?",
        "hard",
        "ambiguous_or_trick",
        sql_best_out_of_dfw(),
        "set",
        _vals("on_time_rate"),
        notes="Best means the highest on-time arrival rate after 100 operated flights. reporting_best is the best reporting carrier. In this extract that row is Envoy (American Eagle). mainline_best is the best mainline carrier. brand_* rows group single-partner families. SkyWest and Republic stay unassigned because the file has no marketing-airline column.",
        ambiguous=True,
        interpretation="I read best as on-time arrival rate, then I also show the best mainline carrier and the brand rollup.",
        follow_up="Want the same three cuts at another hub?",
        alternatives="Envoy (American Eagle) as the best reporting carrier, the best mainline carrier, and each brand at DFW with at least 100 flights.",
    ))
    add(_q(
        "What is the average delay at ORD?",
        "hard",
        "ambiguous_or_trick",
        _scalar("ORD", "avg_arrival_delay", "AVG(arr_delay)", "operated AND origin = 'ORD'"),
        "scalar",
        _vals("avg_arrival_delay"),
        notes="Ambiguous. Candidate interpretation is the mean signed arrival delay of operated departures from ORD, not departures-only delay, not median, and not late-flights-only.",
        ambiguous=True,
    ))
    add(_q(
        "Are flights between DFW and ORD on time? Return the on-time arrival rate.",
        "hard",
        "ambiguous_or_trick",
        sql_dfw_ord_both_ways(),
        "set",
        _vals("on_time_rate"),
        notes="Between means both directions. The answer names DFW-ORD, ORD-DFW, and the combined figure. Each row has an on-time arrival rate and a flight count.",
        ambiguous=True,
        interpretation="I read between DFW and ORD as both directions, and I list each direction on its own plus the combined rate.",
        follow_up="Want the same three figures by airline?",
        alternatives="DFW to ORD, ORD to DFW, and both directions combined.",
    ))
    add(_q(
        "How late is American?",
        "hard",
        "ambiguous_or_trick",
        sql_american_lateness(),
        "scalar",
        _vals("on_time_rate", "avg_arrival_delay"),
        notes="Ambiguous. The literal reading is reporting carrier AA, not the American brand. The answer gives the systemwide on-time arrival rate and the mean signed arrival delay in minutes. Phrasing variants of this question use the same SQL.",
        ambiguous=True,
        interpretation="I read this as reporting-carrier American Airlines: on-time arrival rate and mean signed arrival delay in minutes. It does not roll up Envoy, PSA, or other partners.",
        follow_up="Want the same two figures for the American brand, with Envoy and PSA and without them?",
        alternatives="On-time arrival rate and mean signed arrival delay, both for reporting carrier AA.",
    ))
    add(_q(
        "Which airport is the worst?",
        "hard",
        "ambiguous_or_trick",
        sql_worst_airport(),
        "set",
        _vals("on_time_rate"),
        notes="Two cuts. overall_worst is the lowest on-time arrival rate among airports with at least 5,000 operated departures. large_hub_worst uses airports with at least 150,000 operated departures. The airport column names the airport. A large hub here is a volume bucket, not an FAA passenger-share designation, because this extract has no passenger counts.",
        ambiguous=True,
        interpretation="I read worst as the lowest on-time arrival rate, and I give both the overall airport and the worst large airport.",
        follow_up="Want the five worst large airports, each with its on-time rate?",
        alternatives="Overall worst airport after 5,000 flights, and worst airport among those with 150,000 or more departures.",
    ))
    add(_q(
        "Does weather explain most delays? Return the weather share of late-arrival minutes, the NAS share, and 1 if the weather share is above one half.",
        "hard",
        "ambiguous_or_trick",
        """SELECT 'systemwide' AS label,
       SUM(weather_delay) * 1.0 / NULLIF(SUM(cause_minutes), 0) AS weather_share,
       SUM(nas_delay) * 1.0 / NULLIF(SUM(cause_minutes), 0) AS nas_share,
       CASE
           WHEN SUM(weather_delay) * 1.0 / NULLIF(SUM(cause_minutes), 0) > 0.5 THEN 1
           ELSE 0
       END AS weather_is_majority
FROM flights_v
WHERE late_arrival""",
        "scalar",
        _vals("weather_share", "nas_share", "weather_is_majority"),
        notes="Ambiguous word: weather. Candidate interpretation uses the BTS weather-delay cause only, and puts the NAS share beside it so a weather-versus-system reading stays visible. Pending review.",
        ambiguous=True,
    ))
    add(_q(
        "Including single-partner regional affiliates, how do American and Delta compare on systemwide on-time arrival rate? Return both rates.",
        "hard",
        "ambiguous_or_trick",
        sql_partners_and_mainline(),
        "set",
        _vals("on_time_rate"),
        notes="Four rows: American and Delta, each with operating partners and without them. Partners are the single-partner codes only (Envoy, PSA, and Piedmont for American; Endeavor for Delta). Piedmont and Endeavor have no rows in this extract. SkyWest and Republic are not assigned: the reporting-carrier file has no marketing-airline column.",
        alternatives="Mainline only, and mainline plus single-partner operating partners, for American and for Delta.",
        follow_up="Want United the same way? Its regional codes have no rows in this extract, so the two United figures match.",
    ))
    add(_q(
        "Which is more on time out of Dallas, American or Southwest? Return both on-time arrival rates.",
        "hard",
        "ambiguous_or_trick",
        _rank(
            "airline_code",
            "airline_code",
            "on_time_rate",
            ONTIME,
            "operated AND origin IN ('DFW', 'DAL') AND airline_code IN ('AA', 'WN')",
            None,
            True,
            None,
        ),
        "set",
        _vals("on_time_rate"),
        notes="Dallas with no airport code means DFW and DAL pooled. The answer states that pool. DFW alone would be a different cut. Southwest's Dallas flights in this extract are at DAL. American's are mostly at DFW.",
        ambiguous=True,
        interpretation="I read Dallas as DFW and DAL combined, and I give both airlines' on-time arrival rates.",
        follow_up="Is a lower on-time rate the dice of busy hubs, or the airline after the airport is held fixed?",
        alternatives="Both rates are in the pooled DFW and DAL departures. DFW alone is a different cut.",
    ))
    add(_q(
        "What was the on-time rate yesterday?",
        "hard",
        "ambiguous_or_trick",
        None,
        "refusal",
        notes="The extract is a historical monthly file, not a live feed. The analyst should refuse rather than substitute the latest date in the file.",
    ))
    add(_q(
        "Which airline has the most delays?",
        "hard",
        "ambiguous_or_trick",
        sql_most_delays(),
        "ranking",
        _vals("late_flights", "late_rate"),
        notes="Most delays means the greatest count of late arrivals. The same row also has the late rate, so the count is not the only figure. The minimum is 5,000 operated flights. Both numbers are for that airline.",
        ambiguous=True,
        interpretation="I read most delays as the greatest count of late arrivals, and I also give that airline's late rate.",
        follow_up="Want the airline with the highest late rate, which is a different ranking?",
        alternatives="Late-arrival count and late-arrival rate for the airline with the most late arrivals.",
    ))
    add(_q(
        "Are red-eye flights less on time than other flights? Return the night-departure on-time arrival rate and the rate for all other departures.",
        "hard",
        "ambiguous_or_trick",
        _pair("on_time_rate", ONTIME, [
            ("night", "operated AND dep_time_bucket = 'night'"),
            ("other", "operated AND dep_time_bucket <> 'night'"),
        ]),
        "set",
        _vals("on_time_rate"),
        notes="Ambiguous word: red-eye. Candidate interpretation is the night bucket, 21:00 through 04:59, against every other operated departure.",
        ambiguous=True,
    ))
    add(_q(
        "If late aircraft does not count as the airline's fault, what share of systemwide late-arrival minutes is the airline's fault?",
        "hard",
        "ambiguous_or_trick",
        _scalar("systemwide", "carrier_only_share", "SUM(carrier_delay) * 1.0 / NULLIF(SUM(cause_minutes), 0)", "late_arrival"),
        "scalar",
        _vals("carrier_only_share"),
        notes="The question overrides the default own-fault convention and keeps late-aircraft minutes out of the numerator.",
    ))
    add(_q(
        "What is the passenger-weighted on-time arrival rate at ATL?",
        "hard",
        "ambiguous_or_trick",
        None,
        "refusal",
        notes="The on-time extract has no passenger counts. The analyst should refuse rather than return an unweighted rate.",
    ))
    add(_q(
        "Rank the five most on-time airlines by on-time departure rather than arrival.",
        "hard",
        "ambiguous_or_trick",
        _rank(
            "airline_code",
            "airline_code",
            "on_time_departure_rate",
            DEP_ONTIME,
            "on_time_departure IS NOT NULL",
            f"COUNT(*) >= {MIN_AIRLINE_SYSTEMWIDE}",
            True,
            5,
        ),
        "ranking",
        _vals("on_time_departure_rate"),
        notes="Departure metric, on the flights that have a departure delay. This is not the arrival standard.",
    ))
    add(_q(
        "What share of scheduled flights arrive on time if cancelled and diverted flights count as not on time?",
        "hard",
        "ambiguous_or_trick",
        _scalar(
            "systemwide",
            "on_time_rate",
            "AVG(CASE WHEN (NOT cancelled AND NOT diverted AND arr_delay < 15) THEN 1.0 ELSE 0.0 END)",
            "TRUE",
        ),
        "scalar",
        _vals("on_time_rate"),
        notes="Overrides the operated-flight denominator. Every scheduled row is in the denominator. Only an arrival under 15 minutes counts as on time.",
    ))
    add(_q(
        "Is SkyWest part of the American single-partner family used in this project? Return 1 if it is and 0 if it is not.",
        "hard",
        "ambiguous_or_trick",
        """SELECT 'OO' AS label,
       CASE WHEN COUNT(*) > 0 THEN 1 ELSE 0 END AS included
FROM carrier_families
WHERE family_code = 'AA' AND airline_code = 'OO'""",
        "scalar",
        _vals("included"),
        notes="SkyWest flies for more than one major, so the draft family table leaves it out. The expected figure is 0.",
    ))
    add(_q(
        "Including Envoy and PSA with American, what is the American brand's systemwide on-time arrival rate? Leave SkyWest and Republic out.",
        "medium",
        "airline_reliability",
        _scalar(
            "American",
            "on_time_rate",
            ONTIME,
            "operated AND airline_code IN ('AA', 'MQ', 'OH', 'PT')",
        ),
        "scalar",
        _vals("on_time_rate"),
        notes="Brand rollup for the dice-or-airline note. American is the reporting codes that fly only for American in this project: AA, MQ, OH, and PT. Piedmont has no rows in this extract. SkyWest and Republic fly for more than one brand, and the reporting-carrier file has no marketing-airline column, so they stay out. Pending review.",
    ))
    add(_q(
        "What is the on-time arrival rate for departures from airports with at least 150,000 operated departures, and for airports with 10,000 to 49,999? Return both.",
        "medium",
        "airport_reliability",
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
        "set",
        _vals("on_time_rate"),
        notes="Volume cut for the dice-or-airline note. Buckets are the origin airport's operated departures in this window. The two middle buckets are not both returned. Pending review.",
    ))
    add(_q(
        "At DFW, what is the on-time arrival rate for the American brand and for every other carrier combined? Return both.",
        "hard",
        "airport_reliability",
        """SELECT CASE WHEN airline_code IN ('AA', 'MQ', 'OH', 'PT') THEN 'American' ELSE 'other' END AS label,
       AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END) AS on_time_rate
FROM flights_v
WHERE operated AND origin = 'DFW'
GROUP BY 1""",
        "set",
        _vals("on_time_rate"),
        notes="DFW spotlight. American is AA, MQ, OH, and PT. Other is every remaining reporting carrier at DFW, including SkyWest and Republic. This is the airport average, not the shared-route comparison. Pending review.",
    ))
    add(_q(
        "What share of the American brand's late-arrival minutes is airline-controlled, counting carrier delay and late aircraft together?",
        "medium",
        "delay_causes",
        _scalar(
            "American",
            "share",
            "SUM(airline_controlled_minutes) * 1.0 / NULLIF(SUM(cause_minutes), 0)",
            "late_arrival AND cause_minutes > 0 AND airline_code IN ('AA', 'MQ', 'OH', 'PT')",
        ),
        "scalar",
        _vals("share"),
        notes="Own-fault share for the American brand rollup. Airline-controlled minutes are carrier plus late aircraft. SkyWest and Republic are excluded. Pending review.",
        why_limit=WHY_LIMIT,
    ))
    add(_q(
        "What is American's systemwide on-time arrival rate with its single-partner operating partners, and without them? Return both.",
        "medium",
        "airline_reliability",
        sql_american_brand_pair(),
        "set",
        _vals("on_time_rate"),
        notes="American Eagle in this extract is Envoy (MQ) and PSA (OH). Piedmont (PT) has no rows. SkyWest and Republic are not assigned to American: the July 2026 file has Reporting_Airline and no marketing-airline column.",
        alternatives="Mainline AA only, and AA plus Envoy and PSA.",
        follow_up="Want Delta and United on the same with-and-without cut?",
    ))
    add(_q(
        "Is American usually late?",
        "hard",
        "ambiguous_or_trick",
        sql_american_lateness(),
        "scalar",
        _vals("on_time_rate", "avg_arrival_delay"),
        notes="Phrasing variant of 'How late is American?'. Same SQL, same figures: reporting carrier AA, on-time arrival rate and mean signed arrival delay in minutes.",
        ambiguous=True,
        interpretation="I read this as reporting-carrier American Airlines: on-time arrival rate and mean signed arrival delay in minutes. It does not roll up Envoy, PSA, or other partners.",
        follow_up="Want the same two figures for the American brand, with Envoy and PSA and without them?",
        alternatives="On-time arrival rate and mean signed arrival delay, both for reporting carrier AA.",
    ))
    add(_q(
        "American's on-time record?",
        "hard",
        "ambiguous_or_trick",
        sql_american_lateness(),
        "scalar",
        _vals("on_time_rate", "avg_arrival_delay"),
        notes="Phrasing variant of 'How late is American?'. Same SQL, same figures: reporting carrier AA, on-time arrival rate and mean signed arrival delay in minutes.",
        ambiguous=True,
        interpretation="I read this as reporting-carrier American Airlines: on-time arrival rate and mean signed arrival delay in minutes. It does not roll up Envoy, PSA, or other partners.",
        follow_up="Want the same two figures for the American brand, with Envoy and PSA and without them?",
        alternatives="On-time arrival rate and mean signed arrival delay, both for reporting carrier AA.",
    ))
    add(_q(
        "Is a lower on-time rate the dice of busier airports, or the airline after the origin airport is held fixed? Return the on-time rate for the largest airports and for midsize airports, and the same-airport gap for American, Delta, United, and Southwest.",
        "hard",
        "ambiguous_or_trick",
        sql_dice_or_airline(),
        "set",
        _vals("figure"),
        notes="Largest airports have at least 150,000 operated departures. Midsize airports have 10,000 to 49,999. The same-airport gap is the flight-weighted difference between that brand and the other flights at the same origin, each side at least 100 flights. American includes Envoy and PSA. SkyWest and Republic stay in the other-flights side because they cannot be assigned to a marketing brand. A negative gap means the brand is behind the other flights at the same airports. This is a description, not a cause.",
        ambiguous=True,
        interpretation="I read this as two cuts: airport size, and the airline after the origin airport is held fixed.",
        follow_up="Want the hour-of-day slope next to the airport-size slope? Evening departures move the rate much more than hub size does.",
        alternatives="Largest-airport rate, midsize-airport rate, and a same-airport gap for American, Delta, United, and Southwest.",
    ))
    add(_q(
        "What can the BTS delay and cancellation codes tell us about why a flight was late or cancelled? Return the systemwide share of late-arrival minutes for each delay cause.",
        "medium",
        "delay_causes",
        _cause_parts("late_arrival"),
        "set",
        _vals("share"),
        notes="The five shares are what the codes can tell us. They cannot name the broken part, the crew, or the decision.",
        why_limit=WHY_LIMIT,
        follow_up="The codes cannot say why. Want the same split for one airline?",
        alternatives="Carrier, weather, NAS, security, and late aircraft, as shares of late-arrival minutes.",
    ))

    return _apply_review(questions)


# Question text -> Daniel's call on 2026-10-11. Unlisted questions stay DRAFT.
_REVIEW = {
    "Which airline has the lowest median arrival delay systemwide?": {
        "status": "APPROVED",
        "follow_up": "Want the same median across airline brands, for example American including American Eagle?",
    },
    "Which airport has the largest morning-minus-evening on-time arrival gap?": {"status": "APPROVED"},
    "Which month has the highest systemwide cancellation rate?": {"status": "APPROVED"},
    "Which on-time arrival rate is lower: 2025-11-26, the Wednesday before Thanksgiving 2025, or Wednesdays in October 2025? Return both rates.": {"status": "APPROVED"},
    "How late is American?": {"status": "APPROVED"},
    "Does weather explain most delays? Return the weather share of late-arrival minutes, the NAS share, and 1 if the weather share is above one half.": {
        "status": "APPROVED",
        "why_limit": WHY_LIMIT,
    },
    "Which airline is best out of DFW?": {"status": "APPROVED"},
    "What is the average delay at ORD?": {"status": "APPROVED"},
    "Are flights between DFW and ORD on time? Return the on-time arrival rate.": {"status": "APPROVED"},
    "Which airport is the worst?": {"status": "APPROVED"},
    "Which is more on time out of Dallas, American or Southwest? Return both on-time arrival rates.": {"status": "APPROVED"},
    "Which airline has the most delays?": {"status": "APPROVED"},
    "Are red-eye flights less on time than other flights? Return the night-departure on-time arrival rate and the rate for all other departures.": {"status": "APPROVED"},
    "Which five routes have the lowest on-time arrival rate?": {"status": "APPROVED"},
    "Which five routes have the highest cancellation rate?": {"status": "APPROVED"},
    "What are the five routes out of DFW with the highest average signed arrival delay?": {"status": "APPROVED"},
    "At ORD, what are the on-time arrival rates for departures in hour 06 and hour 18?": {"status": "APPROVED"},
    "At DEN, what are the on-time arrival rates for departures in hour 08 and hour 20?": {"status": "APPROVED"},
    "In July 2026, which airline is most on time out of ATL?": {"status": "APPROVED"},
    "Which airline has the highest share of its late-arrival minutes labeled carrier delay, not counting late aircraft?": {"status": "APPROVED"},
    "Which airline has the highest share of its cancellations coded A (carrier)?": {"status": "APPROVED"},
    "For winter departures from ORD, what share of late-arrival minutes is weather delay?": {"status": "APPROVED"},
    "Including single-partner regional affiliates, how do American and Delta compare on systemwide on-time arrival rate? Return both rates.": {"status": "APPROVED"},
    # Batch 4, 2026-10-11. Daniel rechecked these on 2026-10-13 and approved them.
    # q022 (evening worst airports) and q025 (five worst airports) were "fine as is" and stay DRAFT.
    "Which airline is most on time out of DFW?": {"status": "APPROVED"},
    "Which airline is most on time out of ORD?": {"status": "APPROVED"},
    "Which airline is most on time out of ATL?": {"status": "APPROVED"},
    "Which airline is most on time out of DEN?": {"status": "APPROVED"},
    "Which airline is most on time out of LAX?": {"status": "APPROVED"},
    "Which airline is most on time out of LGA?": {"status": "APPROVED"},
    "Which airline is most on time out of BOS?": {"status": "APPROVED"},
    "Which airline is most on time out of SEA?": {"status": "APPROVED"},
    "Which airline is most on time out of EWR?": {"status": "APPROVED"},
    "Which airline is most on time out of MIA?": {"status": "APPROVED"},
    "Which five airlines have the highest systemwide on-time arrival rate?": {
        "status": "APPROVED",
        "required_phrases": ["Hawaiian benefits from island weather."],
    },
}


def _apply_review(questions: list[dict]) -> list[dict]:
    for item in questions:
        extra = _REVIEW.get(item["question"])
        if not extra:
            continue
        item.update(extra)
    return questions
