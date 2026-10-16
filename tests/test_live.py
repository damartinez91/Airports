import json
import threading
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from flight_analyst.live import SpendLedger, build_service, make_server
from flight_analyst.__main__ import main


def _answer_factory():
    calls = []

    def answer(question):
        calls.append(question)
        return {"answer_text": "1. label=AA", "sql": "SELECT 1", "chart": None, "refused": False, "refusal_reason": None, "sql_error": None}

    return calls, answer


def test_live_command_stays_off_without_the_flag(monkeypatch):
    monkeypatch.delenv("LIVE_ASK", raising=False)
    with pytest.raises(SystemExit) as exc:
        main(["live"])
    assert exc.value.code == 2


def test_spend_cap_stops_before_the_model(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LIVE_SPEND_CAP_USD", "0.05")
    monkeypatch.setenv("LIVE_USD_PER_CALL", "0.05")
    calls, answer = _answer_factory()
    service = build_service(answer, tmp_path / "spend.json", provider="openai")
    status, body = service.handle({"question": "Which airline is most on time out of DFW?"}, "203.0.113.4")
    assert status == 200
    assert body["provider"] == "openai"
    assert body["spend"]["spent_usd"] == 0.05
    status, body = service.handle({"question": "Ask again"}, "203.0.113.9")
    assert status == 403
    assert body["error"] == "spend_cap"
    assert calls == ["Which airline is most on time out of DFW?"]


def test_stub_calls_are_free_and_keys_are_rejected(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LIVE_SPEND_CAP_USD", "0")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret")
    calls, answer = _answer_factory()
    service = build_service(answer, tmp_path / "spend.json", provider="stub")
    status, _body = service.handle({"question": "one"}, "203.0.113.4")
    assert status == 200
    status, body = service.handle({"question": "two", "api_key": "sk-test-secret", "provider": "anthropic"}, "203.0.113.4")
    assert status == 400
    assert "sk-test-secret" not in json.dumps(body)
    assert calls == ["one"]
    status, body = service.handle({"question": "three"}, "203.0.113.4")
    assert status == 200
    encoded = json.dumps(body)
    assert "sk-test-secret" not in encoded
    assert body["spend"]["spent_usd"] == 0


def test_rate_limit_is_per_ip(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LIVE_RATE_PER_HOUR", "1")
    monkeypatch.setenv("LIVE_RATE_GLOBAL_PER_HOUR", "10")
    calls, answer = _answer_factory()
    service = build_service(answer, tmp_path / "spend.json", provider="stub")
    assert service.handle({"question": "one"}, "203.0.113.4")[0] == 200
    status, body = service.handle({"question": "two"}, "203.0.113.4")
    assert status == 429
    assert body["error"] == "rate_limited"
    assert service.handle({"question": "three"}, "203.0.113.8")[0] == 200
    assert calls == ["one", "three"]


def test_corrupt_ledger_refuses_to_reset(tmp_path: Path):
    path = tmp_path / "spend.json"
    path.write_text("{")
    with pytest.raises(RuntimeError):
        SpendLedger(path, 500)


def test_http_ask_uses_the_socket_and_hides_the_key(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LIVE_ASK", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret")
    _calls, answer = _answer_factory()
    service = build_service(answer, tmp_path / "spend.json", provider="stub")
    server = make_server(service, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        health = json.loads(urlopen(f"http://127.0.0.1:{port}/health", timeout=5).read())
        assert health["live_ask"] is True
        assert "sk-test-secret" not in json.dumps(health)
        request = Request(
            f"http://127.0.0.1:{port}/ask",
            data=json.dumps({"question": "Which airline is most on time out of DFW?"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        body = json.loads(urlopen(request, timeout=5).read())
        assert body["sql"] == "SELECT 1"
        assert "sk-test-secret" not in json.dumps(body)
    finally:
        server.shutdown()
        server.server_close()


def test_live_module_does_not_read_the_bank():
    import flight_analyst.live as live

    source = Path(live.__file__).read_text()
    assert "BANK_PATH" not in source
    assert "questions.bank" not in source
    assert "load_bank" not in source
