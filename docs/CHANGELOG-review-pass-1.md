# Review pass 1: what changed, in plain English

For a VP or Head of AI and Data conversation. Daniel Martinez's first review of the draft on-time analyst, 11 October 2026. Flights: 1 August 2025 through 31 July 2026. Review commit: [83b490c](https://github.com/damartinez91/Airports/commit/83b490c). Week-1 system: [19b0c24](https://github.com/damartinez91/Airports/commit/19b0c24).

## What it looked like before

The **analyst** sees the question, the schema, and the definition of "on time," writes one SQL query, and never sees the answer key. The **grader** compared rows only (`compare_result` in `src/flight_analyst/compare.py`): same airlines or airports, numbers within a tolerance, same rank order. It ignored the sentence around the table. Every question was draft, so "best airline" could be Envoy with no mention that it is American Eagle.

No model was called. A rules stub matched **105 of 111** (94.6%) on 10 October 2026. All six misses were `misread_question`. Report: `reports/eval-20261010T201320+0000.json`.

## What Daniel decided, and what the code did

He marked 23 questions (the ask said 21; 23 rows had a call). Thirteen accepted are `APPROVED`. Ten sent back are `DRAFT-REVISED`: the SQL changed, and he has not rechecked them. The other 97, including new questions, stay `DRAFT`. The bank file stays draft. New fields: `status`, `interpretation`, `alternatives`, `follow_up`, `why_limit`, `minimum_rule` (`src/flight_analyst/questions.py`, `questions/bank.json`).

**Same numbers, any wording.** On "How late is American?" he wrote: "I disagree, people may ask it all types of ways." Approved. "Is American usually late?" and "American's on-time record?" share that SQL: American 74.6% on time, mean delay 13.3 minutes.

**Brand, not only the operator.** "Best out of DFW" now shows Envoy (American Eagle) 82.7%, best mainline (Delta) 80.9%, and the brand rollup. American versus Delta: "answer with all and without all operating partners." Mainline 74.6%; with Envoy and PSA 75.8%. Delta 81.5% either way (Endeavor has no rows). He called the median answer "Brilliant" and asked for brands "since that is how people normally ask." That question stayed approved; the brand cut is the follow-up.

**Both directions, every row numbered.** DFW–ORD: one direction only if they ask for one, otherwise "each direction separately and also a combined score." One pooled rate became DFW→ORD 68.3%, ORD→DFW 70.5%, combined 69.4%. Cancellation routes: "state the routes explicitly, nothing implicit" — Detroit–Pellston each way is its own row. Slowest DFW routes: "Show the minutes on each" — city, minutes, and flights on every row.

**"Worst" is not only a small airport.** After 5,000 flights, SFB overall; among airports with 150,000+ departures, ORD. "Most delays" now has Southwest's count (324,261) and late rate (23.6%).

**Literal first, then the better cut.** Atlanta, July 2026: Alaska 73.5% on 113 flights (floor 100), plus American, Delta, and United in the same answer.

**Say when a code cannot say why.** Carrier-delay share and cancellation code A kept their winners. The answer names the five delay codes and A–D, and says they are coarse, self-reported, and silent on the operation.

**Dallas, then the dice question.** American versus Southwest out of Dallas: "Good idea," and say DFW and DAL are pooled. Approved. New draft: largest airports 76.1% on time, midsize 80.4%; same-airport gaps American −1.6 points, Delta +4.9, United +2.7, Southwest −3.8.

The explore page (`src/flight_analyst/sitebuild.py`) shows status, the reading, the other cuts, the ranking floor, the cause-code limit, and a likely follow-up.

**Left undone on purpose.** July 2026 has `Reporting_Airline` and no marketing-airline column. SkyWest and Republic fly for more than one brand, so they stay unassigned.

## The seven rules, and where each one lives

`docs/definitions.md` and `DOC_PHRASES` (`src/flight_analyst/definitions.py`). `briefing_block()` puts them in the prompt. `prose_rubric()` (`compare.py`, called from `evaluate.py`) marks a thin sentence `misread_question` even when the rows match.

| Rule | Where it bites |
| --- | --- |
| Answer literally, then offer a better question | Prompt. `compose_answer` in `analyst.py` adds "Follow-up:". Advisory nudge; does not change pass or fail. |
| A number for one item means a number for every item, with a unit | Grader: every expected label, plus percent, minutes, or flights. |
| Ambiguous questions: literal answer, the reading, and the other cuts | Reference SQL returns the extra cuts. The answer must contain "I read this as". |
| Name every route, airport, and airline. One direction is not the reverse | Separate named rows. The briefing says the same. |
| If cause codes cannot say why, say so | Prompt, plus `why_limit` on cause questions, printed on the site. |
| Small samples do not win. State the floor | SQL `HAVING` floors. `MINIMUM_RULE` on every ranking, in the prompt and on the site. |
| Different wording, same figures | Variant questions share one SQL string (`tests/test_review.py`). |

## Stub scores, before and after

These are **stub** runs. No model was called. A live-model score is **pending**. Daniel's recheck of the 10 revised questions is also **pending**.

| | 10 Oct 2026 | 11 Oct 2026, after the review |
| --- | --- | --- |
| Report | `reports/eval-20261010T201320+0000.json` | `reports/eval-20261011T231046+0000.json` |
| Matched | 105 of 111 (94.6%) | 116 of 120 (96.7%) |
| Easy / medium / hard | 32/32, 50/51, 23/28 | 32/32, 56/56, 28/32 |
| Misses | 6 × `misread_question` | 4 × `misread_question` |
| Advisory nudge | not scored | 120 of 120 |

Do not read 94.6% → 96.7% as the analyst improving. Ten keys changed, the bank grew from 111 to 120, and the stub was edited to emit the new shapes. Two old misses flipped because the stub learned them: "How late is American?" and the highest-cancellation month (an airline rule had been stealing it). Still unimplemented: median delay (q020), morning-minus-evening (q037), Thanksgiving versus October Wednesdays (q079), and whether weather explains most delays (q101). Nudge 120 of 120 is the composer appending a follow-up on every answer, including refusals.

## How I'd explain this in an interview

**Sixty seconds.** The grader only checked the table, and every number was still unsigned. Daniel reviewed 23 questions and wrote seven rules: answer the words asked, show the other cut, unit every figure, and say when the file cannot explain why. I locked the 13 he accepted, rewrote the 10 he returned, and put the rules in the prompt and the grader. The follow-up is advice, so it cannot inflate accuracy. Result: 120 questions, 13 approved, 10 awaiting recheck, 97 draft. A stub matches 116 of 120. A real model score is pending.

**Talking points**

- Human-in-the-loop: the analyst never sees the key. Status (`APPROVED`, `DRAFT-REVISED`, `DRAFT`) is the only promotion. A score against a draft key is a pipeline check.
- Ambiguity: state the reading and show the other cuts in the same answer.
- Trust is a limit you say out loud: no passenger weights, no marketing carrier for SkyWest and Republic, cause codes that cannot say why. A gap that survives a control is still not a cause.
- Keep the advisory nudge off the accuracy line. A line the template always adds must not move the headline number.
