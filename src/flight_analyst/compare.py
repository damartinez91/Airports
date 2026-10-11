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


def _match_values(actual_row: dict, expected_row: dict, specs: list[dict], columns: list[str]) -> tuple[bool, str]:
    if not specs:
        return True, ""
    numeric = _numeric_columns(actual_row, columns)
    alias_hits = []
    for spec in specs:
        aliases = _alias_set(spec)
        hit = next((column for column in numeric if column.lower() in aliases), None)
        alias_hits.append(hit)
    if all(alias_hits):
        chosen = alias_hits
        if all(_close(actual_row.get(column), expected_row.get(spec["name"]), spec, column) for spec, column in zip(specs, chosen)):
            return True, ""
        detail = _value_detail(actual_row, expected_row, specs, chosen)
        return False, detail

    def window_ok(chosen: list[str]) -> bool:
        return all(
            _close(actual_row.get(column), expected_row.get(spec["name"]), spec, column)
            for spec, column in zip(specs, chosen)
        )

    if len(numeric) >= len(specs):
        prefix = numeric[: len(specs)]
        suffix = numeric[-len(specs) :]
        if window_ok(prefix) or window_ok(suffix):
            return True, ""
        detail = _value_detail(actual_row, expected_row, specs, prefix)
        return False, detail
    return False, "not enough numeric columns"


def _value_detail(actual_row, expected_row, specs, columns) -> str:
    bits = []
    for spec, column in zip(specs, columns):
        bits.append(
            f"{spec['name']} expected {expected_row.get(spec['name'])} actual {actual_row.get(column)}"
        )
    return "; ".join(bits)


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


def _choose_labels(rows, columns, resolver: EntityResolver, expected: list[str]) -> list[str] | None:
    expected_set = set(expected)
    best_name = None
    best_labels: list[str] | None = None
    best_score = -1
    for name, raw_values in _label_candidates(rows, columns):
        labels = [resolver.canon(value) for value in raw_values]
        if any(label is None for label in labels):
            continue
        score = sum(1 for label in labels if label in expected_set)
        bonus = 1 if name == "label" else 0
        if score > best_score or (score == best_score and bonus and best_name != "label"):
            best_score = score
            best_name = name
            best_labels = labels
    if best_labels is None or best_score <= 0:
        return None
    return best_labels


def compare_result(question: dict, result, resolver: EntityResolver | None = None) -> Comparison:
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
    if len(rows) != len(expected_rows):
        return Comparison(
            False,
            "shape",
            f"Expected {len(expected_rows)} rows and got {len(rows)}.",
        )

    keyed = bool(question.get("key_columns"))
    expected_labels = [resolver.canon(row.get("label")) for row in expected_rows] if keyed else []
    if keyed and any(label is None for label in expected_labels):
        return Comparison(False, "key_mismatch", "Candidate label could not be resolved.", expected_labels, [])
    actual_labels = _choose_labels(rows, columns, resolver, expected_labels) if keyed else [""] * len(rows)
    if keyed and actual_labels is None:
        return Comparison(False, "key_mismatch", "No result column matched the candidate entities.", expected_labels, [])

    specs = question.get("values") or []
    order = question.get("answer_type") == "ranking"

    def values_for(alignment: list[tuple[int, int]]) -> tuple[bool, str]:
        for actual_index, expected_index in alignment:
            ok, detail = _match_values(rows[actual_index], expected_rows[expected_index], specs, columns)
            if not ok:
                return False, detail or "values differed"
        return True, ""

    if not keyed:
        ok, detail = values_for([(0, 0)] if len(rows) == 1 else list(enumerate(range(len(rows)))))
        # Unkeyed questions in this bank are not used; keep a direct row-order check.
        if len(rows) != 1:
            return Comparison(False, "shape", "Unkeyed comparison expected one row.")
        ok, detail = _match_values(rows[0], expected_rows[0], specs, columns)
        if ok:
            return Comparison(True, "ok", "Metrics match.")
        return Comparison(False, "value_mismatch" if "numeric" not in detail else "no_numeric", detail)

    pairs = {label: index for index, label in enumerate(actual_labels)}
    if len(pairs) != len(actual_labels):
        return Comparison(False, "key_mismatch", "Duplicate labels in the analyst result.", expected_labels, actual_labels)
    if set(actual_labels) != set(expected_labels):
        return Comparison(
            False,
            "key_mismatch",
            f"Expected labels {expected_labels} and got {actual_labels}.",
            expected_labels,
            actual_labels,
        )
    alignment = [(pairs[label], index) for index, label in enumerate(expected_labels)]
    ok, detail = values_for(alignment)
    if not ok:
        reason = "no_numeric" if "numeric" in detail else "value_mismatch"
        return Comparison(False, reason, detail, expected_labels, actual_labels)
    if order and actual_labels != expected_labels:
        return Comparison(
            False,
            "order_mismatch",
            f"Entities match but order differs. Expected {expected_labels}, got {actual_labels}.",
            expected_labels,
            actual_labels,
        )
    return Comparison(True, "ok", "Metrics match.", expected_labels, actual_labels)


_UNIT_WORDS = re.compile(r"\b(percent|minutes|flights)\b", re.IGNORECASE)
_NUDGE = re.compile(r"follow-up\s*:", re.IGNORECASE)
_READING = re.compile(r"i read this as", re.IGNORECASE)


def prose_rubric(question: dict, answer_text: str | None) -> tuple[bool, str, bool]:
    """Check the spoken answer. The third value is the advisory nudge, not a pass/fail.

    Ambiguous questions need the literal labels, a stated reading, and a unit.
    Every expected label has to appear, so a number for one item is not enough.
    """
    text = answer_text or ""
    nudge = bool(_NUDGE.search(text))
    if question.get("answer_type") == "refusal":
        return True, "", nudge
    rows = (question.get("expected") or {}).get("rows") or []
    if not rows:
        return True, "", nudge
    missing = [str(row.get("label")) for row in rows if str(row.get("label", "")).lower() not in text.lower()]
    if missing:
        return False, "The answer left out " + ", ".join(missing) + ".", nudge
    if not _UNIT_WORDS.search(text):
        return False, "The answer gave figures without units.", nudge
    needs_reading = bool(question.get("ambiguous")) or bool(question.get("alternatives"))
    if needs_reading and not _READING.search(text):
        return False, "The answer did not state how it read the question.", nudge
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
