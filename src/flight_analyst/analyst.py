"""Analyst agent. It sees the question, the schema, and the definitions. It does not see candidate answers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from flight_analyst.definitions import briefing_block
from flight_analyst.ingest import connect_readonly
from flight_analyst.llm import StubClient, get_client
from flight_analyst.schema import FLIGHT_COLUMNS, VIEW_EXTRA
from flight_analyst.sql_guard import SqlGuardError, prepare_sql

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


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


def build_system_prompt(window_line: str) -> str:
    columns = "\n".join(f"- {name} ({typ}): {desc}" for name, typ, desc in [*FLIGHT_COLUMNS, *VIEW_EXTRA])
    return f"""You write one DuckDB query for a hiring-manager question about US airline on-time performance.
Use flights_v. You may join carriers, airports, and carrier_families.
Data window: {window_line}

Columns:
{columns}

{briefing_block()}

Respond with JSON only, no markdown:
{{"sql": "SELECT ...", "refuse": false, "refusal_reason": null, "chart": false}}
Set refuse to true and sql to null when the dataset cannot answer the question.
"""


def parse_model_payload(raw: str) -> dict:
    text = (raw or "").strip()
    text = _FENCE.sub("", text).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            start = re.search(r"\b(select|with)\b", text, flags=re.IGNORECASE)
            if not start:
                raise SqlGuardError("Model response did not contain SQL.")
            payload = {"sql": text[start.start():], "refuse": False}
        else:
            payload = json.loads(match.group(0))
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


def _format(rows: list[dict], columns: list[str]) -> str:
    if not rows:
        return "No rows."
    shown = rows[:8]
    rendered = []
    for index, row in enumerate(shown, start=1):
        bits = [f"{column}={row.get(column)}" for column in columns]
        rendered.append(f"{index}. " + ", ".join(bits))
    if len(rows) > len(shown):
        rendered.append(f"... {len(rows) - len(shown)} more rows")
    return "\n".join(rendered)


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
            payload = parse_model_payload(raw)
            if payload.get("refuse"):
                result.refused = True
                result.refusal_reason = payload.get("refusal_reason") or "Refused."
                result.answer_text = result.refusal_reason
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
            result.answer_text = _format(result.rows, result.columns)
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
