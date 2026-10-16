"""Metric definitions shared by the schema notes, the analyst briefing, and the rules stub.

The question bank is status DRAFT. These definitions are the candidate
interpretation used to compute candidate answers. They are not an approved
standard until Daniel reviews them.
"""

from __future__ import annotations

# DOT on-time standard: arrival less than 15 minutes late. A delay of exactly
# 15 minutes is late. BTS ArrDel15 uses the same cut.
ON_TIME_THRESHOLD_MINUTES = 15
SEVERE_LATE_MINUTES = 60

# Minimum samples so a handful of flights cannot win a ranking.
MIN_AIRLINE_AT_AIRPORT = 100
MIN_AIRLINE_SYSTEMWIDE = 5000
MIN_AIRPORT = 5000
MIN_AIRPORT_BUCKET = 2000
MIN_ROUTE = 500
MIN_ROUTE_FROM_AIRPORT = 200
MIN_LATE_FLIGHTS = 1000
MIN_CANCELLATIONS = 200
MIN_HOUR_SYSTEM = 10000
MIN_BUCKET_EACH = 500

# Scheduled local departure hour, inclusive.
MORNING_HOURS = (5, 8)  # 05:00-08:59
MIDDAY_HOURS = (9, 16)  # 09:00-16:59
EVENING_HOURS = (17, 20)  # 17:00-20:59
# Night is every other hour: 21:00-04:59.

# Tolerances for the eval harness. Correct SQL matches far more tightly.
# The tolerance absorbs fraction-versus-percent formatting, not a different filter.
TOLERANCE_FRACTION = 0.005
TOLERANCE_MINUTES = 0.05
TOLERANCE_PERCENTAGE_POINTS = 0.05

