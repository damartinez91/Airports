# Live question routing and daily review

Status: implemented and unit-tested offline ($0). Not deployed: the live listener still refuses to start unless `LIVE_ASK=1`, and no host is launched.

## Tiers

| Question | Model | Cost |
| --- | --- | --- |
| Anticipated (in `questions/anticipated_bank.json`) | Claude Sonnet 4.5, answered once ahead of time, served from cache | $0 per visit |
| New (no confident match) | Claude Haiku 4.5, live, behind the existing spend cap and rate limit | ~$0.005–0.05 per call |
| Daily review of new questions | Claude Sonnet 4.5 answers and grades, capped per run | `REVIEW_SPEND_CAP_USD`, default $2.00 |

## Matching (no model call)

`flight_analyst.routing` embeds the visitor question with a local, dependency-free hashed word + character-trigram embedder (`hash-v1`) and takes the cosine against every bank entry.

1. **Entity guard first.** Airports, airlines, years and time words in the visitor question must equal the matched entry's. "Most on time out of PHX" is never served the DFW answer, however close the wording.
2. `score >= ROUTE_HIGH` (default 0.86): serve the cached Sonnet answer, labeled `saved answer from claude-sonnet-4-5 (claude-sonnet-4-5 r1, DRAFT|APPROVED)`. Not charged.
3. `ROUTE_LOW <= score < ROUTE_HIGH` (default 0.62): borderline. If `ROUTE_BORDERLINE_CHECK=1`, one cheap Haiku YES/NO "same question?" call decides (charged `ROUTE_CHECK_USD_PER_CALL`, default $0.01, to the same ledger, skipped when capped). Otherwise treated as new.
4. `score < ROUTE_LOW`: new. Haiku answers (`LIVE_MODEL`, default `claude-haiku-4-5`, pinned so the Sonnet default in `llm.py` never leaks into the live path).

Thresholds are env-configurable and must be re-tuned if the embedder is swapped for a learned one (the `Embedder` protocol makes that a one-class change; stored vectors are recomputed when the embedder name changes).

`ROUTE_SERVE_STATUSES` (default `APPROVED,DRAFT`) controls which bank statuses may be served. `APPROVED` alone serves only Daniel-approved answers (28 of 92 today).

Build the bank ($0): `python -m flight_analyst build-anticipated --run results/claude-sonnet-4-5-r1.json`. Only answers the grader passed are cached (92 of 120); `--include-failed` overrides.

## Logging

Every live question is appended to `data/live_questions/YYYY-MM-DD.jsonl` (UTC day; `data/` is gitignored, so visitor text never reaches the repo): question, route (`cached`/`new`), score, nearest bank id, and for new questions Haiku's answer, SQL, refusal and SQL error.

## Daily review

`python -m flight_analyst daily-review [--day YYYY-MM-DD]` (default: yesterday UTC). Needs `ANTHROPIC_API_KEY`; the model is pinned to Sonnet.

- Takes `route == "new"`, non-refused questions, dedupes near-duplicates (cosine ≥ 0.9).
- Up to `REVIEW_MAX_QUESTIONS` (25). Stops before the question that would cross `REVIEW_SPEND_CAP_USD`, estimated at 2 × `REVIEW_USD_PER_CALL` per question.
- Sonnet answers each, then grades its answer against Haiku's live answer: `correct`, `agrees_with_reference`, `suggested_category`, `notes`.
- Writes `data/review_queue/YYYY-MM-DD.json`.

### Promotion: queue for approval (default)

Nothing is marked approved without Daniel's explicit categorization:

    python -m flight_analyst review-promote --day 2026-10-17 --id live-2026-10-17-000 --category head-to-head

`--category` is required and an empty one is refused. The entry joins the anticipated bank as `APPROVED` with that category. It does **not** enter the graded bank (`questions/bank.json`); that stays a separate, deliberate step.

### Alternative: labeled auto-promote (off)

`AUTO_PROMOTE=1` adds a candidate to the cache only if Sonnet graded it correct, it agrees with Haiku's figures, and the SQL ran without error or refusal. It is stored as status `AUTO`, category `auto-unreviewed`, served with that label, and never counts as approved or enters grading. To keep auto entries out of the cache while still collecting them, leave `AUTO` out of `ROUTE_SERVE_STATUSES` (the default already does).

## Not built yet

- Scheduler: no cron or GitHub Action runs `daily-review`; it needs the live host and its log (not launched). On a Fly machine it would be a daily `fly machine run --schedule daily` job.
- A review UI: the queue is JSON; Daniel approves by CLI.
- A learned embedder: `hash-v1` misses loose paraphrases (e.g. "most punctual at Dallas Fort Worth" scores 0.38 against the DFW question), so those go to Haiku. That direction is safe (cost, not wrong answers).
- The HTTP listener's off-topic guard (still only on the MCP tool).
