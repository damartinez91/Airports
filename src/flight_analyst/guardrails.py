"""Deterministic checks for the answering rules.

No model call and no flight database. The grader replays the saved stub
answers against the answer key. The other checks read SQL and the carrier map.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from flight_analyst.definitions import (
    LARGE_HUB_DEPARTURES,
    MIN_AIRLINE_AT_AIRPORT,
    MIN_AIRLINE_SYSTEMWIDE,
    MIN_AIRPORT,
    MIN_AIRPORT_BUCKET,
    MIN_BUCKET_EACH,
    MIN_CANCELLATIONS,
    MIN_HOUR_SYSTEM,
    MIN_LATE_FLIGHTS,
    MIN_ROUTE,
    MIN_ROUTE_FROM_AIRPORT,
    WHY_LIMIT,
)
from flight_analyst.paths import ROOT

# A change to any of these is an answer-key change. The same diff has to add
# a BANK-EDIT line to the edits log. See docs/bank-edits.md.
ANSWER_KEY_PATHS = (
    "questions/bank.json",
    "questions/anticipated_bank.json",
    "src/flight_analyst/questions.py",
)
BASELINE_PATH = "tests/accuracy_baseline.json"
EDITS_LOG = "docs/bank-edits.md"

# The only database that belongs in git. Everything else is a data file.
ALLOWED_DUCKDB = "tests/fixtures/sample_flights.duckdb"
MAX_TRACKED_BYTES = 5_000_000

GITIGNORE_LINES = (
    "/data/",
    "*.duckdb",
    "*.parquet",
    "*.csv",
    "!/tests/fixtures/sample_flights.duckdb",
)

# These questions have to say the codes cannot explain the operation.
WHY_QUESTION_FRAGMENTS = (
    "What can the BTS delay and cancellation codes tell us",
    "Does weather explain most delays?",
    "highest share of its late-arrival minutes labeled carrier delay",
    "highest share of its cancellations coded A",
)

# Display label, who they fly for, assignment, single-partner family or None, legal name.
BRAND_RULES = {
    "MQ": ("American Eagle", "American", "single_partner", "AA", "Envoy Air"),
    "OH": ("American Eagle", "American", "single_partner", "AA", "PSA Airlines"),
    "PT": ("American Eagle", "American", "single_partner", "AA", "Piedmont Airlines"),
    "YX": ("American Eagle", "several airlines", "unassigned", None, "Republic Airways"),
    "OO": ("SkyWest (flies for several airlines)", "several airlines", "unassigned", None, "SkyWest Airlines"),
    "YV": ("Mesa (flies for several airlines)", "several airlines", "unassigned", None, "Mesa Airlines"),
}

_CLOSED_GROUP = re.compile(
    r"GROUP BY (?:day_of_week|season|year, month|dep_time_bucket)\b"
)
_ORIGIN_EQ = re.compile(r"origin = '[A-Z]{3}'")
_DEST_EQ = re.compile(r"dest = '[A-Z]{3}'")
_BETWEEN = re.compile(r"between ([A-Z]{3}) and ([A-Z]{3})")
_DIRECTION = re.compile(r"([A-Z]{3}) to ([A-Z]{3}) or ([A-Z]{3}) to ([A-Z]{3})")
_MARKER = {
    "BANK-EDIT": re.compile(r"^\+BANK-EDIT:\s+\S", re.M),
    "BASELINE-EDIT": re.compile(r"^\+BASELINE-EDIT:\s+\S", re.M),
}


def _compact(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def _thresholds(sql: str) -> list[int]:
    return [int(number) for number in re.findall(r">=\s*(\d+)", sql)]


def _required_floor(compact: str) -> int | None:
    """Minimum `>= N` an open ranking must contain. None means a closed set."""
    if _CLOSED_GROUP.search(compact) or "GROUP BY" not in compact:
        return None
    if "origin IN (" in compact and "GROUP BY airline_code" not in compact:
        return None
    if (
        "COUNT(*) AS operated_flights" in compact
        and _ORIGIN_EQ.search(compact)
        and _DEST_EQ.search(compact)
    ):
        return None
    if "GROUP BY sched_dep_hour" in compact:
        return None
    if "GROUP BY origin, dest" in compact:
        if _ORIGIN_EQ.search(compact):
            return MIN_ROUTE_FROM_AIRPORT
        return MIN_ROUTE
    if "GROUP BY airline_code" in compact:
        if "cancellation_code" in compact and "cancelled" in compact:
            return MIN_CANCELLATIONS
        if "late_arrival" in compact and "carrier_delay" in compact:
            return MIN_LATE_FLIGHTS
        if _ORIGIN_EQ.search(compact):
            return MIN_AIRLINE_AT_AIRPORT
        return MIN_AIRLINE_SYSTEMWIDE
    if "GROUP BY origin" in compact:
        has_evening = "dep_time_bucket = 'evening'" in compact
        has_morning = "dep_time_bucket = 'morning'" in compact
        # A morning-minus-evening gap filters each bucket. A single bucket uses the airport floor.
        if has_evening and has_morning:
            return MIN_BUCKET_EACH
        if has_evening or has_morning:
            return MIN_AIRPORT_BUCKET
        return MIN_AIRPORT
    return None


def ranking_floor_problems(questions: list[dict]) -> list[str]:
    """Open rankings have to keep the sample floor. A closed list does not."""
    problems = []
    for item in questions:
        if item.get("answer_type") != "ranking":
            continue
        question = item.get("question") or "(untitled ranking)"
        compact = _compact(item.get("sql") or "")
        thresholds = _thresholds(compact)
        if "GROUP BY sched_dep_hour" in compact:
            stated = [int(number) for number in re.findall(r"at least (\d+)", question, flags=re.I)]
            if stated:
                need = stated[0]
                if need < MIN_AIRLINE_AT_AIRPORT:
                    problems.append(
                        f"{question} states a floor of {need}, under the {MIN_AIRLINE_AT_AIRPORT}-flight minimum."
                    )
                elif need not in thresholds:
                    problems.append(f"{question} says at least {need}, and the SQL dropped that floor.")
            elif not any(number >= MIN_HOUR_SYSTEM for number in thresholds):
                problems.append(
                    f"{question} needs a floor of at least {MIN_HOUR_SYSTEM} operated flights."
                )
            continue
        floor = _required_floor(compact)
        if floor is None:
            continue
        if not any(number >= floor for number in thresholds):
            problems.append(
                f"{question} can let a small sample win. The SQL needs a threshold of at least {floor}."
            )
    return problems


def check_brands(
    brand_map: dict,
    names: dict,
    family_codes: dict[str, str],
    dfw_sql: str,
) -> list[str]:
    """Envoy and Republic are American Eagle. SkyWest stays multi-airline."""
    problems = []
    for code, (brand, flies_for, assignment, family, legal_name) in BRAND_RULES.items():
        row = brand_map.get(code)
        if row != (brand, flies_for, assignment):
            problems.append(
                f"{code} ({legal_name}) must be brand {brand!r}, flies for {flies_for!r}, {assignment}. It is {row!r}."
            )
        if names.get(code) != legal_name:
            problems.append(f"{code} must keep the legal name {legal_name!r}. It is {names.get(code)!r}.")
        if family is None:
            if code in family_codes:
                problems.append(
                    f"{code} ({legal_name}) flies for several airlines and stays out of the single-partner families."
                )
        elif family_codes.get(code) != family:
            problems.append(f"{code} ({legal_name}) belongs to the {family} family. It is {family_codes.get(code)!r}.")
    if "Envoy (American Eagle)" not in dfw_sql:
        problems.append("The DFW 'best airline' SQL has to name Envoy as Envoy (American Eagle).")
    if "brand_skywest_unassigned" not in dfw_sql or "brand_republic_unassigned" not in dfw_sql:
        problems.append("The DFW brand rollup has to leave SkyWest and Republic unassigned.")
    if f">= {MIN_AIRLINE_AT_AIRPORT}" not in dfw_sql:
        problems.append(
            f"The DFW 'best airline' SQL dropped the {MIN_AIRLINE_AT_AIRPORT}-flight floor."
        )
    return problems


def brand_mapping_problems() -> list[str]:
    from flight_analyst.mcp_server import _BRAND
    from flight_analyst.questions import sql_best_out_of_dfw
    from flight_analyst.schema import CARRIER_FAMILIES, CARRIER_NAMES

    families = {code: family for code, family, _name, _role in CARRIER_FAMILIES}
    return check_brands(_BRAND, CARRIER_NAMES, families, sql_best_out_of_dfw())


def route_shape_problems(questions: list[dict]) -> list[str]:
    """One named direction stays one way. 'Between' returns both ways plus combined."""
    problems = []
    for item in questions:
        question = item.get("question") or ""
        sql = item.get("sql") or ""
        between = _BETWEEN.search(question)
        if between and "on time" in question.lower():
            origin, dest = between.group(1), between.group(2)
            for label in (f"{origin}-{dest}", f"{dest}-{origin}", "both-directions"):
                if label not in sql:
                    problems.append(f"{question} is missing the {label} row.")
        if question.lower().startswith("which direction is more on time"):
            pair = _DIRECTION.search(question)
            if pair is None:
                problems.append(f"{question} does not name both directions.")
                continue
            for label in (f"{pair.group(1)}-{pair.group(2)}", f"{pair.group(3)}-{pair.group(4)}"):
                if label not in sql:
                    problems.append(f"{question} is missing the {label} row.")
    return problems


def worst_airport_problems(questions: list[dict]) -> list[str]:
    """The worst airport overall sits next to the worst major hub."""
    matches = [item for item in questions if item.get("question") == "Which airport is the worst?"]
    if len(matches) != 1:
        return ["The question bank is missing 'Which airport is the worst?'."]
    sql = _compact(matches[0].get("sql") or "")
    problems = []
    if "overall_worst" not in sql:
        problems.append("The worst-airport answer dropped the overall airport.")
    if "large_hub_worst" not in sql:
        problems.append("The worst-airport answer dropped the worst major hub.")
    if f">= {MIN_AIRPORT}" not in sql:
        problems.append(f"The overall worst airport has to use the {MIN_AIRPORT}-departure floor.")
    hub = str(LARGE_HUB_DEPARTURES)
    if f">= {hub}" not in sql:
        problems.append(f"The worst major hub has to use the {hub}-departure floor.")
    return problems


def why_limit_problems(questions: list[dict], briefing: str) -> list[str]:
    """Coarse delay codes stay labeled as unable to explain the operation."""
    problems = []
    if "often cannot say why" not in briefing:
        problems.append("The analyst briefing dropped the line that delay codes often cannot say why.")
    if "cannot tell us the operational reason" not in WHY_LIMIT:
        problems.append("The delay-code limit no longer says the codes cannot explain the operation.")
    by_question = [(item.get("question") or "", item) for item in questions]
    for fragment in WHY_QUESTION_FRAGMENTS:
        matches = [item for text, item in by_question if fragment in text]
        if not matches:
            problems.append(f"Missing the question that contains {fragment!r}.")
            continue
        for item in matches:
            if item.get("why_limit") != WHY_LIMIT:
                problems.append(f"{item.get('question')} has to keep the delay-code limit on the answer.")
    return problems


def grade_stub(bank: dict, document: dict) -> tuple[int, int, list[str]]:
    """Re-score saved stub answers. Returns correct, total, and the miss ids."""
    from flight_analyst.evaluate import regrade_saved_run

    graded = regrade_saved_run(document, bank)
    block = graded["summary"]["all"]
    misses = [row["id"] for row in graded["results"] if not row["grade"]["passed"]]
    return int(block["n_correct"]), int(block["n"]), misses


def load_baseline(path: Path | None = None) -> dict:
    file = path or (ROOT / BASELINE_PATH)
    data = json.loads(file.read_text())
    return {"min_correct": int(data["min_correct"]), "n": int(data["n"])}


def accuracy_problems(n_correct: int, n: int, baseline: dict) -> list[str]:
    problems = []
    if n != baseline["n"]:
        problems.append(f"The stub grade covered {n} questions. The baseline is {baseline['n']}.")
    if n_correct < baseline["min_correct"]:
        problems.append(
            f"Stub accuracy fell to {n_correct} of {n}. The baseline is {baseline['min_correct']} of {baseline['n']}."
        )
    return problems


def gitignore_problems(text: str) -> list[str]:
    lines = {line.strip() for line in text.splitlines()}
    missing = [line for line in GITIGNORE_LINES if line not in lines]
    if not missing:
        return []
    return ["`.gitignore` is missing the data-file rules: " + ", ".join(missing)]


def tracked_file_problems(files: list[tuple[str, int]]) -> list[str]:
    """Reject committed data files. `files` is (repo-relative path, size in bytes)."""
    problems = []
    for path, size in files:
        normalized = path.replace("\\", "/")
        if normalized.startswith("data/") or normalized == "data":
            problems.append(f"{normalized} is under data/ and cannot be committed.")
        if normalized.endswith((".parquet", ".csv")):
            problems.append(f"{normalized} is a data file and cannot be committed.")
        if normalized.endswith((".duckdb", ".duckdb.wal")) and normalized != ALLOWED_DUCKDB:
            problems.append(
                f"{normalized} is a database. The only database in git is {ALLOWED_DUCKDB}."
            )
        if size > MAX_TRACKED_BYTES:
            problems.append(f"{normalized} is {size} bytes. The cap for a tracked file is {MAX_TRACKED_BYTES}.")
    return problems


def marker_present(diff_text: str, token: str) -> bool:
    """True when the diff adds a line that starts with the marker."""
    return _MARKER[token].search(diff_text or "") is not None


def review_marker_problems(
    *,
    key_paths_changed: list[str],
    baseline_lowered: bool,
    edits_diff: str,
) -> list[str]:
    problems = []
    if key_paths_changed and not marker_present(edits_diff, "BANK-EDIT"):
        names = ", ".join(key_paths_changed)
        problems.append(
            f"{names} changed, and docs/bank-edits.md has no new line that starts with BANK-EDIT:."
        )
    if baseline_lowered and not marker_present(edits_diff, "BASELINE-EDIT"):
        problems.append(
            "tests/accuracy_baseline.json lowered the floor, and docs/bank-edits.md has no new line "
            "that starts with BASELINE-EDIT:."
        )
    return problems


def baseline_moved_down(previous: str | None, current: str) -> bool:
    """True when min_correct or the question count dropped. A new file does not count."""
    if previous is None:
        return False
    old = json.loads(previous)
    new = json.loads(current)
    return int(new["min_correct"]) < int(old["min_correct"]) or int(new["n"]) < int(old["n"])