# Sentences that must appear in docs/definitions.md and in the analyst briefing.
DOC_PHRASES = [
    "A flight is on time when its arrival delay is under 15 minutes.",
    "The on-time arrival rate is the share of operated flights that arrive under 15 minutes late, not the share that are late.",
    "Operated flights are not cancelled, not diverted, and have a non-null arrival delay.",
    "Cancelled and diverted flights are left out of the on-time arrival denominator.",
    "On-time departure means the flight was not cancelled, the departure delay is non-null, and that delay is under 15 minutes.",
    "A late arrival is an operated flight whose arrival delay is 15 minutes or more.",
    "Severe late means an operated arrival delay of 60 minutes or more.",
    "Evening is a scheduled local departure from 17:00 through 20:59.",
    "Morning is a scheduled local departure from 05:00 through 08:59.",
    "Midday is a scheduled local departure from 09:00 through 16:59.",
    "Night is a scheduled local departure from 21:00 through 04:59.",
    "Winter is December, January, and February. Spring is March through May. Summer is June through August. Fall is September through November.",
    "Day of week uses the BTS coding: 1 is Monday and 7 is Sunday.",
    "Airline-controlled delay minutes are carrier delay plus late-aircraft delay.",
    "Weather delay is only the BTS weather-delay cause, not weather that the NAS bucket absorbed.",
    "Own fault means airline-controlled delay minutes unless the question explicitly sets late aircraft aside.",
    "Cancellation code A is carrier, B is weather, C is national air system, and D is security.",
    "Rankings of airlines at one airport require at least 100 operated flights.",
    "Systemwide airline rankings require at least 5000 operated flights.",
    "Airport rankings require at least 5000 operated departures, and evening or morning airport rankings require at least 2000 operated departures in that bucket.",
    "Route rankings require at least 500 operated flights, or at least 200 when the route is limited to departures from one named airport.",
    "Delay-cause rankings of airlines require at least 1000 late arrivals. Cancellation-cause rankings require at least 200 cancellations.",
    "The reporting carrier is the default airline. It is not the marketing airline and it is not the whole brand family.",
    "Single-partner regional families are American (AA, MQ, OH, PT), Delta (DL, 9E), United (UA, C5, G7, ZW), and Alaska (AS, QX). SkyWest (OO), Republic (YX), and Mesa (YV) are multi-partner and stay out of those families.",
    "A named direction such as DFW to ORD is one way. The phrase between DFW and ORD means both directions combined.",
    "Dallas with no airport code means DFW and DAL combined. Dallas/Fort Worth or DFW means DFW only.",
    "Most delays means the greatest count of late arrivals, not the highest late rate.",
    "Average delay means the mean signed arrival delay of operated flights. Early arrivals pull it down.",
    "Best airline and worst airport use the on-time arrival rate, after the minimum-sample rule.",
    "Red-eye means the night bucket.",
    "Clock times in the extract are local airport times, not UTC.",
    "The extract has no passenger counts, so a passenger-weighted rate cannot be computed.",
    "The extract is not a live feed. Questions about yesterday, tomorrow, or the current hour cannot be answered from it.",
    "Ties in a ranking break by label ascending.",
    "Answer the question literally first, then offer one better follow-up question.",
    "When a number is given for one listed item, give that number for every listed item, with its unit.",
    "An ambiguous question gets the literal answer, a stated reading, and the natural alternative cuts.",
    "Name every route, airport, and airline explicitly. A route named in one direction is not the reverse.",
    "BTS delay codes are carrier, weather, national air system, security, and late aircraft. They are coarse and self-reported, and they often cannot say why.",
    "A ranking states its minimum-flights rule. A sample under that floor does not win.",
    "The same question asked in different words gets the same figures.",
    "Brand figures are shown with operating partners and without them. This extract has no marketing-airline column, so SkyWest and Republic stay unassigned.",
    "A question that asks which airline is most on time out of an airport returns the literal winner and the major carriers at that airport. Major carriers are the top 3 by operated departures, plus any further carriers needed so the listed majors cover about 80 percent of operated departures. Each of those rows has the on-time arrival rate and the flight count. SkyWest is labeled SkyWest (flies for several airlines).",
    "When Hawaiian is in a systemwide on-time ranking, the answer notes that Hawaiian benefits from island weather.",
    "Do not put a window function in a WHERE clause. Put the window in a CTE, or filter the window with QUALIFY.",
    "The on_time_rate column is the mean of on_time_arrival. It is never the late share and never one minus the on-time share.",
    "On a most-on-time list, put the literal winner first. Order the other required carriers by operated_flights descending. Do not sort the whole list by volume.",
    "When a question asks which of two directions is more on time, return both ORIGIN-DEST rows with the same on-time arrival rate metric.",
    "When the question says between two airports, return each direction as ORIGIN-DEST and a both-directions row. Each row has the on-time arrival rate.",
    "When a question names two airlines, return a row for each, even when one airline has zero flights.",
    "A most-delays row includes late_flights and late_rate. late_rate is late arrivals divided by operated flights. It is a fraction, not 1, and it is not the on-time rate. Do not restrict the query to late flights only.",
    "How late is American, Is American usually late, and American's on-time record all return on_time_rate and avg_arrival_delay for reporting carrier AA. A flight count does not stand in for avg_arrival_delay.",
    "Average delay is AVG(arr_delay) over operated flights. It is not the mean of late flights only, not departure delay, and not the median, unless the question asks for that.",
    "Every THEN belongs to a CASE that has WHEN and END. Prefer the boolean columns operated, on_time_arrival, and late_arrival. Write arr_delay < 15 only inside a CASE expression.",
    "A VALUES list names its columns, for example FROM (VALUES ('AA'), ('WN')) AS codes(airline_code).",
    "Return every required carrier, direction, and row. SkyWest and Republic stay in the answer when the rule includes them. A named direction is its own row.",
    "A month slice, such as July 2026, still returns the literal winner and the major carriers in that slice. Filter the month inside the operated rows. Do not replace the carrier list with a single airline.",
    "Top 3 by operated departures is the start of the major-carrier list, not the end. Keep adding carriers by volume until the carriers already listed cover about 80 percent of operated departures. Use rate_rank = 1 OR vol_rank <= 3 OR (cum_flights - operated_flights) < 0.80 * airport_flights.",
    "The default on-time denominator is operated flights. A null on_time_arrival is not a zero. When the same query also returns a cancellation rate, keep every scheduled row and write AVG(CASE WHEN operated THEN CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END END). Do not divide on-time arrivals by scheduled flights. Only a question that says cancelled and diverted flights count as not on time puts every scheduled row in that denominator.",
    "flight_date, year, and month are columns of flights_v. flight_date is not a table. A single day is flight_date = DATE '2025-11-26'. origin and dest are columns of flights_v. airports.airport_code is the airport key. airports has no iata column and no origin column. Rank a named list of airports from flights_v with origin IN (...) and GROUP BY origin.",
    "status is DRAFT",
    "candidate answers pending Daniel's review",
]

