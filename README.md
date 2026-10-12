# Flight delay analyst

A draft, self-grading analyst for US airline on-time performance. It answers a plain-English question with DuckDB SQL, then a separate eval agent scores that answer against a bank of candidate results.

The audience is a hiring manager who runs data or AI. The point of week 1 is a measured loop: public data, explicit definitions, SQL you can read, and a score that says what it is. The scoreboard is a draft. Daniel Martinez has to approve the candidate answers before any of them is an approved standard.

## What you can run

```bash
pip install -r requirements.txt
PYTHONPATH=src python3 -m flight_analyst ingest
PYTHONPATH=src python3 -m flight_analyst build-bank
PYTHONPATH=src python3 -m flight_analyst eval
PYTHONPATH=src python3 -m flight_analyst.run_eval --provider stub --model stub --out results/stub.json
PYTHONPATH=src python3 -m flight_analyst scoreboard
PYTHONPATH=src python3 -m flight_analyst build-site
PYTHONPATH=src python3 -m flight_analyst ask "Which airline is most on time out of DFW?"
pytest
```

`ingest` downloads the data and builds `data/flights.duckdb`. `build-bank` runs the reference SQL and writes `questions/bank.json`. `eval` writes `reports/latest.json` and `reports/latest.md`. `scoreboard` writes `scoreboard/index.html`. `build-site` writes the static folder `site/` from the bank and the latest report. Open either HTML file locally. Building the site does not publish it.

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

`questions/bank.json` holds 120 plain-English questions: easy, medium, and hard, across airline reliability, airport reliability, routes, time of day, seasonality, and delay causes, plus ambiguous and trick questions. Each answerable question stores reference SQL and the rows that SQL returned. Two questions are refusals (yesterday, and a passenger-weighted rate) because the extract cannot answer them.

Daniel's 2026-10-11 review is applied question by question. The bank file stays `DRAFT` because most questions are still unread. Each question has its own status:

- `APPROVED` (13): Daniel-approved, including the wording of "How late is American?"
- `DRAFT-REVISED` (21): the reference SQL or the required note was changed from his review and is waiting for a recheck. The extra 11 are batch 4 on 2026-10-11: every "most on time out of" airport question now returns the literal winner and the major carriers (top 3 by flights, extended to about 80 percent), and the systemwide top 5 keeps the note that Hawaiian benefits from island weather. q022 and q025 were left as he found them.
- `DRAFT` (86): not in those review passes

The answering rules he approved are in the analyst briefing and the grader rubric (`docs/definitions.md`). An ambiguous answer has to give the literal figures, say how it was read, and include the alternative cuts, each with a unit. A follow-up question is an advisory nudge score. It does not change pass or fail.

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
- The spoken answer has to carry every expected label and a unit. An ambiguous question also has to say how it was read. A follow-up line is recorded and does not change pass or fail.

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

The stub is a rules baseline. It encodes the same definitions as the briefing and writes SQL for the patterns it recognizes. It does not implement medians, the airport morning-minus-evening gap, the 2025-11-26 versus October-Wednesdays comparison, or "does weather explain most delays". On those it returns a flight count, and the harness marks a miss. A stub score is a pipeline check. It is not a model accuracy number.

On the loaded window (1 August 2025 through 31 July 2026, 7,043,858 flights, 120 questions) the stub matched 116 of 120 candidate answers: 32/32 easy, 56/56 medium, and 28/32 hard. On the 13 Daniel-approved questions the same stub matched 9 of 13. All four misses are labeled `misread_question`, and all four are patterns the stub was never written to answer (median delay, the morning-minus-evening gap, Thanksgiving versus October Wednesdays, and whether weather explains most delays). The advisory nudge, which does not change that score, was offered on 120 of 120 answers. These figures are a rules baseline. The numbers are in `reports/latest.md`, `results/stub.json`, and `scoreboard/index.html`.

The first Claude Haiku 4.5 run is in `results/claude-haiku-4-5.json`. Graded again offline, with no further model call, it matches 64 of 120 candidate answers (53.3%) and 10 of 13 Daniel-approved questions. The file as first saved, when every extra row failed, was 44 of 120 and 5 of 13. The grader now requires the candidate rows and their order, and it allows extra rows that follow the answering rules. Numeric tolerances are unchanged. List-price spend on that run was $0.4901.

A paid run, on a machine that has a key, is one command. It stops before the next question once list-price spend reaches the cap (default $3). Prices live in `src/flight_analyst/prices.json` with a source URL and a date. An unlisted model id refuses to run.

```bash
python -m flight_analyst.run_eval --provider anthropic --model claude-haiku-4-5 --out results/claude-haiku-4-5.json
```

The results file is the same shape for Anthropic, OpenAI, xAI, and a local Qwen. Dropping another file in `results/` is enough for the compare page. The exact install and rebuild steps are in `RUN.md`.

