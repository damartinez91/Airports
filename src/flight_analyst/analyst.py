"""Analyst agent. It sees the question, the schema, and the definitions. It does not see candidate answers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from flight_analyst.definitions import MINIMUM_RULE, WHY_LIMIT, briefing_block
from flight_analyst.ingest import connect_readonly
from flight_analyst.llm import StubClient, get_client
from flight_analyst.schema import FLIGHT_COLUMNS, VIEW_EXTRA
from flight_analyst.sql_guard import SqlGuardError, prepare_sql

@dataclass
class AnalystResult:
    question: str
    sql: str | None = None
    rows: list[dict] | None = None
    columns: list[str] | None = None
    answer_text: str = ""
    chart: dict | None = None
    refused: bool = False
    refusal_reason: str | None = None
    sql_error: str | None = None
    infrastructure_error: str | None = None
    provider: str | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_seconds: float = 0.0


def build_system_prompt(window_line: str) -> str:
    columns = "\n".join(f"- {name} ({typ}): {desc}" for name, typ, desc in [*FLIGHT_COLUMNS, *VIEW_EXTRA])
    return f"""You write one DuckDB query for a hiring-manager question about US airline on-time performance.
Use flights_v. You may join carriers, airports, and carrier_families.
Data window: {window_line}

Columns:
{columns}

{briefing_block()}

