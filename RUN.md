# Run the analyst on another machine

This folder is the flight-delay analyst. The DuckDB file, when present at `data/flights.duckdb`, is the August 2025 through July 2026 extract. If that file is missing, the first command builds it from BTS.

Nothing here publishes the site, enables GitHub Pages, or changes repository visibility.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

`pip install -e .` puts `flight_analyst` on the path, so the commands below do not need `PYTHONPATH`.

## Database

If `data/flights.duckdb` is already in this folder, skip this step.

```bash
python -m flight_analyst ingest
python -m flight_analyst build-bank
```

`ingest` downloads the latest 12 BTS reporting-carrier months. `build-bank` recomputes the candidate rows.

## Anthropic eval

Set the key in the environment. The command below does not print it. The default cap is $3 of list price. The run stops before the next question once spend reaches the cap. The model id has to be a row in `src/flight_analyst/prices.json` (Haiku 4.5 is `$1` per million input tokens and `$5` per million output tokens, source `https://platform.claude.com/docs/en/about-claude/pricing`, dated 2026-10-12).

```bash
export ANTHROPIC_API_KEY=your-key
python -m flight_analyst.run_eval \
  --provider anthropic \
  --model claude-haiku-4-5 \
  --budget-usd 3
```

Omit `--out`. Each run writes a new file, `results/claude-haiku-4-5-r1.json`, then `results/claude-haiku-4-5-r2.json`, and the compare page labels them `claude-haiku-4-5 r1` and `claude-haiku-4-5 r2`. A Sonnet run is `results/claude-sonnet-4-5-r1.json`, labeled `claude-sonnet-4-5 r1`. Passing `--out` to a path that already exists stops before any request. The file has, per question, the answer, the grade, input tokens, output tokens, list-price cost, and latency. The printed summary has accuracy on every scored question and accuracy on the Daniel-approved questions. A model id that is not in the price table exits before any request.

The same command with `--provider openai` writes an OpenAI run. An xAI or local Qwen run uses the same JSON shape (`schema_version` `model-run-v1`). Add a price row, then save the file under `results/`. The compare page picks it up with no change to the page code. `qwen-local` is already priced at $0 and labeled as not an API invoice.

## Rebuild the static site

```bash
python -m flight_analyst build-site
python -m http.server 8765 --directory site
```

Open `http://127.0.0.1:8765/`. The scoreboard is the latest eval report. The compare page reads every `results/*.json` file. This command does not deploy.

## Stub, no key

```bash
python -m flight_analyst.run_eval --provider stub --model stub --out results/stub.json --budget-usd 3
```

That score is a rules baseline. It is not a model accuracy number. `results/stub.json` is the saved baseline. The command refuses to overwrite it. Omit `--out` only when you want a second stub file, `results/stub-r1.json`.