SQL from the model is checked before it runs: one `SELECT` or `WITH`, the project tables only, no writes, no file or network readers. It then runs on a read-only DuckDB connection.

## Design choices

- DuckDB stays local. The public folder is HTML and two JSON extracts. Publishing is documented and left off.
- The metric definitions live in one module and are copied into the briefing and the definitions doc, so the three cannot quietly drift.
- Reference SQL is visible next to every candidate answer. A reviewer can rerun it.
- Minimum-sample rules are explicit because an empty airport will win a "worst on time" list if you let it.
- Reporting carrier is the default airline. Brand families are a separate, narrower table, and multi-partner regionals are left out rather than assigned by guess.
- The question bank stays `DRAFT` in the file, the report, and the scoreboard until there is an explicit review.

## Dice or airline

`analysis/dice_or_airline/findings.md` is the note for the LinkedIn post. It compares raw brand on-time rates with the same comparison after the airport, the route, the hour, and the month are held fixed, plus a DFW spotlight and the delay-cause split. The charts are PNGs in `analysis/dice_or_airline/charts/`. Reproduce with `PYTHONPATH=src python3 analysis/dice_or_airline/run.py` (extra packages in `analysis/requirements.txt`). The note is not published. The four bank questions that come from it stay `DRAFT`.

## Publishing

Two tiers. Neither one is turned on in this repository: GitHub Pages is not enabled, the Pages workflow does not run on push, and the live process refuses to listen unless `LIVE_ASK=1`.

### Static tier, $0 a month

`build-site` writes `site/`. The pages are the overview, the explore view (every draft candidate answer, a bar chart when the answer has 2 to 12 rows, and the reference SQL), and the scoreboard. The data files are `site/data/explore.json` and `site/data/report.json`. Nothing in that folder queries DuckDB, and nothing in it is a flight-level extract.

The database on disk is 367,538,176 bytes, about 351 MB, for 7,043,858 flights. That is too large to ship. DuckDB-WASM would make every visitor download it, and a Parquet extract of the same table is the same problem (`*.parquet` is gitignored as data). The published extracts are the candidate answers and the eval report.

This build of `site/` is **1,092,450 bytes**. The JSON extracts are **543,212 bytes** (`explore.json`, `report.json`, and `models.json`). `flight_rows_published` is 0. Those figures are also in `site/data/manifest.json`. The explore page includes all 120 questions, with each question's status, the alternative cuts, the minimum-flights rule on rankings, a likely follow-up, and a link to the model comparison. The compare page plots cost per 100 questions against accuracy and switches among saved answers. The saved runs are the stub at $0 and Claude Haiku 4.5. OpenAI, xAI, and a local model stay pending until a file for that provider is saved. The scoreboard on the site is the 116-of-120 stub run. A click-test map for that site is `docs/FEATURE-MAP.md`.

Preview locally, still without publishing:

```bash
PYTHONPATH=src python3 -m flight_analyst build-site
python3 -m http.server 8765 --directory site
```

Then open `http://127.0.0.1:8765/`. Links are relative, so the same folder works at a project-site subpath.

**GitHub Pages.** The workflow is `.github/workflows/pages.yml`. It runs only from the Actions tab (`workflow_dispatch`). The `deploy` input defaults to false, so a manual run builds `site/` and uploads a workflow artifact. It does not publish.

1. Leave the repository private until you decide otherwise.
2. In the repository settings, set Pages → Build and deployment → Source to **GitHub Actions**. This step is what enables Pages. It has not been done.
3. Actions → pages → Run workflow. Leave **deploy** unchecked to confirm the artifact.
4. Run it again with **deploy** checked only when you want the site public on Pages. A private repository's Pages site has its own visibility setting; check that before the deploy run.

Published GitHub Pages sites are limited to 1 GB and have a soft bandwidth limit of 100 GB a month. This site is far under both. Expected cost: **$0**.

**Cloudflare Pages**, alternative, also $0 on the free plan (unlimited static bandwidth, 500 builds a month, one build at a time):

- Build command: `pip install -r requirements.txt && PYTHONPATH=src python3 -m flight_analyst build-site`
- Build output directory: `site`
- Do not set a deploy hook until you mean to publish. Connecting the repo is the step that starts builds.

**Netlify**, alternative. The free plan is $0 while usage stays inside 300 credits a month. A production deploy costs 15 credits. Bandwidth costs 20 credits per GB, so credits spent only on bandwidth cover about 15 GB. Same build command and publish directory `site`.

### Live tier, cheap, off by default

