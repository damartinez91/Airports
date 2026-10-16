"""Compare an analyst result to a draft candidate answer.

Scoring uses numbers, entity identity, and rank or set membership.
It does not compare the analyst's prose.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

METRIC_ALIASES = {
    "on_time_rate": ["on_time_rate", "ontime_rate", "on_time_arrival_rate", "arrival_on_time_rate", "otp"],
    "on_time_departure_rate": ["on_time_departure_rate", "departure_on_time_rate", "dep_on_time_rate"],
    "cancellation_rate": ["cancellation_rate", "cancel_rate"],
    "diversion_rate": ["diversion_rate", "diverted_rate"],
    "irregular_rate": ["irregular_rate", "cancelled_or_diverted_rate"],
    "severe_late_rate": ["severe_late_rate", "late_60_rate"],
    "share": ["share", "delay_share", "minute_share", "cause_share", "cancellation_share"],
    "carrier_only_share": ["carrier_only_share", "carrier_share"],
    "weather_share": ["weather_share", "weather_delay_share"],
    "nas_share": ["nas_share", "nas_delay_share"],
    "rate_gap": ["rate_gap", "morning_minus_evening", "gap"],
    "avg_arrival_delay": ["avg_arrival_delay", "average_arrival_delay", "mean_arrival_delay", "avg_arr_delay"],
    "median_arrival_delay": ["median_arrival_delay", "median_delay", "median_arr_delay"],
    "percentage_points": ["percentage_points", "percentage_point_gap", "pp"],
    "operated_flights": ["operated_flights", "n_operated", "flight_count"],
    "scheduled_flights": ["scheduled_flights", "n_scheduled", "scheduled"],
    "late_flights": ["late_flights", "n_late", "late_count"],
    "late_rate": ["late_rate", "late_arrival_rate"],
    "figure": ["figure", "gap", "on_time_rate"],
    "included": ["included", "is_included", "in_family"],
    "weather_is_majority": ["weather_is_majority", "is_majority", "weather_majority"],
}

NAME_ALIASES = {
    "american": "AA",
    "american airlines": "AA",
    "southwest": "WN",
    "southwest airlines": "WN",
    "delta": "DL",
    "delta air lines": "DL",
    "united": "UA",
    "united airlines": "UA",
    "jetblue": "B6",
    "jetblue airways": "B6",
    "alaska": "AS",
    "alaska airlines": "AS",
    "frontier": "F9",
    "frontier airlines": "F9",
    "allegiant": "G4",
    "allegiant air": "G4",
    "skywest": "OO",
    "skywest airlines": "OO",
    "skywest flies for several airlines": "OO",
    "american brand": "AA",
    "envoy": "MQ",
    "envoy air": "MQ",
    "republic": "YX",
    "republic airways": "YX",
    "psa": "OH",
    "psa airlines": "OH",
    "late aircraft": "late_aircraft",
    "late-aircraft": "late_aircraft",
    "airline controlled": "airline_controlled",
    "airline-controlled": "airline_controlled",
    "own fault": "airline_controlled",
    "national aviation system": "nas",
}


def _norm_text(value: str) -> str:
    text = value.lower().strip().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class EntityResolver:
    def __init__(self, name_to_code: dict[str, str] | None = None):
        self.name_to_code = {**NAME_ALIASES}
        for name, code in (name_to_code or {}).items():
            if name and code:
                self.name_to_code[_norm_text(str(name))] = str(code).upper()
        self.known_codes = {code.upper() for code in self.name_to_code.values()}

    @classmethod
    def from_pairs(cls, pairs: list[tuple[str, str]]) -> "EntityResolver":
        mapping = {}
        for code, name in pairs:
            mapping[str(code)] = str(code).upper()
            if name:
                mapping[str(name)] = str(code).upper()
        return cls(mapping)

    def canon(self, value) -> str | None:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, float):
            if math.isnan(value):
                return None
            if value.is_integer():
                value = int(value)
        if isinstance(value, int):
            return str(value)
        text = str(value).strip()
        if not text:
            return None
        date_match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
        if date_match:
            return text
        month_match = re.fullmatch(r"(\d{4})-(\d{1,2})", text)
        if month_match:
            return f"{month_match.group(1)}-{int(month_match.group(2)):02d}"
        route_match = re.fullmatch(r"([A-Za-z]{3})\s*(?:-|to|–|—)\s*([A-Za-z]{3})", text, flags=re.IGNORECASE)
        if route_match:
            return f"{route_match.group(1).upper()}-{route_match.group(2).upper()}"
        hour_match = re.fullmatch(r"(?:hour[_\s-]?)?(\d{1,2})(?::00)?", text, flags=re.IGNORECASE)
        if hour_match and 0 <= int(hour_match.group(1)) <= 23 and not re.fullmatch(r"\d{4}", text):
            # Avoid treating a bare small integer that is really a flag as an hour when
            # the caller passed a number. Strings that are only a count stay numeric above.
            if re.search(r"[A-Za-z:]", text) or (len(text) <= 2 and text.isdigit() and "hour" in text.lower()):
                return f"hour_{int(hour_match.group(1)):02d}"
            if text.lower().startswith("hour"):
                return f"hour_{int(hour_match.group(1)):02d}"
        if re.fullmatch(r"[A-Za-z0-9]{2}", text):
            return text.upper()
        if re.fullmatch(r"[A-Za-z]{3}", text):
            return text.upper()
        key = _norm_text(text)
        months = {
            "january": 1,
            "february": 2,
            "march": 3,
            "april": 4,
            "may": 5,
            "june": 6,
            "july": 7,
            "august": 8,
            "september": 9,
            "october": 10,
            "november": 11,
            "december": 12,
        }
        named_wednesdays = re.fullmatch(r"([a-z]+)\s+(\d{4})\s+wednesdays", key)
        if named_wednesdays and named_wednesdays.group(1) in months:
            month = months[named_wednesdays.group(1)]
            return f"{named_wednesdays.group(2)}-{month:02d}-wednesdays"
        numbered_wednesdays = re.fullmatch(r"(\d{4})\s+(\d{1,2})\s+wednesdays", key)
        if numbered_wednesdays and 1 <= int(numbered_wednesdays.group(2)) <= 12:
            return f"{numbered_wednesdays.group(1)}-{int(numbered_wednesdays.group(2)):02d}-wednesdays"
        if "150k" in key and ("large" in key or "or more" in key):
            return "150k_or_more"
        if "10k" in key and "49" in key:
            return "10k_to_49999"
        if key in {"other carriers", "other carrier", "all other carriers"}:
            return "other"
        if key in self.name_to_code:
            return self.name_to_code[key]
        for token in re.findall(r"\b([A-Za-z0-9]{2}|[A-Za-z]{3})\b", text):
            upper = token.upper()
            if upper in self.known_codes:
                return upper
        token = key.replace(" ", "_")
        return token or None


@dataclass
class Comparison:
    passed: bool
    reason: str
    detail: str
    expected_labels: list[str] = field(default_factory=list)
    actual_labels: list[str] = field(default_factory=list)


def _is_number(value) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return not (isinstance(value, float) and math.isnan(value))
    return False


def _close(actual, expected, spec, column_name: str) -> bool:
    if not _is_number(actual) or not _is_number(expected):
        return False
    actual_f = float(actual)
    expected_f = float(expected)
    tolerance = float(spec["tolerance"])
    options = [actual_f]
    unit = spec.get("unit", "fraction")
    name = (column_name or "").lower()
    countish = any(part in name for part in ("count", "flight", "n_late", "n_operated"))
    rateish = any(part in name for part in ("rate", "share", "pct", "percent", "ratio", "gap"))
    if unit == "fraction" and not countish and (rateish or 1.5 <= abs(actual_f) <= 100.5):
        options.append(actual_f / 100.0)
    return any(abs(option - expected_f) <= tolerance + 1e-9 for option in options)


def _alias_set(spec: dict) -> set[str]:
    names = [spec["name"], *spec.get("aliases", []), *METRIC_ALIASES.get(spec["name"], [])]
    return {name.lower() for name in names}


def _numeric_columns(row: dict, columns: list[str]) -> list[str]:
    return [column for column in columns if _is_number(row.get(column))]


def _rate_flip(actual, expected, spec) -> bool:
    """True when a fraction is the late share instead of the on-time share."""
    if spec.get("unit") != "fraction":
        return False
    if not _is_number(actual) or not _is_number(expected):
        return False
    tolerance = float(spec["tolerance"])
    complement = 1.0 - float(expected)
    options = [float(actual)]
    if abs(float(actual)) > 1.5:
        options.append(float(actual) / 100.0)
    return any(abs(option - complement) <= tolerance + 1e-9 for option in options)


def _value_detail(actual_row, expected_row, specs, columns) -> str:
    bits = []
    for spec, column in zip(specs, columns):
        actual = actual_row.get(column) if column else None
        expected = expected_row.get(spec["name"])
        note = " late-vs-on-time flip" if column and _rate_flip(actual, expected, spec) else ""
        bits.append(f"{spec['name']} expected {expected} actual {actual}{note}")
    return "; ".join(bits)


def _match_values(actual_row: dict, expected_row: dict, specs: list[dict], columns: list[str]) -> tuple[bool, str]:
    if not specs:
        return True, ""
    numeric = _numeric_columns(actual_row, columns)
    chosen = []
    missing = []
    for spec in specs:
        aliases = _alias_set(spec)
        hit = next((column for column in numeric if column.lower() in aliases), None)
        if hit:
            chosen.append(hit)
        else:
            chosen.append(None)
            missing.append(spec["name"])
    if not missing:
        if all(
            _close(actual_row.get(column), expected_row.get(spec["name"]), spec, column)
            for spec, column in zip(specs, chosen)
        ):
            return True, ""
        return False, _value_detail(actual_row, expected_row, specs, chosen)

    def window_ok(picked: list[str]) -> bool:
        return all(
            _close(actual_row.get(column), expected_row.get(spec["name"]), spec, column)
            for spec, column in zip(specs, picked)
        )

    # Positional alignment only when no metric name matched. A named metric
    # beside an unnamed extra column is not a substitute for a missing one.
    if chosen.count(None) == len(specs) and len(numeric) >= len(specs):
        prefix = numeric[: len(specs)]
        suffix = numeric[-len(specs) :]
        if window_ok(prefix):
            return True, ""
        if suffix != prefix and window_ok(suffix):
            return True, ""
        return False, _value_detail(actual_row, expected_row, specs, prefix)
    if missing:
        return False, "missing " + ", ".join(missing)
    return False, "not enough numeric columns"


def _label_candidates(rows: list[dict], columns: list[str]) -> list[tuple[str, list]]:
    candidates = [(column, [row.get(column) for row in rows]) for column in columns]
    if "origin" in columns and "dest" in columns:
        candidates.append(("origin-dest", [f"{row.get('origin')}-{row.get('dest')}" for row in rows]))
    if "year" in columns and "month" in columns:
        synthesized = []
        for row in rows:
            year, month = row.get("year"), row.get("month")
            if year is None or month is None:
                synthesized.append(None)
            else:
                synthesized.append(f"{int(year):04d}-{int(month):02d}")
        candidates.append(("year-month", synthesized))
    return candidates


def _refine_label(value, resolver: EntityResolver, expected_set: set[str]) -> str | None:
    label = resolver.canon(value)
    if label in expected_set:
        return label
    text = str(value).strip() if value is not None and not isinstance(value, bool) else ""
    if re.fullmatch(r"\d{1,2}", text):
        hour = f"hour_{int(text):02d}"
        if hour in expected_set:
            return hour
    named_month = re.fullmatch(r"(\d{1,2})\s*\(\s*([A-Za-z]+)\s*\)", text)
    if named_month:
        month_names = {
            "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
            "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
        }
        month_number = month_names.get(named_month.group(2).lower())
        if month_number:
            hits = [item for item in expected_set if re.fullmatch(rf"\d{{4}}-{month_number:02d}", str(item))]
            if len(hits) == 1:
                return hits[0]
    return label


def _month_labels(rows: list[dict], expected: list[str]) -> list[str | None]:
    """Map a month column onto YYYY-MM when that candidate label is the only match."""
    labels = []
    for row in rows:
        month = row.get("month")
        found = None
        if isinstance(month, (int, float)) and not isinstance(month, bool) and float(month).is_integer():
            month_number = int(month)
            year = row.get("year")
            hits = []
            for label in expected:
                match = re.fullmatch(r"(\d{4})-(\d{2})", str(label))
                if not match or int(match.group(2)) != month_number:
                    continue
                if year is None or int(year) == int(match.group(1)):
                    hits.append(label)
            if len(hits) == 1:
                found = hits[0]
        labels.append(found)
    return labels


def _choose_labels(rows, columns, resolver: EntityResolver, expected: list[str]) -> list[str] | None:
    expected_set = set(expected)
    best_name = None
    best_labels: list[str] | None = None
    best_score = -1
    best_bonus = -1
    candidates = _label_candidates(rows, columns)
    month_labels = _month_labels(rows, expected)
    if any(month_labels):
        candidates.append(("month-number", month_labels))
    for name, raw_values in candidates:
        if name == "month-number":
            labels = list(raw_values)
        else:
            labels = [_refine_label(value, resolver, expected_set) for value in raw_values]
        score = sum(1 for label in labels if label in expected_set)
        bonus = 1 if name == "label" else 0
        if score > best_score or (score == best_score and bonus > best_bonus):
            best_score = score
            best_bonus = bonus
            best_name = name
            best_labels = labels
    if best_labels is None or best_score <= 0:
        return None
    return best_labels


def _spec_value(row: dict, spec: dict, columns: list[str]):
    numeric = _numeric_columns(row, columns)
    aliases = _alias_set(spec)
    hit = next((column for column in numeric if column.lower() in aliases), None)
    if hit is None:
        return None
    return row.get(hit)


def compare_result(question: dict, result, resolver: EntityResolver | None = None) -> Comparison:
    """Grade required rows.

    Every candidate row has to be present, with its numbers inside the existing
    tolerance. On a ranking the literal answer is the first row, and the required
    labels stay in that order. Extra rows are allowed: major carriers, the other
    direction, and other documented cuts. An extra copy of a required entity, or
    two copies of any other entity, fail when the numbers disagree.
    """
    resolver = resolver or EntityResolver()
    if question.get("answer_type") == "refusal":
        if result.refused and not result.rows:
            return Comparison(True, "ok", "Analyst refused.")
        return Comparison(False, "not_refused", "This question asks for a refusal.")

    rows = list(result.rows or [])
    columns = list(result.columns or (list(rows[0].keys()) if rows else []))
    expected_rows = question.get("expected", {}).get("rows", [])
    if not rows:
        return Comparison(False, "empty", "Analyst SQL returned no rows.")

    keyed = bool(question.get("key_columns"))
    raw_expected = [row.get("label") for row in expected_rows] if keyed else []
    expected_labels = [resolver.canon(label) for label in raw_expected] if keyed else []
    if keyed and any(label is None for label in expected_labels):
        return Comparison(False, "key_mismatch", "Candidate label could not be resolved.", expected_labels, [])
    actual_labels = _choose_labels(rows, columns, resolver, expected_labels) if keyed else [""] * len(rows)
    if keyed and actual_labels is None:
        # One unlabeled row can still be the scalar answer when its numbers match.
        unlabeled = len(rows) == 1 and resolver.canon(rows[0].get("label")) is None
        if unlabeled and len(expected_rows) == 1:
            ok, detail = _match_values(rows[0], expected_rows[0], question.get("values") or [], columns)
            if ok:
                actual_labels = list(expected_labels)
            else:
                reason = "no_numeric" if "numeric" in detail else "value_mismatch"
                return Comparison(False, reason, detail, expected_labels, [])
        else:
            return Comparison(False, "key_mismatch", "No result column matched the candidate entities.", expected_labels, [])

    specs = question.get("values") or []

    if not keyed:
        if len(expected_rows) != 1:
            return Comparison(False, "shape", "Unkeyed comparison expected one row.")
        ok, detail = _match_values(rows[0], expected_rows[0], specs, columns)
        if ok:
            return Comparison(True, "ok", "Metrics match.")
        reason = "no_numeric" if "numeric" in detail else "value_mismatch"
        return Comparison(False, reason, detail)

    expected_set = set(expected_labels)
    present = set(actual_labels)
    missing = [str(raw) for raw, label in zip(raw_expected, expected_labels) if label not in present]
    if missing:
        return Comparison(
            False,
            "key_mismatch",
            f"Missing required labels {missing}. Expected {raw_expected}, got {actual_labels}.",
            expected_labels,
            actual_labels,
        )

    expected_by_label = {}
    for label, row in zip(expected_labels, expected_rows):
        expected_by_label.setdefault(label, row)
    for label, expected_row in expected_by_label.items():
        matched = [index for index, actual in enumerate(actual_labels) if actual == label]
        for index in matched:
            ok, detail = _match_values(rows[index], expected_row, specs, columns)
            if not ok:
                reason = "no_numeric" if "numeric" in detail else "value_mismatch"
                return Comparison(False, reason, f"{label}: {detail}", expected_labels, actual_labels)

    extras: dict[str, list[int]] = {}
    for index, label in enumerate(actual_labels):
        if label is None or label in expected_set:
            continue
        extras.setdefault(label, []).append(index)
    for label, indexes in extras.items():
        if len(indexes) < 2:
            continue
        for spec in specs:
            values = [(index, _spec_value(rows[index], spec, columns)) for index in indexes]
            present_values = [(index, value) for index, value in values if value is not None]
            if len(present_values) < 2:
                continue
            base = present_values[0][1]
            for index, value in present_values[1:]:
                if not _close(value, base, spec, spec["name"]):
                    return Comparison(
                        False,
                        "value_mismatch",
                        f"Contradictory extra values for {label}: {spec['name']} {base} and {value}.",
                        expected_labels,
                        actual_labels,
                    )

    if question.get("answer_type") == "ranking":
        if actual_labels[0] != expected_labels[0]:
            return Comparison(
                False,
                "order_mismatch",
                f"The literal answer was not first. Expected {expected_labels[0]} first, got {actual_labels}.",
                expected_labels,
                actual_labels,
            )
        collapsed = []
        seen = set()
        for label in actual_labels:
            if label in expected_set and label not in seen:
                collapsed.append(label)
                seen.add(label)
        if collapsed != expected_labels:
            return Comparison(
                False,
                "order_mismatch",
                f"Required ranking order differs. Expected {expected_labels}, got {actual_labels}.",
                expected_labels,
                actual_labels,
            )
    return Comparison(True, "ok", "Metrics match.", expected_labels, actual_labels)


_UNIT_WORDS = re.compile(r"\b(percent|minutes|flights)\b", re.IGNORECASE)
_NUDGE = re.compile(r"follow-up\s*:", re.IGNORECASE)
_READING = re.compile(r"i read this as", re.IGNORECASE)


def _label_mentioned(label: str, text: str, resolver: EntityResolver) -> bool:
    if label.lower() in text.lower():
        return True
    target = resolver.canon(label)
    if not target:
        return False
    if re.search(rf"\b{re.escape(target)}\b", text, flags=re.IGNORECASE):
        return True
    low = f" {_norm_text(text)} "
    for alias, code in resolver.name_to_code.items():
        if code == target and alias and f" {alias} " in low:
            return True
    for line in text.splitlines():
        head = line.split(":", 1)[0].strip()
        if resolver.canon(head) == target:
            return True
        if re.fullmatch(r"\d{1,2}", head) and target == f"hour_{int(head):02d}":
            return True
        if target and _refine_label(head, resolver, {target}) == target:
            return True
    month = re.fullmatch(r"(\d{4})-(\d{2})", target or "")
    if month and re.search(rf"\bmonth\s+0?{int(month.group(2))}\b", text, flags=re.IGNORECASE):
        return True
    return False


def prose_rubric(question: dict, answer_text: str | None, resolver: EntityResolver | None = None) -> tuple[bool, str, bool]:
    """Check the spoken answer. The third value is the advisory nudge, not a pass/fail.

    Ambiguous questions need the required entities, a stated reading, and a unit.
    A code, a known name, or the same canon label counts. Extra entities are fine.
    """
    text = answer_text or ""
    nudge = bool(_NUDGE.search(text))
    if question.get("answer_type") == "refusal":
        return True, "", nudge
    rows = (question.get("expected") or {}).get("rows") or []
    if not rows:
        return True, "", nudge
    resolver = resolver or EntityResolver()
    missing = []
    for row in rows:
        label = str(row.get("label", ""))
        if label.lower() == "systemwide" and len(rows) == 1:
            continue
        if not _label_mentioned(label, text, resolver):
            missing.append(label)
    if missing:
        return False, "The answer left out " + ", ".join(missing) + ".", nudge
    if not _UNIT_WORDS.search(text):
        return False, "The answer gave figures without units.", nudge
    needs_reading = bool(question.get("ambiguous")) or bool(question.get("alternatives"))
    if needs_reading and not _READING.search(text):
        return False, "The answer did not state how it read the question.", nudge
    for phrase in question.get("required_phrases") or []:
        if phrase.lower() not in text.lower():
            return False, f"The answer left out the required note: {phrase}", nudge
    return True, "", nudge


def categorize(question: dict, result, comparison: Comparison | None) -> str | None:
    """Map a miss onto the draft failure taxonomy. A pass returns None."""
    if comparison is not None and comparison.passed:
        return None
    if getattr(result, "infrastructure_error", None):
        return "error"
    if question.get("answer_type") == "refusal":
        return "misread_question"
    if getattr(result, "refused", False):
        return "refused"
    if getattr(result, "sql_error", None) or not (getattr(result, "sql", None) or "").strip():
        return "wrong_sql"
    if comparison is None:
        return "error"
    if comparison.reason in {"value_mismatch", "order_mismatch"}:
        return "wrong_aggregation"
    if comparison.reason in {"key_mismatch", "shape", "empty", "no_numeric", "not_refused"}:
        return "misread_question"
    return "error"


_NUMBER_FIELD = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)\s+"
    r"([-+]?(?:\d+(?:\.\d*)?|\.\d+))"
    r"(?:\s+(percent|minutes|flights))?"
)
_CODE_FIELD = re.compile(r"\b(airline_code|origin|dest)\s+([A-Za-z0-9]{2,3})\b")
_TEXT_FIELD = re.compile(
    r"\b(dep_time_bucket|season|category|route|airline_name|airport_name|origin_airport_name|dest_airport_name|who)\s+([^,\n]+)"
)
_SKIP_LABEL = re.compile(r"^(?:i read|units|follow-up)\b", re.IGNORECASE)


def rows_from_answer(text: str) -> tuple[list[dict], list[str]]:
    """Rebuild result rows from a saved spoken answer. Percent figures become fractions."""
    rows = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if ":" not in line:
            continue
        label, rest = line.split(":", 1)
        label = label.strip()
        if _SKIP_LABEL.match(label):
            continue
        fields: dict = {}
        for match in _NUMBER_FIELD.finditer(rest):
            name, num, unit = match.group(1), match.group(2), match.group(3)
            if unit == "percent":
                value = float(num) / 100.0
            elif "." in num:
                value = float(num)
            else:
                value = int(num)
            fields[name] = value
        for match in _CODE_FIELD.finditer(rest):
            fields[match.group(1)] = match.group(2)
        for match in _TEXT_FIELD.finditer(rest):
            fields[match.group(1)] = match.group(2).strip()
        if not fields:
            continue
        rows.append({"label": label, **fields})
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    return rows, columns
