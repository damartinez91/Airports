# Definitions

These are the draft conventions for this project. Candidate answers in `questions/bank.json` use them. They are not an approved standard. The bank status is DRAFT, candidate answers pending Daniel's review.

A flight is on time when its arrival delay is under 15 minutes.

The on-time arrival rate is the share of operated flights that arrive under 15 minutes late, not the share that are late.

That is the US Department of Transportation cut: a delay of exactly 15 minutes is late. The project applies it to arrival, not departure, unless a question says otherwise.

Operated flights are not cancelled, not diverted, and have a non-null arrival delay.

Cancelled and diverted flights are left out of the on-time arrival denominator.

On-time departure means the flight was not cancelled, the departure delay is non-null, and that delay is under 15 minutes.

A late arrival is an operated flight whose arrival delay is 15 minutes or more.

Severe late means an operated arrival delay of 60 minutes or more.

## Time of day and calendar

Clock times in the extract are local airport times, not UTC.

Evening is a scheduled local departure from 17:00 through 20:59.

Morning is a scheduled local departure from 05:00 through 08:59.

Midday is a scheduled local departure from 09:00 through 16:59.

Night is a scheduled local departure from 21:00 through 04:59.

Winter is December, January, and February. Spring is March through May. Summer is June through August. Fall is September through November.

Day of week uses the BTS coding: 1 is Monday and 7 is Sunday.

Red-eye means the night bucket.

## Delay causes

BTS reports five cause buckets, in minutes, on flights that arrive 15 minutes or more late: carrier, weather, national aviation system (NAS), security, and late aircraft. This project sums those five columns and uses the sum as the denominator.

Airline-controlled delay minutes are carrier delay plus late-aircraft delay.

Weather delay is only the BTS weather-delay cause, not weather that the NAS bucket absorbed.

Own fault means airline-controlled delay minutes unless the question explicitly sets late aircraft aside.

Cancellation code A is carrier, B is weather, C is national air system, and D is security.

A reader who says "weather" usually means something broader than `weather_delay`. The NAS bucket holds a lot of weather-driven airspace delay. The draft questions that say "weather" mean the BTS weather column, and the question that asks whether weather explains most delays also returns the NAS share so that choice stays visible.

## Who counts as the airline

The reporting carrier is the default airline. It is not the marketing airline and it is not the whole brand family.

Single-partner regional families are American (AA, MQ, OH, PT), Delta (DL, 9E), United (UA, C5, G7, ZW), and Alaska (AS, QX). SkyWest (OO), Republic (YX), and Mesa (YV) are multi-partner and stay out of those families.

In the loaded vintage, several of those regional codes do not appear at all (Endeavor, Piedmont, Horizon, CommuteAir, GoJet, Air Wisconsin). American's rollup in this file is mainline plus Envoy and PSA. Delta, United, and Alaska contribute mainline rows only. That is a fact about this extract, and the family list above is still the draft rule for the next month that does contain those carriers.

## Sample size, direction, and wording

Rankings of airlines at one airport require at least 100 operated flights.

Systemwide airline rankings require at least 5000 operated flights.

Airport rankings require at least 5000 operated departures, and evening or morning airport rankings require at least 2000 operated departures in that bucket.

Route rankings require at least 500 operated flights, or at least 200 when the route is limited to departures from one named airport.

Delay-cause rankings of airlines require at least 1000 late arrivals. Cancellation-cause rankings require at least 200 cancellations.

A named direction such as DFW to ORD is one way. The phrase between DFW and ORD means both directions combined.

Dallas with no airport code means DFW and DAL combined. Dallas/Fort Worth or DFW means DFW only.

Most delays means the greatest count of late arrivals, not the highest late rate.

Average delay means the mean signed arrival delay of operated flights. Early arrivals pull it down.

Best airline and worst airport use the on-time arrival rate, after the minimum-sample rule.

Ties in a ranking break by label ascending.

## Answering rules Daniel approved on 2026-10-11

These sentences are part of the analyst briefing and the grader rubric.

Answer the question literally first, then offer one better follow-up question.

When a number is given for one listed item, give that number for every listed item, with its unit.

An ambiguous question gets the literal answer, a stated reading, and the natural alternative cuts.

Name every route, airport, and airline explicitly. A route named in one direction is not the reverse.

BTS delay codes are carrier, weather, national air system, security, and late aircraft. They are coarse and self-reported, and they often cannot say why.

A ranking states its minimum-flights rule. A sample under that floor does not win.

The same question asked in different words gets the same figures.

Brand figures are shown with operating partners and without them. This extract has no marketing-airline column, so SkyWest and Republic stay unassigned.

A question that asks which airline is most on time out of an airport returns the literal winner and the major carriers at that airport. Major carriers are the top 3 by operated departures, plus any further carriers needed so the listed majors cover about 80 percent of operated departures. Each of those rows has the on-time arrival rate and the flight count. SkyWest is labeled SkyWest (flies for several airlines).

When Hawaiian is in a systemwide on-time ranking, the answer notes that Hawaiian benefits from island weather.

Do not put a window function in a WHERE clause. Put the window in a CTE, or filter the window with QUALIFY.

The July 2026 reporting-carrier header has `Reporting_Airline` and no marketing-airline column. Envoy, PSA, and Piedmont can roll into American because they fly for one brand. SkyWest and Republic fly for more than one brand, and this file cannot assign those flights to a marketing brand.

These cutoffs are choices. A smaller airport can still clear the evening floor. Changing the floor will change who sits at the bottom of that list. That is a review item, not a hidden constant.

## What this dataset cannot answer

The extract has no passenger counts, so a passenger-weighted rate cannot be computed.

The extract is not a live feed. Questions about yesterday, tomorrow, or the current hour cannot be answered from it.

The analyst is told to refuse those questions. A refusal on any other question is a miss.

## Status

status is DRAFT

candidate answers pending Daniel's review