The static site cannot answer a new question. That needs the DuckDB file, a process, and a model key. The cheapest sensible place for that process is a [Fly.io](https://fly.io/docs/about/pricing/) shared-cpu-1x machine with 1 GB of RAM and a 1 GB volume, in Ashburn (`iad`) or Secaucus (`ewr`), with `min_machines_running = 0` so the machine stops when it is idle.

Why that shape: the analyst already speaks DuckDB, and the file is about 351 MB. A 256 MB machine is the cheaper preset ($2.19 a month always on) and is too small to map that file and run Python. 1 GB is the smallest listed machine that can. Autostop means an idle month is mostly the volume ($0.15 per GB) plus a stopped root filesystem (about $0.15 per GB). A machine that stays on all month is $6.70 of compute plus the $0.15 volume, about **$7**. Light use with autostop stays around **$1** of hosting. Prices are Fly's published Ashburn list as of October 2026 and vary by region. Outbound traffic in North America and Europe is $0.02 per GB.

The endpoint is implemented and gated:

```bash
# does not listen, exit code 2
PYTHONPATH=src python3 -m flight_analyst live

LIVE_ASK=1 PYTHONPATH=src python3 -m flight_analyst live
# POST /ask {"question": "..."}  and  GET /health
```

Defaults, also in `.env.example` and `fly.toml`:

| Control | Default | What it does |
| --- | --- | --- |
| `LIVE_ASK` | off | The process listens only when this is `1`. |
| `LIVE_RATE_PER_HOUR` | 10 | Questions per client IP per hour. |
| `LIVE_RATE_GLOBAL_PER_HOUR` | 30 | Questions per process per hour. |
| `LIVE_SPEND_CAP_USD` | 5 | Persisted cap in `data/live_spend.json` (gitignored). A corrupt ledger refuses to run. |
| `LIVE_USD_PER_CALL` | 0.05 | Estimated charge for a non-stub call, taken before the model runs. The stub is charged $0. |

The cap is a ceiling on estimated spend, not the vendor invoice. $0.05 is above a short `gpt-4o-mini` call and in range for a short Claude Sonnet call, so the process stops before a forgotten server can run up a bill. Set `LIVE_USD_PER_CALL` to match the model you actually use. Delete `data/live_spend.json` to reset the counter on purpose.

The key stays on the server. `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` is read from the environment. A request that includes a key, a token, a provider, or a model is rejected. The response is an allow-list of answer fields plus the spend counters. The listener binds to `127.0.0.1` unless you pass `--host`. The Dockerfile sets `LIVE_ASK=0`, copies no database and no question bank, and expects the file at `FLIGHT_DB_PATH` (default `/data/flights.duckdb`).

Fly steps, when you choose to launch. Do not run the last command until the volume holds the database and the secrets are set:

```bash
fly launch --no-deploy --copy-config --name flight-analyst --region iad
fly volumes create flight_data --size 1 --region iad
# copy data/flights.duckdb onto that volume at /data/flights.duckdb
fly secrets set LIVE_ASK=1 OPENAI_API_KEY=... 
fly deploy
```

`fly.toml` is in the repo so the machine size, the volume mount, autostop, and `/health` are reviewable. No app has been launched from it.

Expected monthly cost if you turn this on:

| Use | Hosting | Model | Together |
| --- | ---: | ---: | ---: |
| Idle, machine autostops | about $1 | $0 until someone asks | about $1 |
| Left running all month | about $7 | $0 on the stub | about $7 |
| Paid model, cap fully used | about $1 to $7 | up to the $5 cap | about $6 to $12 |

### Supabase

Supabase is the wrong bill for this file. The free plan is $0 and includes a 500 MB database that pauses after a week idle. The DuckDB file is already about 351 MB in a columnar layout; the same 7,043,858 flights in Postgres, with indexes, will not fit that 500 MB. The Pro plan starts at $25 a month for 8 GB of disk and still needs a compute size that can scan the table. Edge functions also time out too quickly for a model-written scan of the full extract, and the analyst SQL is DuckDB, so the reference queries and the SQL guard would have to be rewritten. Fly keeps the existing file and the existing analyst, and the spend cap sits in the same process as the key.

## Tests

`pytest` covers the SQL guard, numeric and ranking comparison, the failure labels, a sample BTS load, the stub and the API payload builders, an end-to-end harness run on a fixture database, the draft status of the bank, the static site build, and the live-ask flag, rate limit, and spend cap. When `data/flights.duckdb` is present, the suite also reruns every reference query and checks it against the stored rows.

## Week 2

1. Review the bank question by question. Accept the interpretation, edit it, or drop it. Leave the status at `DRAFT` until that pass is done, and keep the scoreboard labeled that way.
2. Run one real model with `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` on the reviewed set. Keep the stub as a regression check so a prompt change cannot silently break the pipeline.
3. Hold out a slice of questions before editing the briefing. Otherwise the score measures the prompt against questions the prompt has already seen.
4. Publish a sensitivity table for the 100, 2,000, and 5,000 flight floors. Call out SFB on the evening list and Hawaiian on the systemwide list as the cases most exposed to those floors and to partial-year coverage.
5. Confirm the family table against whatever regionals the next BTS month actually contains.
6. Read a sample of misses and check whether the failure label matches a human reading.
7. The publishing steps are written and still off. Turn a host on only after the definitions and a model run are both in place.
