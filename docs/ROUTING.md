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

## Answer feedback

Under a live answer (cached Sonnet or Haiku) the asker can press **This didn't answer my question** and optionally fill in **What I expected**. The static site does not have that box yet. The live process serves the markup at `GET /feedback-hook`, and `flight_analyst.feedback.wrap_live_answer` returns the same button and box under an answer. Drop that fragment into the live question box when it ships. Until then, the same report can be filed with no model call:

```bash
python -m flight_analyst feedback \
  --question "Which airline is most on time out of DFW?" \
  --answer "Envoy leads at 82.7 percent." \
  --route cached-sonnet \
  --model claude-sonnet-4-5 \
  --match-score 0.91 \
  --matched-id q001 \
  --expected "I wanted the major carriers as well as the winner."
```

`POST /feedback` takes the same fields as JSON (`question`, `answer_text`, `route`, `model`, optional `expected`, and for a cached hit `match_score` and `matched_id`). `route` is `cached-sonnet` or `haiku`. The ask response now includes `route`, `model`, `matched_id`, and `match_score` so the hook can send them back. The handler does not call a model and does not charge the spend ledger.

Each report is one JSON line in `data/feedback.jsonl` (gitignored, next to the live logs): question text, the answer shown, model, route, match score when the route was cached, matched anticipated-question id when there was one, the free text, and a UTC timestamp. The free text is capped at 1,000 characters. A client address, account, or contact field is not stored; extra keys such as `email` or `ip` are dropped, and credential fields are rejected.

A cached-Sonnet miss sets `recheck_cached` and, when `matched_id` is present, flags that bank id in `data/cached_recheck.json`. The embedding match may have been wrong, so the cached item is marked for re-check. The committed anticipated bank is not rewritten by the report.

## Daily review

`python -m flight_analyst daily-review [--day YYYY-MM-DD]` (default: yesterday UTC). New questions need `ANTHROPIC_API_KEY`; the model is pinned to Sonnet. A day that is only feedback does not call a model.

- Takes `route == "new"`, non-refused questions, dedupes near-duplicates (cosine ≥ 0.9).
- Up to `REVIEW_MAX_QUESTIONS` (25). Stops before the question that would cross `REVIEW_SPEND_CAP_USD`, estimated at 2 × `REVIEW_USD_PER_CALL` per question.
- Sonnet answers each, then grades its answer against Haiku's live answer: `correct`, `agrees_with_reference`, `suggested_category`, `notes`.
- Also takes that day's rows from `data/feedback.jsonl`. They are flagged `user-reported miss`, ranked first, and are not sent to a model while the queue is built. Each one shows the question, the answer given, and what I expected (`for_reviewer`, plus `question`, `answer_given`, `what_i_expected`). A cached miss keeps `recheck_cached` and the matched id.
- Writes `data/review_queue/YYYY-MM-DD.json`.

### Promotion: queue for approval (default)

Nothing is marked approved without Daniel's explicit categorization:

    python -m flight_analyst review-promote --day 2026-10-17 --id live-2026-10-17-000 --category head-to-head

`--category` is required and an empty one is refused. The entry joins the anticipated bank as `APPROVED` with that category. It does **not** enter the graded bank (`questions/bank.json`); that stays a separate, deliberate step.

A user-reported miss uses the same required-category rule, with one of these categories. File misses separately from new-question candidates. The other categories are recorded on the queue item and do not promote or mark it approved.

`send-to-sonnet` is a preview, not an approval. It asks Sonnet to re-answer and then grades that answer (the same two-call shape the daily cap estimates). The result is saved as `PENDING` in `data/pending_promotions.json` and on the queue item. The command prints the question, the old answer, Sonnet's new answer, and the grader result. It does not write the anticipated bank. The daily-review spend cap is unchanged: feedback rows still do not spend it, and this preview is a separate explicit command, not part of that run.

After Daniel has read the preview, a second command moves it:

```bash
python -m flight_analyst review-promote --approve fb-2026-10-17-000
python -m flight_analyst review-promote --reject fb-2026-10-17-001 --note "Match was fine; the question was out of scope."
```

`--approve` writes the pending Sonnet answer into the anticipated bank as `APPROVED`. It refuses any id that is not `PENDING` (unknown, rejected, or already approved). `--reject` stores the optional note and does not write the bank. A rejected id is no longer pending, so approve refuses it too.

| Category | What Daniel is deciding | Effect |
| --- | --- | --- |
| `send-to-sonnet` | Send to Sonnet | Re-answer, grade, and save a PENDING preview. The bank is not written |
| `fix-cached-answer` | Fix the cached answer | Recorded. The bank text is not rewritten here |
| `fix-routing` | Fix the routing or match threshold | Recorded. Thresholds stay as configured; the cached id stays flagged for re-check |
| `out-of-scope` | Out of scope | Recorded. Not approved |
| `no-change` | No change | Recorded. Not approved |

```bash
python -m flight_analyst review-promote --day 2026-10-17 --id fb-2026-10-17-000 --category send-to-sonnet
python -m flight_analyst review-promote --approve fb-2026-10-17-000
python -m flight_analyst review-promote --day 2026-10-17 --id fb-2026-10-17-001 --category fix-routing
```

### Alternative: labeled auto-promote (off)

`AUTO_PROMOTE=1` adds a candidate to the cache only if Sonnet graded it correct, it agrees with Haiku's figures, and the SQL ran without error or refusal. It is stored as status `AUTO`, category `auto-unreviewed`, served with that label, and never counts as approved or enters grading. To keep auto entries out of the cache while still collecting them, leave `AUTO` out of `ROUTE_SERVE_STATUSES` (the default already does). User-reported misses are never auto-promoted.

## Not built yet

- The live question box on the static site. Feedback is ready for it (`GET /feedback-hook`, `wrap_live_answer`, `POST /feedback`, and the `feedback` CLI) but no page renders the box, and this change does not deploy or enable Pages.
- Scheduler: no cron or GitHub Action runs `daily-review`; it needs the live host and its log (not launched). On a Fly machine it would be a daily `fly machine run --schedule daily` job.
- A review UI: the queue is JSON; Daniel approves by CLI. A miss shows the question, the answer given, and what I expected in that JSON.
- A learned embedder: `hash-v1` misses loose paraphrases (e.g. "most punctual at Dallas Fort Worth" scores 0.38 against the DFW question), so those go to Haiku. That direction is safe (cost, not wrong answers).
- The HTTP listener's off-topic guard (still only on the MCP tool).
