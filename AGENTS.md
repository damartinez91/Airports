# Agent rules

Read this file before editing. The answering rules are written in [docs/definitions.md](docs/definitions.md), in the section "Answering rules Daniel approved on 2026-10-11". That file is the source of truth. When this page and that file disagree, follow that file.

This is a self-grading analyst for US DOT on-time data. The analyst writes SQL. A separate grader scores that SQL against a draft answer key. The analyst never reads the key.

## Map

| Path | What it is |
| --- | --- |
| `src/flight_analyst/analyst.py` | Writes one SQL statement from the question text. |
| `src/flight_analyst/evaluate.py`, `compare.py` | The grader. |
| `src/flight_analyst/questions.py` | Reference SQL. This is the source of the answer key. |
| `questions/bank.json` | The answer key. The file status stays `DRAFT`. |
| `questions/anticipated_bank.json` | Cached answers for the router. Not a place to store a made-up match. |
| `docs/definitions.md` | Definitions and the answering rules. |
| `docs/schema.md` | Column dictionary. It is generated from `schema.py` and has to match it. |
| `docs/bank-edits.md` | The log a reviewer reads when the answer key changes. |
| `results/stub.json` | Rules baseline. 116 of 120. No model call. |
| `tests/accuracy_baseline.json` | The accuracy floor the grader check enforces. |
| `site/` | Static pages built by `build-site`. |
| `.github/workflows/guardrails.yml` | These checks, on every pull request and on every push to `main`. |
| `.github/workflows/pages.yml` | Site deploy. Leave this file alone unless the task is the deploy. |
| `tests/fixtures/sample_flights.duckdb` | The only database that belongs in git. |

## How to run

```bash
pip install -r requirements.txt
PYTHONPATH=src python3 -m flight_analyst ask "Which airline is most on time out of DFW?"
pytest
PYTHONPATH=src python3 scripts/check_guardrails.py
```

`pytest` and `scripts/check_guardrails.py` are the definition of a green change. Both run with empty `ANTHROPIC_API_KEY` and `OPENAI_API_KEY`. Neither calls a model. The full flight database is optional: the committed checks use the answer key, the stub file, and the sample database.

`ingest` downloads BTS files into `data/`, which is gitignored. `build-bank` rewrites `questions/bank.json` from the reference SQL. That rewrite is an answer-key change and needs a `BANK-EDIT` line.

## Answering rules

Follow [docs/definitions.md](docs/definitions.md). The short form:

- Answer the question literally first, then offer one better follow-up. The follow-up does not change pass or fail.
- When one listed item has a number, every listed item has that number, with a unit.
- Envoy, PSA, and Piedmont are American Eagle and fly for American. Republic is American Eagle and also flies for other airlines, so it stays unassigned. SkyWest flies for several airlines and stays unassigned.
- A named direction is one way. "Between" two airports returns each direction and a both-directions row.
- A ranking states its minimum-flights rule. A sample under that floor does not win.
- "Which airport is the worst?" returns the worst airport overall and the worst major hub (150,000 operated departures).
- BTS delay codes are coarse and self-reported. Say that they cannot fully explain why.

## Never

- Edit `questions/bank.json`, `questions/anticipated_bank.json`, or `src/flight_analyst/questions.py` to make a test pass. A real change to those files adds a new line to `docs/bank-edits.md` that starts with `BANK-EDIT:`.
- Lower `tests/accuracy_baseline.json` without a new line that starts with `BASELINE-EDIT:`.
- Commit `data/`, a parquet file, a CSV, a DuckDB file other than `tests/fixtures/sample_flights.duckdb`, or any file over 5 MB.
- Call a paid model API from tests or from GitHub Actions. A graded model run is a local command with a key, and it writes a new file under `results/`.
- Hand-edit the numbers in `site/data/`. Rebuild with `build-site` when the inputs change.
- Weaken `tests/test_guardrails.py`, `src/flight_analyst/guardrails.py`, or `scripts/check_guardrails.py` to turn a red check green.
- Change `.github/workflows/pages.yml` as part of a guardrail edit. The Pages deploy is a separate review.

## Done

A change is done when `pytest` passes and `PYTHONPATH=src python3 scripts/check_guardrails.py` passes. A broken brand label, a ranking floor that a tiny sample can clear, or a stub score under 116 of 120 fails those commands. The pull request says, in plain English, what changed and what the checks now catch.
