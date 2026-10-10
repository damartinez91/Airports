# Flight delay analyst

A draft, self-grading analyst for US airline on-time performance. It answers a plain-English question with DuckDB SQL, then a separate eval agent scores that answer against a bank of candidate results.

The audience is a hiring manager who runs data or AI. The point of week 1 is a measured loop: public data, explicit definitions, SQL you can read, and a score that says what it is. The scoreboard is a draft. Daniel Martinez has to approve the candidate answers before any of them is an approved standard.

## What you can run

```bash
pip install -r requirements.txt
PYTHONPATH=src python3 -m flight_analyst ingest
PYTHONPATH=src python3 -m flight_analyst build-bank
PYTHONPATH=src python3 -m flight_analyst eval
PYTHONPATH=src python3 -m flight_analyst scoreboard
PYTHONPATH=src python3 -m flight_analyst ask "Which airline is most on time out of DFW?"
pytest
```

`ingest` downloads the data and builds `data/flights.duckdb`. `build-bank` runs the reference SQL and writes `questions/bank.json`. `eval` writes `reports/latest.json` and `reports/latest.md`. `scoreboard` writes `scoreboard/index.html`. Open that file locally. This project does not deploy anything.

Raw zips, the DuckDB file, parquet, and downloaded CSVs stay on disk and out of git (see `.gitignore`).

## Data

Source: US DOT Bureau of Transportation Statistics, Reporting Carrier On-Time Performance (1987-present), the prezipped monthly files on transtats.bts.gov. The ingest script walks backward from the current month and loads the 12 newest files BTS has published.

Loaded vintage, retrieved 10 October 2026:

| | |
| --- | --- |
| Months | August 2025 through July 2026 |
| Flight dates | 2025-08-01 through 2026-07-31 |
| Scheduled flights | 7,043,858 |
| Operated flights | 6,895,510 |
| Reporting carriers | 14 |
| Origin airports | 359 |
| On-time arrival rate | 77.6% |
| Cancellation rate | 1.8% |

File hashes and per-month row counts are in `docs/data_window.json`.

Each row is one scheduled flight. The fact table keeps airline, origin, destination, scheduled and actual times, cancellation and diversion flags, and the five delay-cause columns (carrier, weather, NAS, security, late aircraft). `carriers` and `airports` are lookup tables. Airport names come from OurAirports (public domain), joined on IATA code. Carrier names come from a small map in `src/flight_analyst/schema.py`. The column dictionary is `docs/schema.md`.

Two facts about this vintage belong next to any ranking:

- Hawaiian reports through December 2025 and then drops out. Spirit reports through early May 2026 (354 flights that month) and then drops out. Both still clear the 5,000-flight floor, so Hawaiian leads the draft systemwide on-time ranking on a partial year.
- Endeavor, Piedmont, Horizon, CommuteAir, GoJet, and Air Wisconsin have no rows. SkyWest and Republic do, and they stay outside the single-partner family table because they fly for more than one major.

## Definitions

"On time" means an operated flight with arrival delay under 15 minutes. That is the DOT cut. A delay of exactly 15 minutes is late. Cancelled and diverted flights are out of that denominator. The rest of the wording (evening, own fault, Dallas, most delays, minimum samples) is written down in `docs/definitions.md` and copied into the analyst briefing so the SQL and the prose use the same rules.

Those rules are choices. The evening-airport floor is 2,000 operated departures. Sanford (SFB) clears it by a small margin and sits at the top of the draft "worst evening airports" list. A higher floor would remove it. That is a review item.

## Question bank

`questions/bank.json` holds 111 plain-English questions: easy, medium, and hard, across airline reliability, airport reliability, routes, time of day, seasonality, and delay causes, plus ambiguous and trick questions. Each answerable question stores reference SQL and the rows that SQL returned. Two questions are refusals (yesterday, and a passenger-weighted rate) because the extract cannot answer them.

The file is marked:

- `status`: `DRAFT`
- `review_note`: `candidate answers pending Daniel's review`

The analyst process never reads this file. The eval process does.

A few draft results, so a reader can see the shape of the bank. They are candidate numbers, pending review:

- Most on time out of DFW, after the 100-flight rule: Envoy (MQ), on-time arrival rate 82.7% on 62,560 operated departures. Mainline American is a different question.
- Five weakest evening-departure airports by on-time arrival rate: SFB, DAL, FLL, MCO, BOS.
- American versus Southwest on DFW to ORD: American operated 4,072 flights, on-time arrival rate 66.1%, cancellation rate 3.5%, mean signed arrival delay 20.3 minutes. Southwest scheduled 0 flights on that airport pair. Southwest's Dallas service in this extract is at DAL.
- American's late-arrival minutes: 80.6% airline-controlled (carrier plus late aircraft) and 5.6% weather, under the draft cause definition.
- Weather is 6.1% of systemwide late-arrival minutes and NAS is 20.7%. The draft weather-majority flag is 0.

