from pathlib import Path

from flight_analyst.analyst import parse_model_payload
from flight_analyst.llm import (
    StubClient,
    build_anthropic_payload,
    build_openai_payload,
    get_client,
    parse_anthropic_response,
    parse_openai_response,
    plan_question,
)

ROOT = Path(__file__).resolve().parents[1]


def test_analyst_source_does_not_open_the_bank():
    for name in ("analyst.py", "llm.py"):
        text = (ROOT / "src" / "flight_analyst" / name).read_text()
        assert "bank.json" not in text
        assert "load_bank" not in text
        assert "expected_rows" not in text


def test_stub_answers_the_dfw_question_without_reading_answers():
    payload = plan_question("Which airline is most on time out of DFW?")
    assert payload["refuse"] is False
    assert "DFW" in payload["sql"]
    assert "on_time_rate" in payload["sql"]
    assert "100" in payload["sql"]


def test_stub_refuses_live_and_passenger_questions():
    yesterday = plan_question("What was the on-time rate yesterday?")
    assert yesterday["refuse"] is True
    weighted = plan_question("What is the passenger-weighted on-time arrival rate at ATL?")
    assert weighted["refuse"] is True


def test_openai_and_anthropic_payloads_are_shaped_for_their_apis():
    openai = build_openai_payload("gpt-4o-mini", "system", "user question")
    assert openai["messages"][0]["role"] == "system"
    assert openai["messages"][1]["content"] == "user question"
    assert parse_openai_response({"choices": [{"message": {"content": "{\"sql\": \"SELECT 1\"}"}}]}) == "{\"sql\": \"SELECT 1\"}"
    anthropic = build_anthropic_payload("claude-sonnet-4-5", "system", "user question")
    assert anthropic["system"] == "system"
    assert parse_anthropic_response({"content": [{"text": "ok"}]}) == "ok"


def test_missing_key_selects_the_stub(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    client = get_client()
    assert isinstance(client, StubClient)


def test_openai_without_a_key_fails_clearly(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    try:
        get_client()
    except Exception as exc:
        assert "OPENAI_API_KEY" in str(exc)
    else:
        raise AssertionError("expected a missing-key error")


def test_parse_fenced_json():
    payload = parse_model_payload('```json\n{"sql": "SELECT 1 AS n FROM flights_v", "refuse": false}\n```')
    assert payload["sql"].startswith("SELECT 1")
