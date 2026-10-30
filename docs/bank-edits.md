# Answer-key edits

The grader's answer key is `questions/bank.json`. The reference SQL that produces it lives in `src/flight_analyst/questions.py`. `questions/anticipated_bank.json` is the cached-answer file the router reads.

Changing any of those files is a review event. In the same change, add one new line to this file, starting at the first column:

    BANK-EDIT: what changed, in one sentence. The bank stays draft until Daniel reviews it.

Lowering `min_correct` or `n` in `tests/accuracy_baseline.json` uses a second marker, also at the first column:

    BASELINE-EDIT: why the accuracy floor moved, in one sentence.

The guardrail check looks for a newly added line. A line that was already in this file does not count, and an indented example does not count. The check fails when the key or the floor moves and the new line is missing.