# Spoken answer rules. The analyst briefing includes them, and the grader applies them.
GRADER_RUBRIC = (
    "Grade the spoken answer as well as the rows. "
    "An ambiguous question passes only when the answer has the literal figures, "
    "a stated reading that begins 'I read this as', and the expected alternative cuts, each with a number and a unit. "
    "If one listed item has a number, every listed item has that number, with its unit "
    "(percent, minutes, or flights). "
    "A follow-up question is an advisory nudge score. It does not change pass or fail. "
    "Rankings must state the minimum-flights rule. "
    "When BTS cause codes cannot say why, the answer says that plainly."
)

MINIMUM_RULE = (
    "Rankings use a minimum-flights rule so a small sample cannot win. "
    "An airline at one airport needs at least 100 operated flights. "
    "A systemwide airline ranking needs at least 5,000. "
    "An airport ranking needs at least 5,000 operated departures, or 2,000 in a morning or evening bucket. "
    "A route ranking needs at least 500 operated flights, or 200 when the route is limited to one origin. "
    "A delay-cause ranking needs at least 1,000 late arrivals. "
    "A cancellation-cause ranking needs at least 200 cancellations."
)

WHY_LIMIT = (
    "BTS delay codes are carrier, weather, national air system (NAS), security, and late aircraft. "
    "Cancellation code A is carrier, B is weather, C is national air system, and D is security. "
    "The codes are coarse and self-reported, and they are filled in only for arrivals that are 15 minutes or more late, or for cancellations. "
    "They cannot tell us the operational reason."
)

def sql_major_carriers(origin: str, extra_where: str = "") -> str:
    """Literal winner, plus majors: top 3 by volume, extended to about 80 percent.

    The briefing pastes this template with origin XXX. Reference questions call
    the same function so the 80 percent predicate cannot drift out of the prompt.
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


# Shape examples for the analyst briefing. No stored rates. Mirrored in docs/definitions.md.
SHAPE_EXAMPLES = f"""Answer-shape examples. Copy the column layout. These are patterns, not stored rates.

Most on time out of an airport. Copy this template. The window lives in the CTE. The outer WHERE keeps the winner, the top 3 by volume, and every further carrier still needed until the carriers already listed cover about 80 percent of operated departures. Stopping at vol_rank <= 3 drops carriers such as United or Southwest. The first row is the winner. Later rows follow by operated_flights descending.

{sql_major_carriers("XXX")}

Two named directions. Both rows, same metric.

SELECT 'ORD-LGA' AS label, AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END) AS on_time_rate
FROM flights_v
WHERE operated AND origin = 'ORD' AND dest = 'LGA'
UNION ALL
SELECT 'LGA-ORD' AS label, AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END) AS on_time_rate
FROM flights_v
WHERE operated AND origin = 'LGA' AND dest = 'ORD'

Between two airports. Each direction, plus both-directions.

SELECT 'DFW-ORD' AS label, AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END) AS on_time_rate, COUNT(*) AS n
FROM flights_v
WHERE operated AND origin = 'DFW' AND dest = 'ORD'
UNION ALL
SELECT 'ORD-DFW' AS label, AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END) AS on_time_rate, COUNT(*) AS n
FROM flights_v
WHERE operated AND origin = 'ORD' AND dest = 'DFW'
UNION ALL
SELECT 'both-directions' AS label, AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END) AS on_time_rate, COUNT(*) AS n
FROM flights_v
WHERE operated AND ((origin = 'DFW' AND dest = 'ORD') OR (origin = 'ORD' AND dest = 'DFW'))

