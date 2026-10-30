#!/usr/bin/env python3
"""Run the answering-rule checks. Exit 1 when one fails.

No model call. Set GUARDRAIL_BASE to the pull-request base commit when the
answer-key diff should be judged against that commit. On a feature branch
with no variable set, the script uses origin/main.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from flight_analyst.analyst import build_system_prompt  # noqa: E402
from flight_analyst.bank import load_bank  # noqa: E402
from flight_analyst.guardrails import (  # noqa: E402
    ANSWER_KEY_PATHS,
    BASELINE_PATH,
    EDITS_LOG,
    accuracy_problems,
    baseline_moved_down,
    brand_mapping_problems,
    gitignore_problems,
    grade_stub,
    load_baseline,
    ranking_floor_problems,
    review_marker_problems,
    route_shape_problems,
    tracked_file_problems,
    why_limit_problems,
    worst_airport_problems,
)
from flight_analyst.paths import BANK_PATH  # noqa: E402
from flight_analyst.questions import build_questions  # noqa: E402


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _usable_base(raw: str) -> str | None:
    value = raw.strip()
    if not value or set(value) <= {"0"}:
        return None
    return value


def resolve_base() -> str | None:
    from_env = _usable_base(os.environ.get("GUARDRAIL_BASE", ""))
    if from_env:
        return from_env
    probe = _git("rev-parse", "--verify", "origin/main")
    if probe.returncode != 0:
        return None
    head = _git("rev-parse", "HEAD")
    if head.returncode != 0:
        return None
    if head.stdout.strip() == probe.stdout.strip():
        return None
    return "origin/main"


def _show(base: str, path: str) -> str | None:
    result = _git("show", f"{base}:{path}")
    if result.returncode != 0:
        return None
    return result.stdout


def diff_problems(base: str) -> list[str]:
    verify = _git("rev-parse", "--verify", base)
    if verify.returncode != 0:
        return [f"GUARDRAIL_BASE {base} is not a commit in this checkout, so the answer-key diff was not checked."]
    names = _git("diff", "--name-only", f"{base}...HEAD", "--", *ANSWER_KEY_PATHS)
    if names.returncode != 0:
        return [names.stderr.strip() or "git diff failed while checking the answer key."]
    changed = [line.strip() for line in names.stdout.splitlines() if line.strip()]
    current = (ROOT / BASELINE_PATH).read_text()
    lowered = baseline_moved_down(_show(base, BASELINE_PATH), current)
    edits = _git("diff", f"{base}...HEAD", "--", EDITS_LOG)
    if edits.returncode != 0:
        return [edits.stderr.strip() or "git diff failed while checking docs/bank-edits.md."]
    return review_marker_problems(
        key_paths_changed=changed,
        baseline_lowered=lowered,
        edits_diff=edits.stdout,
    )


def tree_problems() -> list[str]:
    problems = []
    problems.extend(gitignore_problems((ROOT / ".gitignore").read_text()))
    listed = _git("ls-files", "-z")
    if listed.returncode != 0:
        return [listed.stderr.strip() or "git ls-files failed."]
    files = []
    for name in listed.stdout.split("\0"):
        if not name:
            continue
        path = ROOT / name
        size = path.stat().st_size if path.is_file() else 0
        files.append((name, size))
    problems.extend(tracked_file_problems(files))
    questions = build_questions()
    problems.extend(brand_mapping_problems())
    problems.extend(ranking_floor_problems(questions))
    problems.extend(route_shape_problems(questions))
    problems.extend(worst_airport_problems(questions))
    problems.extend(why_limit_problems(questions, build_system_prompt("the loaded window")))
    bank = load_bank(BANK_PATH)
    document = json_load(ROOT / "results" / "stub.json")
    n_correct, n, _misses = grade_stub(bank, document)
    problems.extend(accuracy_problems(n_correct, n, load_baseline()))
    return problems


def json_load(path: Path) -> dict:
    return json.loads(path.read_text())


def main() -> int:
    problems = tree_problems()
    base = resolve_base()
    if base:
        problems.extend(diff_problems(base))
        compared = f"diff against {base}"
    else:
        compared = "no base commit, so the answer-key diff was skipped"
    if problems:
        print(f"Guardrails failed ({compared}).")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print(f"Guardrails passed ({compared}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
