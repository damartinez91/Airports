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
        f"Grader rubric: {GRADER_RUBRIC}"
    )