Most delays. late_flights is the count. late_rate divides that count by operated flights. Keep the operated flights in the denominator.

SELECT airline_code AS label,
       SUM(CASE WHEN late_arrival THEN 1 ELSE 0 END) AS late_flights,
       AVG(CASE WHEN late_arrival THEN 1.0 ELSE 0.0 END) AS late_rate,
       COUNT(*) AS operated_flights
FROM flights_v
WHERE operated
GROUP BY airline_code
ORDER BY late_flights DESC

How late, usually late, or an on-time record. Both metrics, for reporting carrier AA. AVG(arr_delay) is the mean over operated flights.

SELECT 'AA' AS label,
       AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END) AS on_time_rate,
       AVG(arr_delay) AS avg_arrival_delay
FROM flights_v
WHERE operated AND airline_code = 'AA'

A VALUES list names its columns. AS r with no column list has no airline_code.

SELECT codes.airline_code AS label
FROM (VALUES ('AA'), ('WN')) AS codes(airline_code)

Two airlines on one route, with on-time rate, cancellation rate, and mean arrival delay together. Do not filter the whole query with WHERE operated: that drops the cancellation denominator. A null on_time_arrival stays null.

SELECT codes.airline_code AS label,
       AVG(CASE WHEN f.operated THEN CASE WHEN f.on_time_arrival THEN 1.0 ELSE 0.0 END END) AS on_time_rate,
       AVG(CASE WHEN f.cancelled THEN 1.0 ELSE 0.0 END) AS cancellation_rate,
       AVG(CASE WHEN f.operated THEN f.arr_delay END) AS avg_arrival_delay
FROM (VALUES ('DL'), ('WN')) AS codes(airline_code)
LEFT JOIN flights_v f
  ON f.airline_code = codes.airline_code
 AND f.origin = 'ATL'
 AND f.dest = 'LGA'
GROUP BY codes.airline_code

A named list of airports. origin is a column of flights_v. Do not read it from airports, and do not join airports on iata.

SELECT origin AS label,
       AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END) AS on_time_rate
FROM flights_v
WHERE operated AND origin IN ('ATL', 'DFW', 'DEN', 'ORD', 'CLT')
GROUP BY origin
ORDER BY on_time_rate DESC, origin ASC

A calendar day uses the flight_date column. There is no flight_date table.

SELECT '2025-11-26' AS label,
       AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END) AS on_time_rate
FROM flights_v
WHERE operated AND flight_date = DATE '2025-11-26'
"""


def briefing_block() -> str:
    """The definition block pasted into the analyst system prompt."""
    lines = "\n".join(f"- {phrase}" for phrase in DOC_PHRASES)
    return (
        "Use these definitions exactly. They are a draft convention for this project, "
        "not an industry statute beyond the DOT 15-minute rule called out below.\n"
        f"{lines}\n"
        "When a question ranks airlines, airports, or routes and does not give its own cutoff, "
        "apply the minimum-sample rules above.\n"
        "Identify entities in a column named label. Use IATA codes for airlines and airports, "
        "ORIGIN-DEST for a route (example DFW-ORD), Monday-Sunday for days, season names, "
        "cause names (carrier, weather, nas, security, late_aircraft), bucket names "
        "(morning, midday, evening, night), hour_HH for a clock hour, YYYY-MM for a month, "
        "and systemwide when the figure is not about one entity.\n"
        "Name metrics on_time_rate, on_time_departure_rate, cancellation_rate, diversion_rate, "
        "irregular_rate, avg_arrival_delay, median_arrival_delay, severe_late_rate, share, "
        "percentage_points, rate_gap, operated_flights, late_flights, carrier_only_share, "
        "weather_share, nas_share, or weather_is_majority, matching what the question asks.\n"
        "Return rates and shares as fractions from 0 to 1. Return one SELECT (or WITH ... SELECT). "
        "Query only flights_v, carrier_families, carriers, and airports.\n"
        f"{SHAPE_EXAMPLES}\n"
        f"Grader rubric: {GRADER_RUBRIC}"
    )
