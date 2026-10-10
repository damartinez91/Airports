# Schema and data dictionary

This is the candidate schema for the draft analyst. The loaded vintage (months, row count, file hashes) is written to `docs/data_window.json` by the ingest script.

## Source

US Department of Transportation, Bureau of Transportation Statistics, Reporting Carrier On-Time Performance (1987-present). Files are the prezipped monthly extracts:

`https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{year}_{month}.zip`

The ingest script discovers the latest month that BTS has published and loads the 12 months ending there. It keeps the columns below and drops diversion-airport detail, taxi times, and the various airport-id keys. Those fields are real, and they are out of scope for this week.

Each row is one scheduled operation of a reporting carrier. Times are local airport times, not UTC. Delay fields are minutes.

## Grain and tables

- `flights` is the fact table, one row per scheduled flight in the extract.
- `carriers` and `airports` are lookup tables for readable names.
- `carrier_families` is the draft single-partner rollup. Multi-partner regionals are not in it.
- `flights_v` is the view the analyst is told to query. It joins names and adds the definition columns.
- `metadata` stores load bookkeeping (month list, hashes, row counts).

Airport names come from [OurAirports](https://davidmegginson.github.io/ourairports-data/airports.csv), which David Megginson has placed in the public domain. The join key is the IATA code. When a code is missing, the view shows the code. Carrier names come from the project map in `schema.py` (public identities of the reporting carriers). A code that is not in the map is displayed as itself.

Regional codes attached to a single family in this draft: MQ (AA), OH (AA), PT (AA), 9E (DL), C5 (UA), G7 (UA), ZW (UA), QX (AS). SkyWest (`OO`), Republic (`YX`), and Mesa (`YV`) are excluded because they fly for more than one major, so their flights cannot be given to one family without a separate assignment rule.

## `flights`

| Column | Type | Description |
| --- | --- | --- |
| `flight_date` | DATE | Calendar date of the scheduled flight. |
| `year` | INTEGER | Calendar year. |
| `quarter` | INTEGER | Calendar quarter, 1 through 4. |
| `month` | INTEGER | Calendar month, 1 through 12. |
| `day_of_month` | INTEGER | Day of the month. |
| `day_of_week` | INTEGER | BTS day of week, 1 Monday through 7 Sunday. |
| `airline_code` | VARCHAR | Reporting-carrier IATA code. |
| `tail_number` | VARCHAR | Aircraft tail number, when reported. |
| `flight_number` | VARCHAR | Reporting-carrier flight number. |
| `origin` | VARCHAR | Origin airport IATA code. |
| `dest` | VARCHAR | Destination airport IATA code. |
| `origin_city_name` | VARCHAR | BTS origin city label, for example Dallas/Fort Worth, TX. |
| `origin_state` | VARCHAR | Origin state or territory postal code. |
| `origin_state_name` | VARCHAR | Origin state or territory name. |
| `dest_city_name` | VARCHAR | BTS destination city label. |
| `dest_state` | VARCHAR | Destination state or territory postal code. |
| `dest_state_name` | VARCHAR | Destination state or territory name. |
| `crs_dep_time` | INTEGER | Scheduled local departure, HHMM. 2400 is stored as 2400 and maps to hour 0. |
| `dep_time` | INTEGER | Actual local departure, HHMM. Null when the flight did not depart. |
| `dep_delay` | DOUBLE | Signed departure delay in minutes. Negative means early. Null when unknown. |
| `crs_arr_time` | INTEGER | Scheduled local arrival, HHMM. |
| `arr_time` | INTEGER | Actual local arrival, HHMM. |
| `arr_delay` | DOUBLE | Signed arrival delay in minutes. Negative means early. |
| `cancelled` | BOOLEAN | True when the flight was cancelled. |
| `cancellation_code` | VARCHAR | A carrier, B weather, C national air system, D security. Null when not cancelled. |
| `diverted` | BOOLEAN | True when the flight was diverted. |
| `crs_elapsed_time` | DOUBLE | Scheduled block time in minutes. |
| `actual_elapsed_time` | DOUBLE | Actual block time in minutes. |
| `air_time` | DOUBLE | Airborne time in minutes. |
| `distance` | DOUBLE | Great-circle distance in miles. |
| `carrier_delay` | DOUBLE | Minutes of arrival delay BTS attributes to the carrier. Usually null unless the arrival was 15 minutes or more late. |
| `weather_delay` | DOUBLE | Minutes BTS attributes to extreme weather. |
| `nas_delay` | DOUBLE | Minutes BTS attributes to the National Aviation System. |
| `security_delay` | DOUBLE | Minutes BTS attributes to security. |
| `late_aircraft_delay` | DOUBLE | Minutes BTS attributes to a late inbound aircraft. |
| `sched_dep_hour` | INTEGER | Scheduled local departure hour, 0 through 23. |
| `sched_arr_hour` | INTEGER | Scheduled local arrival hour, 0 through 23. |
| `season` | VARCHAR | winter, spring, summer, or fall. |

## Columns added on `flights_v`

| Column | Type | Description |
| --- | --- | --- |
| `airline_name` | VARCHAR | Readable reporting-carrier name from the carrier lookup. |
| `origin_airport_name` | VARCHAR | Readable origin airport name. Falls back to the IATA code. |
| `dest_airport_name` | VARCHAR | Readable destination airport name. Falls back to the IATA code. |
| `operated` | BOOLEAN | True when the flight was not cancelled, not diverted, and has an arrival delay. |
| `on_time_arrival` | BOOLEAN | True when an operated flight arrived under 15 minutes late. Null when the flight did not operate. |
| `late_arrival` | BOOLEAN | True when an operated flight arrived 15 minutes or more late. Null when the flight did not operate. |
| `on_time_departure` | BOOLEAN | True when a non-cancelled flight with a known departure delay left under 15 minutes late. |
| `dep_time_bucket` | VARCHAR | morning, midday, evening, or night, from the scheduled departure hour. |
| `cause_minutes` | DOUBLE | Sum of the five BTS delay-cause minute columns. Null when none were reported. |
| `airline_controlled_minutes` | DOUBLE | Carrier delay plus late-aircraft delay. |
| `family_code` | VARCHAR | Single-partner family code, or null for multi-partner regionals and unknown carriers. |
| `family_name` | VARCHAR | Single-partner family name. |

## Lookups

### `carriers`

| Column | Type | Description |
| --- | --- | --- |
| `airline_code` | VARCHAR | Reporting-carrier IATA code. |
| `airline_name` | VARCHAR | Readable name. Project map first, otherwise the code itself. |

### `airports`

| Column | Type | Description |
| --- | --- | --- |
| `airport_code` | VARCHAR | IATA code. |
| `airport_name` | VARCHAR | Airport name from OurAirports, or the code when the lookup has no row. |
| `city` | VARCHAR | OurAirports municipality. |
| `region` | VARCHAR | OurAirports ISO region, for example US-TX. |
| `country` | VARCHAR | ISO country or territory code. |

### `carrier_families`

| Column | Type | Description |
| --- | --- | --- |
| `airline_code` | VARCHAR | Reporting-carrier code that belongs to one family. |
| `family_code` | VARCHAR | Family code: AA, DL, UA, AS, or a mainline-only code. |
| `family_name` | VARCHAR | Readable family name. |
| `affiliate_role` | VARCHAR | mainline or regional_single_partner. |


## Delay causes

BTS fills the five cause columns when an operated flight arrives 15 minutes or more late. The five minute columns are designed to sum to the arrival delay. This project takes that sum as `cause_minutes` and does not rescale it to `arr_delay` if the two differ by a rounding minute.

Airline-controlled minutes are `carrier_delay + late_aircraft_delay`. That is a draft convention: late aircraft is partly in the airline's control and partly inherited from upstream disruption. NAS minutes include traffic, airport operations, and some weather that was not coded as extreme weather. A reader who treats "weather" as everything meteorological will over-read `weather_delay` and under-read `nas_delay`.

## What the numbers are not

The on-time flag is an arrival metric on operated flights. It is not a passenger-weighted metric, not a customer-complaint metric, and not a live operational snapshot. See `docs/definitions.md`.