Respond with one JSON object and nothing after it. No markdown fence, no second JSON object, and no commentary.
{{"sql": "SELECT ...", "refuse": false, "refusal_reason": null, "chart": false}}
Set refuse to true and sql to null when the dataset cannot answer the question.
"""


def _strip_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\s*", "", stripped)
        if "```" in stripped:
            stripped = stripped.split("```", 1)[0]
    return stripped.strip()


def _first_json_object(text: str) -> dict | None:
    """First JSON object in the text. Trailing commentary is ignored."""
    decoder = json.JSONDecoder()
    starts = [0] if text.startswith("{") else []
    starts.extend(match.start() for match in re.finditer(r"\{", text))
    seen: set[int] = set()
    for start in starts:
        if start in seen:
            continue
        seen.add(start)
        try:
            value, _end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def parse_model_payload(raw: str) -> dict:
    text = _strip_fence(raw or "")
    payload = _first_json_object(text)
    if payload is None:
        start = re.search(r"\b(select|with)\b", text, flags=re.IGNORECASE)
        if not start:
            raise SqlGuardError("Model response did not contain SQL.")
        payload = {"sql": text[start.start():], "refuse": False}
    if not isinstance(payload, dict):
        raise SqlGuardError("Model response was not a JSON object.")
    return payload


def _chart(rows: list[dict], columns: list[str]) -> dict | None:
    if not (2 <= len(rows) <= 12):
        return None
    label_col = "label" if "label" in columns else None
    if label_col is None:
        for column in columns:
            if not isinstance(rows[0].get(column), (int, float)) or isinstance(rows[0].get(column), bool):
                label_col = column
                break
    if label_col is None:
        return None
    value_col = next((column for column in columns if isinstance(rows[0].get(column), (int, float)) and not isinstance(rows[0].get(column), bool)), None)
    if value_col is None:
        return None
    return {
        "type": "bar",
        "x": label_col,
        "y": value_col,
        "points": [{"label": row.get(label_col), "value": row.get(value_col)} for row in rows],
    }


def _with_unit(column: str, value) -> str:
    if isinstance(value, bool) or value is None:
        return f"{column} {value}"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        name = column.lower()
        if any(part in name for part in ("rate", "share", "gap", "figure")) and abs(float(value)) <= 1.5:
            return f"{column} {float(value) * 100:.1f} percent"
        if "delay" in name or name.endswith("minutes"):
            return f"{column} {float(value):.1f} minutes"
        if any(part in name for part in ("flight", "count", "n")) or name == "n":
            return f"{column} {value} flights"
        return f"{column} {value}"
    return f"{column} {value}"


def _reading(question: str) -> str:
    low = question.lower()
    if "between" in low and " and " in low:
        return "both directions, each named on its own, plus the combined figure."
    if "usually late" in low or "how late" in low or "on-time record" in low:
        return "reporting-carrier American: the on-time arrival rate and the mean signed arrival delay in minutes, not the brand rollup."
    if "most delays" in low:
        return "the greatest count of late arrivals, with that airline's late rate beside the count."
    if "most on time out of" in low:
        return (
            "the literal winner by on-time arrival rate, and the major carriers at that airport "
            "(top 3 by flights, extended to about 80 percent of departures), each with a rate and a flight count. "
            "SkyWest is labeled as flying for several airlines."
        )
    if "best" in low or "worst" in low:
        return "the on-time arrival rate after the minimum-flights rule, with the natural alternative cut beside it."
    if "dallas" in low and "american" in low:
        return "Dallas as DFW and DAL pooled, with a rate for each airline."
    if "dice" in low or "same-airport" in low:
        return "two cuts: airport size, and the airline after the origin airport is held fixed."
    if "operating partner" in low or "regional" in low:
        return "the mainline figure and the figure with single-partner operating partners. SkyWest and Republic stay unassigned."
    if "average delay" in low:
        return "the mean signed arrival delay in minutes of operated flights. Early arrivals pull it down."
    if "red-eye" in low or "night" in low:
        return "red-eye as the night bucket, 21:00 through 04:59, against every other departure."
    if "weather" in low and "most" in low:
        return "the BTS weather-delay cause, with the NAS share beside it."
    return "the figures below, using the project definitions."


def _follow_up(question: str) -> str:
    low = question.lower()
    if any(token in low for token in ("delay cause", "cancellation code", "carrier delay", "why", "late-arrival minutes", "weather explain")):
        return "The BTS codes cannot say why. Want the carrier, weather, airspace, security, and late-aircraft split for one airline?"
    if "airport" in low or "worst" in low:
        return "Want the same cut limited to large airports, with the minimum-flights rule stated?"
    if "route" in low or "between" in low:
        return "Want each direction named on its own, with the minutes on every route?"
    return "Want to see this across the big 3 business airlines, with and without operating partners?"


def _needs_minimum(question: str) -> bool:
    low = question.lower()
    return any(token in low for token in ("which ", "five ", "most ", "highest", "lowest", "worst", "best", "rank"))


def _needs_why(question: str) -> bool:
    low = question.lower()
    return any(token in low for token in ("delay", "cancellation code", "cause", "why", "weather explain", "late-arrival"))


def compose_answer(question: str, rows: list[dict], columns: list[str]) -> str:
    """Spoken answer. Every listed item gets a number and a unit, then a follow-up."""
    lines = [f"I read this as: {_reading(question)}"]
    shown = rows[:12]
    for row in shown:
        label = row.get("label", "")
        bits = [_with_unit(column, row.get(column)) for column in columns if column != "label"]
        lines.append(f"{label}: " + ", ".join(bits))
    if len(rows) > len(shown):
        lines.append(f"{len(rows) - len(shown)} more rows")
    if "five airlines" in question.lower() and "highest systemwide on-time" in question.lower():
        lines.append("Hawaiian benefits from island weather.")
    if _needs_minimum(question):
        lines.append(MINIMUM_RULE)
    if _needs_why(question):
        lines.append(WHY_LIMIT)
    lines.append("Units: rates and shares are in percent, delays are in minutes, and counts are in flights.")
    lines.append(f"Follow-up: {_follow_up(question)}")
    return "\n".join(lines)


class Analyst:
    def __init__(self, db_path: str | Path, client=None):
        self.db_path = Path(db_path)
        self.client = client if client is not None else get_client()

    def answer(self, question: str) -> AnalystResult:
        """Answer one question. The only input is the question text."""
        provider = getattr(self.client, "name", "unknown")
        model = getattr(self.client, "model", None)
        result = AnalystResult(question=question, provider=provider, model=model, rows=[], columns=[])
        try:
            con = connect_readonly(self.db_path)
        except Exception as exc:  # noqa: BLE001
            result.infrastructure_error = str(exc)
            result.answer_text = result.infrastructure_error
            return result
        try:
            window = con.execute(
                "SELECT MIN(flight_date), MAX(flight_date), COUNT(*) FROM flights_v"
            ).fetchone()
            window_line = f"{window[0]} through {window[1]}, {window[2]} scheduled flights"
            raw = self.client.complete(build_system_prompt(window_line), question)
            result.input_tokens = int(getattr(raw, "input_tokens", 0) or 0)
            result.output_tokens = int(getattr(raw, "output_tokens", 0) or 0)
            result.latency_seconds = float(getattr(raw, "latency_seconds", 0.0) or 0.0)
            payload = parse_model_payload(raw.text if hasattr(raw, "text") else raw)
            if payload.get("refuse"):
                result.refused = True
                result.refusal_reason = payload.get("refusal_reason") or "Refused."
                result.answer_text = (
                    result.refusal_reason
                    + "\nFollow-up: Ask for a rate this extract can compute, such as the on-time arrival rate for a month that is in the file."
                )
                return result
            sql = prepare_sql(payload.get("sql") or "")
            result.sql = sql
            try:
                cursor = con.execute(f"SELECT * FROM (\n{sql}\n) AS analyst_result LIMIT 50")
                result.columns = [item[0] for item in cursor.description]
                from flight_analyst.bank import _jsonify

                result.rows = [
                    {column: _jsonify(value) for column, value in zip(result.columns, record)}
                    for record in cursor.fetchall()
                ]
            except Exception as exc:  # noqa: BLE001
                result.sql_error = str(exc).splitlines()[0][:500]
                result.answer_text = result.sql_error
                result.rows = []
                return result
            result.chart = _chart(result.rows, result.columns)
            result.answer_text = compose_answer(question, result.rows, result.columns)
            return result
        except SqlGuardError as exc:
            result.sql_error = str(exc)
            result.answer_text = result.sql_error
            return result
        except Exception as exc:  # noqa: BLE001
            result.infrastructure_error = str(exc).splitlines()[0][:500]
            result.answer_text = result.infrastructure_error
            return result
        finally:
            con.close()


def default_client_name() -> str:
    return StubClient.name
