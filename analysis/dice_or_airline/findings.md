# Is it the dice or the airline?

**It is both.** Evening departures are 23.95 percentage points less on time than dawn, and the ten largest airports are 4.34 points less on time than midsize ones, but the airline does not disappear once the airport, the destination, the hour, and the month are held fixed: Delta is still 3.12 points ahead of the average flight and Southwest is 4.10 points behind, while American's 1.83-point systemwide deficit shrinks to 0.37 points.

This is a description of one year of flights, not a cause. A carrier that chooses a congested hub, a short turn, or a late-day bank is still the one that built that schedule.

## Supporting numbers

All rates are operated flights, August 1, 2025 through July 31, 2026. On time means arrival delay under 15 minutes. The national rate is 77.6304% of 6,895,510 operated flights (7,043,858 scheduled).

1. **The clock is the steep slope.** Departures scheduled at 05:00 local were on time 91.6873% of the time (188,230 flights). Departures at 20:00 were on time 67.7360% (300,806 flights). That is 23.95 percentage points.
2. **Hub size is a smaller slope, and it is not a straight line.** Airports with 10,000 to 49,999 operated departures: 80.43%. Airports with 150,000 or more: 76.09%. Airports under 10,000 sit in between, at 79.40%. Among 318 airports with at least 500 departures, the correlation of log volume and the on-time rate is -0.092.
3. **Delta's lead survives the controls. Southwest's deficit gets larger.** Raw gaps versus the national rate: Delta +3.90 points (81.5294% of 1,018,096 flights), United +1.63 (79.2632% of 814,734), Southwest -1.23 (76.4050% of 1,374,279), American -1.83 (75.7983% of 1,503,366). After airport, destination, hour, and month effects, those gaps are Delta +3.12, United +4.49, Southwest -4.10, and American -0.37. A logistic model on 980,762 flights gives the same pattern: Delta +3.80, United +4.35, Southwest -3.76, American -0.01.
4. **At DFW the American brand is behind the other airlines on the same routes.** American (mainline, Envoy, and PSA) was on time 70.9824% of 243,256 operated DFW departures. Everyone else at DFW was on time 76.8849% of 59,606. On 65 DFW routes where American and the other carriers each had at least 100 flights, American was 7.99 points behind those other flights (131,091 American flights in the match).
5. **Late minutes are mostly the airline's, and that fact does not rank the carriers.** Of American's late-arrival minutes, 78.1% are carrier delay plus late-aircraft delay. United's share is 61.8%, and 34.0% of United's late minutes are the National Aviation System bucket. Southwest's late minutes are 51.1% late aircraft.

## How the brands are built

The file is the BTS **reporting-carrier** extract. The July 2026 header has `Reporting_Airline` and no marketing-airline column, so a regional that flies for two majors cannot be given to one brand from this file.

| Brand in this note | Reporting codes rolled in | What actually appears |
| --- | --- | --- |
| American | AA, MQ, OH, PT | AA 962,421, Envoy (MQ) 309,815, PSA (OH) 231,130. Piedmont has no rows. |
| Delta | DL, 9E | DL 1,018,096. Endeavor has no rows. |
| United | UA, C5, G7, ZW | UA 814,734. The regional codes have no rows. |
| Alaska | AS, QX | AS 297,116. Horizon has no rows. |
| Southwest, JetBlue, Frontier, Spirit, Allegiant, Hawaiian | The reporting code itself | No regional to add. |

SkyWest (OO, 836,175 operated flights) and Republic (YX, 347,315) fly for more than one brand. Together they are 1,183,490 flights, 17.16% of operated flights. They are not inside American, Delta, United, or Alaska in any number above. Folding them in without a marketing carrier would be a guess. They stay in the model as their own rows so the airport effects are fit on the whole extract, and they are left off the brand charts.

Hawaiian reported from August 2025 through December 2025 only. Spirit reported through May 2026. Both are marked on the charts.

## Volume

Airport buckets are the origin's operated departures in this window.

| Origin size | Airports | Operated flights | On time |
| --- | ---: | ---: | ---: |
| Under 10,000 | 258 | 595,645 | 79.40% |
| 10,000 to 49,999 | 64 | 1,436,313 | 80.43% |
| 50,000 to 149,999 | 27 | 2,534,964 | 77.04% |
| 150,000 or more | 10 | 2,328,588 | 76.09% |

The dose-response is real and modest, and the smallest airports are not the best ones. The hour-of-day slope is much steeper: the morning peak is near 90%, and 17:00 through 20:00 sits near 68%.

## What "after controls" means

Three cuts, all on operated flights.

**Same airport.** Inside each origin, the brand's on-time rate minus the rate of the other flights at that airport, weighted by the brand's flights. Each side needs at least 100 flights. American's gap is -1.62 points. Same-airport matching does not remove American's systemwide deficit. Delta is +4.90. United is +2.69. Southwest is -3.80.