## How the eval works

The harness and the analyst are separate. For each question the harness passes only the question text. The analyst returns SQL, the rows DuckDB produced, a short text rendering, and a bar-chart spec when the result is a short table. The harness then compares those rows with the stored candidate rows.

Comparison is numeric and structural:

- Entities are resolved through carrier and airport codes and names (AA and "American Airlines" are the same entity). Rank order and set membership use those identities.
- Rates match within 0.5 percentage points. Delay minutes match within 0.05. Counts match exactly. A rate column expressed as 81 instead of 0.81 is accepted when the column is a rate.
- The prose answer is stored and ignored by the scorer.

A miss gets one failure label:

| Label | Meaning |
| --- | --- |
| `wrong_sql` | The statement did not run, or the response had no SQL. |
| `wrong_aggregation` | The entities match and a figure or the order does not. |
| `misread_question` | The shape or the set of entities is different, or the analyst answered a question it should have refused. |
| `refused` | The analyst declined a question that has a candidate answer. |
| `error` | The run broke before a SQL result. |

Those labels are a heuristic for the scoreboard, not a second judgment of intent. Ambiguous questions are scored against one documented interpretation, so a miss there can be a definition disagreement. The question notes say which those are.

The report records overall accuracy, accuracy by difficulty, accuracy by category, and the failure counts. The scoreboard renders the latest report: the overall number, the three difficulties, and each miss with the analyst SQL beside the reference SQL.

## Analyst and the model provider

`LLM_PROVIDER` selects the client.

| Provider | Secret | Optional model variable | Default model |
| --- | --- | --- | --- |
| `openai` | `OPENAI_API_KEY` | `OPENAI_MODEL` | `gpt-4o-mini` |
| `anthropic` | `ANTHROPIC_API_KEY` | `ANTHROPIC_MODEL` | `claude-sonnet-4-5` |
| `stub` | none | | |

If `LLM_PROVIDER` is unset and neither key is set, the CLI uses the stub. This workspace had neither key, so the published report is the stub run. No model API was called.

The stub is a rules baseline. It encodes the same definitions as the briefing and writes SQL for the patterns it recognizes. It does not implement medians, the airport morning-minus-evening gap, the 2025-11-26 versus October-Wednesdays comparison, "how late is ...", or "does weather explain most delays". On those it returns a flight count, and the harness marks a miss. A stub score is a pipeline check. It is not a model accuracy number.

On the loaded window (1 August 2025 through 31 July 2026, 7,043,858 flights, 111 draft questions) the stub matched 105 candidate answers: 32/32 easy, 50/51 medium, and 23/28 hard. All six misses are labeled `misread_question`. The numbers are in `reports/latest.md` and `scoreboard/index.html`. No live model has been scored.

SQL from the model is checked before it runs: one `SELECT` or `WITH`, the project tables only, no writes, no file or network readers. It then runs on a read-only DuckDB connection.

## Design choices

- DuckDB, local files, and a static HTML page. Nothing to host.
- The metric definitions live in one module and are copied into the briefing and the definitions doc, so the three cannot quietly drift.
- Reference SQL is visible next to every candidate answer. A reviewer can rerun it.
- Minimum-sample rules are explicit because an empty airport will win a "worst on time" list if you let it.
- Reporting carrier is the default airline. Brand families are a separate, narrower table, and multi-partner regionals are left out rather than assigned by guess.
- The question bank stays `DRAFT` in the file, the report, and the scoreboard until there is an explicit review.

## Tests

`pytest` covers the SQL guard, numeric and ranking comparison, the failure labels, a sample BTS load, the stub and the API payload builders, an end-to-end harness run on a fixture database, and the draft status of the bank. When `data/flights.duckdb` is present, the suite also reruns every reference query and checks it against the stored rows.

## Week 2

1. Review the bank question by question. Accept the interpretation, edit it, or drop it. Leave the status at `DRAFT` until that pass is done, and keep the scoreboard labeled that way.
2. Run one real model with `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` on the reviewed set. Keep the stub as a regression check so a prompt change cannot silently break the pipeline.
3. Hold out a slice of questions before editing the briefing. Otherwise the score measures the prompt against questions the prompt has already seen.
4. Publish a sensitivity table for the 100, 2,000, and 5,000 flight floors. Call out SFB on the evening list and Hawaiian on the systemwide list as the cases most exposed to those floors and to partial-year coverage.
5. Confirm the family table against whatever regionals the next BTS month actually contains.
6. Read a sample of misses and check whether the failure label matches a human reading.
7. Leave deployment alone until the definitions and the model run are both in place.