**Same route.** The same comparison on an origin-destination pair, again with at least 100 flights on each side. American's gap is -0.73 points, on 896,500 of its 1,503,366 flights (the routes where someone else also flies). Delta is +5.67. United is +5.26. Southwest is -4.83, on 683,700 of its flights. Allegiant's route match covers only 7,213 flights, so that cut is too thin to quote; Allegiant's same-airport gap is -4.53 points on 83,875 flights.

**Airport, destination, hour, and month together.** A linear model on all 6,895,510 operated flights. The on-time indicator is regressed on brand, with additive effects for origin, destination, scheduled departure hour, and year-month. The brand coefficients are shifted so the flight-weighted average is zero. What remains is the gap versus the average flight after those four schedules are held fixed. That is the dark dot on the third chart.

A logistic regression with the same controls, on the deterministic 1-in-7 sample that survives an 80-flight airport floor (980,762 flights), converged in 22 iterations. Its predictive margins match the linear gaps within about one point. American is -0.37 in the linear model and -0.01 in the logistic model: both say the systemwide American gap is gone, or too small to separate from zero.

United is the clearest "dice" case in the other direction. The raw gap is +1.63. After controls it is +4.49. The hubs were hiding a better on-time record. Southwest is the reverse: the raw gap is -1.23, and after controls it is -4.10. The secondary airports were flattering the record.

## DFW, and Dallas versus Southwest

American is most of DFW, so the airport average is partly American's own record. The sharper cut is the shared routes: on those 65 routes American is 7.99 points behind the other carriers. Delta at DFW is on time 80.95% (11,358 flights). United is 76.15% (9,785). SkyWest is 77.15% (21,812). American is 70.98%.

Southwest does not operate DFW in this extract. The Dallas comparison is American-brand departures from DFW versus Southwest departures from DAL to the same BTS destination city, each side at least 200 flights. That match has 58 cities. Weighted by American's flights, American is 2.56 points behind Southwest. It is not one number in every city: American is ahead to New York and Washington, and behind on Houston, San Antonio, Austin, Orlando, Las Vegas, and Denver. The city-level rows are in `results.json`.

## Delay causes

On late arrivals, BTS splits the minutes into carrier, weather, NAS (the airspace system), security, and late aircraft. This project calls carrier plus late aircraft the airline-controlled share. Weather is the small slice almost everywhere (American 7.0%, Southwest 2.3%, SkyWest 14.0%). A high airline-controlled share is not the same thing as a poor on-time rate. Hawaiian's late minutes are 96.0% airline-controlled, and Hawaiian's raw on-time rate is the highest in the extract. United's NAS share is 34.0%, which fits a hub system, and United's controlled on-time gap is positive.

## Caveats

- Correlation is not causation. The controls hold the recorded airport, destination, hour, and month fixed. They do not hold the aircraft turn, the schedule padding, the fleet, or the passengers fixed. None of those are in this file.
- On time is the DOT arrival cut: under 15 minutes. A delay of exactly 15 minutes is late. Cancelled and diverted flights are out of the denominator.
- There is no marketing carrier in this extract. Republic and SkyWest are not American, Delta, United, or Alaska here.
- Hawaiian and Spirit do not cover the whole year.
- The airport-size pattern is small and not monotone. Do not say that every extra flight makes the airport later.
- The linear and logistic gaps are model summaries. The same-route gaps are the ones that only use routes both sides actually fly. Where those two disagree, the route cut is the more literal reading, and the model is the one that also holds hour and month fixed.
- Delay-cause minutes exist only on flights that arrived 15 minutes or more late. They do not explain cancellations.

## Charts

- `analysis/dice_or_airline/charts/01_who_arrives_on_time.png` (1200×1200)
- `analysis/dice_or_airline/charts/02_busier_airports.png` (1200×627)
- `analysis/dice_or_airline/charts/03_airline_still_shows.png` (1200×1200). The file name is the chart of gaps before and after controls. The title on the image is "Holding the schedule fixed."
- `analysis/dice_or_airline/charts/04_whose_minutes.png` (1200×627)

Each image carries the source line `BTS On-Time Performance, Aug 2025 to Jul 2026`.

## Reproduce

From the repo root, with the local database at `data/flights.duckdb`:

```bash
pip install -r analysis/requirements.txt
PYTHONPATH=src python3 analysis/dice_or_airline/run.py
```

The script reads the DuckDB view `flights_v`, writes `analysis/dice_or_airline/results.json`, and redraws the four charts. The brand `CASE`, the matched-route query, and the regression are in that file. Nothing here is published. The question-bank rows added for this note are status DRAFT, candidate answers pending Daniel's review.
