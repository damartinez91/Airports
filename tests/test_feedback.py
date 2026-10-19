"""Answer feedback: storage, the live hook, and the review queue.

No test here calls a model. Sonnet re-answer is a function the test supplies.
"""

import datetime as dt
import json
import subprocess
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from flight_analyst.daily_review import ReviewConfig, promote, run_review
from flight_analyst.feedback import (
    STORED_FIELDS,
    FeedbackLog,
    feedback_hook_html,
    wrap_live_answer,
)
from flight_analyst.live import LiveAsk, RateLimiter, SpendLedger, build_service, make_server
from flight_analyst.routing import AnticipatedBank, BankEntry, HashEmbedder, QuestionLog, RouteConfig, Router
from flight_analyst.__main__ import main

ROOT = Path(__file__).resolve().parents[1]


def _now():
    return dt.datetime(2026, 10, 16, 12, tzinfo=dt.timezone.utc)


def _bank():
    return AnticipatedBank([
        BankEntry(id="q001", question="Which airline is most on time out of DFW?", answer_text="DFW cached answer"),
    ], HashEmbedder())


def _log(tmp_path: Path) -> FeedbackLog:
    return FeedbackLog(tmp_path / "feedback.jsonl", now=_now)


def _record(log: FeedbackLog, **overrides) -> dict:
    payload = {
        "question": "Which airline is most on time out of DFW?",
        "answer_text": "DFW cached answer",
        "route": "cached-sonnet",
        "model": "claude-sonnet-4-5",
        "match_score": 0.91,
        "matched_id": "q001",
        "expected": "The major carriers, not only the winner.",
    }
    payload.update(overrides)
    return log.record(payload)


def test_feedback_stores_the_answer_and_caps_expected_text(tmp_path: Path):
    log = _log(tmp_path)
    long_text = "x" * 1500
    row = _record(log, expected=long_text, email="person@example.com", ip="203.0.113.9")
    assert set(row) == set(STORED_FIELDS)
    assert row["id"] == "fb-2026-10-16-000"
    assert row["ts"].startswith("2026-10-16")
    assert row["question"].startswith("Which airline")
    assert row["answer_text"] == "DFW cached answer"
    assert row["model"] == "claude-sonnet-4-5"
    assert row["route"] == "cached-sonnet"
    assert row["match_score"] == 0.91
    assert row["matched_id"] == "q001"
    assert row["expected"] == "x" * 1000
    assert row["expected_truncated"] is True
    assert row["recheck_cached"] is True
    line = (tmp_path / "feedback.jsonl").read_text()
    assert "person@example.com" not in line
    assert "203.0.113.9" not in line
    assert "ip" not in json.loads(line)
    flagged = json.loads((tmp_path / "cached_recheck.json").read_text())
    assert flagged["q001"]["reason"].startswith("user-reported miss")
    assert flagged["q001"]["report_ids"] == ["fb-2026-10-16-000"]
    again = _record(log, expected="shorter")
    flagged = json.loads((tmp_path / "cached_recheck.json").read_text())
    assert flagged["q001"]["report_ids"] == [row["id"], again["id"]]


def test_haiku_feedback_keeps_no_match_and_does_not_flag_the_bank(tmp_path: Path):
    log = _log(tmp_path)
    row = _record(
        log,
        route="haiku",
        model="claude-haiku-4-5",
        question="How late is JetBlue at BOS?",
        answer_text="About 12 minutes.",
        matched_id="q001",
        match_score=0.4,
        expected="",
    )
    assert row["route"] == "haiku"
    assert row["matched_id"] is None
    assert row["match_score"] is None
    assert row["recheck_cached"] is False
    assert row["expected"] == ""
    assert not (tmp_path / "cached_recheck.json").exists()


def test_feedback_files_are_gitignored():
    for rel in ("data/feedback.jsonl", "data/cached_recheck.json"):
        result = subprocess.run(["git", "check-ignore", "-q", rel], cwd=ROOT, check=False)
        assert result.returncode == 0, rel


def test_hook_has_the_button_and_the_expected_box():
    page = wrap_live_answer(
        "<script>alert(1)</script>",
        "a & b",
        route="cached-sonnet",
        model="claude-sonnet-4-5",
        match_score=0.91,
        matched_id="q001",
    )
    assert "This didn't answer my question" in page
    assert "What I expected" in page
    assert 'maxlength="1000"' in page
    assert 'data-question="&lt;script&gt;alert(1)&lt;/script&gt;"' in page
    assert "a &amp; b" in page
    assert "<script>alert(1)</script>" not in page
    assert 'type="email"' not in page
    hook = feedback_hook_html()
    assert "This didn't answer my question" in hook
    assert "What I expected" in hook


def test_ask_labels_route_and_feedback_does_not_spend(tmp_path: Path):
    ledger = SpendLedger(tmp_path / "spend.json", 100)
    calls = []

    def answer(question):
        calls.append(question)
        return {"answer_text": "haiku says"}

    svc = LiveAsk(
        answer, ledger, RateLimiter(100, 100), "anthropic", 5,
        router=Router(_bank(), RouteConfig()),
        feedback_log=_log(tmp_path),
        live_model="claude-haiku-4-5",
    )
    status, cached = svc.handle({"question": "Which airline is most on time out of DFW?"}, "203.0.113.8")
    assert status == 200
    assert cached["route"] == "cached-sonnet"
    assert cached["model"] == "claude-sonnet-4-5"
    assert cached["matched_id"] == "q001"
    assert cached["provider"] == "cache"
    assert ledger.spent_cents == 0
    status, body = svc.submit_feedback({
        "question": "Which airline is most on time out of DFW?",
        "answer_text": cached["answer_text"],
        "route": cached["route"],
        "model": cached["model"],
        "match_score": cached["match_score"],
        "matched_id": cached["matched_id"],
        "expected": "The majors too.",
        "email": "person@example.com",
    }, "203.0.113.8")
    assert status == 200 and body["recheck_cached"] is True
    assert ledger.spent_cents == 0 and calls == []
    stored = json.loads((tmp_path / "feedback.jsonl").read_text())
    assert stored["answer_text"] == "DFW cached answer"
    assert "person@example.com" not in json.dumps(stored)
    assert "203.0.113.8" not in json.dumps(stored)
    status, live = svc.handle({"question": "How late is JetBlue at BOS?"}, "203.0.113.8")
    assert status == 200
    assert live["route"] == "haiku" and live["model"] == "claude-haiku-4-5"
    assert live["matched_id"] is None and live["match_score"] is None
    status, rejected = svc.submit_feedback({"question": "x", "answer_text": "y", "route": "haiku", "model": "claude-haiku-4-5", "api_key": "sk-test-secret"}, "203.0.113.8")
    assert status == 400
    assert "sk-test-secret" not in json.dumps(rejected)
    assert "sk-test-secret" not in (tmp_path / "feedback.jsonl").read_text()


def test_http_feedback_hook_and_post(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LIVE_ASK", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret")
    service = build_service(lambda question: {"answer_text": "haiku says"}, tmp_path / "spend.json", provider="stub")
    server = make_server(service, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        hook = urlopen(f"http://127.0.0.1:{port}/feedback-hook", timeout=5).read().decode()
        assert "This didn't answer my question" in hook
        assert "What I expected" in hook
        assert 'maxlength="1000"' in hook
        request = Request(
            f"http://127.0.0.1:{port}/feedback",
            data=json.dumps({
                "question": "How late is JetBlue at BOS?",
                "answer_text": "haiku says",
                "route": "haiku",
                "model": "claude-haiku-4-5",
                "expected": "A carrier table.",
                "ip": "203.0.113.4",
            }).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        body = json.loads(urlopen(request, timeout=5).read())
        assert body["ok"] is True and body["route"] == "haiku"
        stored = (tmp_path / "feedback.jsonl").read_text()
        assert "A carrier table." in stored
        assert "203.0.113.4" not in stored
        assert "127.0.0.1" not in stored
        assert "sk-test-secret" not in stored
        secret = Request(
            f"http://127.0.0.1:{port}/feedback",
            data=json.dumps({
                "question": "How late is JetBlue at BOS?",
                "answer_text": "haiku says",
                "route": "haiku",
                "model": "claude-haiku-4-5",
                "api_key": "sk-test-secret",
            }).encode(),
            method="POST",
        )
        with pytest.raises(HTTPError) as exc:
            urlopen(secret, timeout=5)
        assert exc.value.code == 400
        assert "sk-test-secret" not in exc.value.read().decode()
        assert service.ledger.spent_cents == 0
    finally:
        server.shutdown()
        server.server_close()


def test_feedback_cli(tmp_path: Path, capsys):
    path = tmp_path / "feedback.jsonl"
    main([
        "feedback",
        "--question", "Which airline is most on time out of DFW?",
        "--answer", "DFW cached answer",
        "--route", "cached-sonnet",
        "--model", "claude-sonnet-4-5",
        "--match-score", "0.91",
        "--matched-id", "q001",
        "--expected", "The majors too.",
        "--file", str(path),
    ])
    out = capsys.readouterr().out
    assert "route=cached-sonnet" in out and "recheck=q001" in out
    row = json.loads(path.read_text().splitlines()[0])
    assert row["expected"] == "The majors too."
    assert row["match_score"] == 0.91
    with pytest.raises(SystemExit):
        main([
            "feedback",
            "--question", "How late is JetBlue at BOS?",
            "--answer", "12 minutes",
            "--route", "haiku",
            "--model", "not a model",
            "--file", str(path),
        ])


def _queue(tmp_path: Path, calls: list):
    day = _now().date()
    log = QuestionLog(tmp_path / "live", now=_now)
    log.write({"question": "How late is JetBlue at BOS?", "route": "new", "answer_text": "haiku"})
    feedback = _log(tmp_path)
    _record(feedback, expected="The majors too.")
    bank_path = tmp_path / "bank.json"
    _bank().save(bank_path)
    before = bank_path.read_text()

    def answer(question):
        calls.append(question)
        return {"answer_text": "sonnet rewrote this", "sql": "SELECT 1"}

    queue = run_review(
        day, answer, lambda system, user: json.dumps({"correct": True, "agrees_with_reference": True}),
        log=log, bank=AnticipatedBank.load(bank_path), config=ReviewConfig(),
        queue_dir=tmp_path / "q", bank_path=bank_path, feedback=feedback,
    )
    return queue, before, bank_path


def test_feedback_lands_first_in_the_review_queue_and_is_not_sent_to_sonnet(tmp_path: Path):
    calls = []
    queue, before, bank_path = _queue(tmp_path, calls)
    assert calls == ["How late is JetBlue at BOS?"]
    assert queue["user_reported_misses"] == 1
    assert queue["estimated_spend_usd"] == "0.10"
    miss, new = queue["candidates"]
    assert miss["user_reported_miss"] is True
    assert miss["flag"] == "user-reported miss"
    assert miss["id"].startswith("fb-")
    assert miss["question"].startswith("Which airline")
    assert miss["answer_given"] == "DFW cached answer"
    assert miss["what_i_expected"] == "The majors too."
    assert "Question:" in miss["for_reviewer"]
    assert "Answer given:" in miss["for_reviewer"]
    assert "What I expected:" in miss["for_reviewer"]
    assert miss["recheck_cached"] is True and miss["matched_id"] == "q001"
    assert miss["status"] == "CANDIDATE" and miss["category"] is None and miss["approved"] is False
    assert [choice["category"] for choice in miss["category_choices"]] == [
        "send-to-sonnet", "fix-cached-answer", "fix-routing", "out-of-scope", "no-change",
    ]
    assert new["id"].startswith("live-")
    assert not new.get("user_reported_miss")
    assert bank_path.read_text() == before


def test_auto_promote_does_not_approve_a_user_reported_miss(tmp_path: Path):
    day = _now().date()
    log = QuestionLog(tmp_path / "live", now=_now)
    log.write({"question": "How late is JetBlue at BOS?", "route": "new", "answer_text": "haiku"})
    feedback = _log(tmp_path)
    _record(feedback)
    bank_path = tmp_path / "bank.json"
    _bank().save(bank_path)
    queue = run_review(
        day, lambda question: {"answer_text": "sonnet", "sql": "SELECT 1"},
        lambda system, user: json.dumps({"correct": True, "agrees_with_reference": True}),
        log=log, bank=AnticipatedBank.load(bank_path), config=ReviewConfig(auto_promote=True),
        queue_dir=tmp_path / "q", bank_path=bank_path, feedback=feedback,
    )
    miss = queue["candidates"][0]
    assert miss["user_reported_miss"] is True and miss["status"] == "CANDIDATE" and miss["approved"] is False
    entries = AnticipatedBank.load(bank_path, all_statuses=True).entries
    ids = [entry.id for entry in entries]
    assert not any(item.startswith("fb-") for item in ids)
    auto = [entry for entry in entries if entry.id.startswith("live-")]
    assert len(auto) == 1 and auto[0].status == "AUTO" and auto[0].category == "auto-unreviewed"


def test_promote_requires_a_category_and_only_send_to_sonnet_approves(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("flight_analyst.daily_review.sonnet_functions", lambda: (_ for _ in ()).throw(AssertionError("paid")))
    calls = []
    queue, before, bank_path = _queue(tmp_path, calls)
    paid_before = list(calls)
    day = _now().date()
    miss_id = queue["candidates"][0]["id"]
    queue_path = tmp_path / "q" / f"{day.isoformat()}.json"
    snapshot = queue_path.read_text()

    with pytest.raises(ValueError, match="category is required"):
        promote(day, [miss_id], "", queue_dir=tmp_path / "q", bank_path=bank_path, answer_fn=lambda question: {"answer_text": "x"})
    with pytest.raises(ValueError, match="feedback miss needs a category"):
        promote(day, [miss_id], "head-to-head", queue_dir=tmp_path / "q", bank_path=bank_path)
    assert queue_path.read_text() == snapshot
    assert bank_path.read_text() == before

    def reanswer(question):
        calls.append(("reanswer", question))
        return {"answer_text": "sonnet rewrote this", "sql": "SELECT 1"}

    assert promote(day, [miss_id], "send to Sonnet", queue_dir=tmp_path / "q", bank_path=bank_path, answer_fn=reanswer) == 1
    assert calls == paid_before + [("reanswer", queue["candidates"][0]["question"])]
    entry = [item for item in AnticipatedBank.load(bank_path).entries if item.id == miss_id][0]
    assert entry.status == "APPROVED"
    assert entry.category == "send-to-sonnet"
    assert entry.model == "claude-sonnet-4-5"
    assert entry.answer_text == "sonnet rewrote this"
    assert entry.answer_text != "DFW cached answer"
    saved = json.loads(queue_path.read_text())["candidates"][0]
    assert saved["status"] == "APPROVED" and saved["approved"] is True
    assert saved["answer_given"] == "DFW cached answer"
    assert saved["what_i_expected"] == "The majors too."
    # A second approval must not call Sonnet again.
    assert promote(day, [miss_id], "send-to-sonnet", queue_dir=tmp_path / "q", bank_path=bank_path, answer_fn=reanswer) == 1
    assert calls == paid_before + [("reanswer", queue["candidates"][0]["question"])]


@pytest.mark.parametrize("category", ["fix-cached-answer", "fix the routing or match threshold", "out of scope", "no change"])
def test_other_feedback_categories_are_recorded_and_not_approved(tmp_path: Path, category: str):
    calls = []
    queue, _before, bank_path = _queue(tmp_path, calls)
    day = _now().date()
    miss_id = queue["candidates"][0]["id"]
    assert promote(day, [miss_id], category, queue_dir=tmp_path / "q", bank_path=bank_path,
                   answer_fn=lambda question: (_ for _ in ()).throw(AssertionError("no reanswer"))) == 1
    saved = json.loads((tmp_path / "q" / f"{day.isoformat()}.json").read_text())["candidates"][0]
    assert saved["status"] == "REVIEWED"
    assert saved["approved"] is False
    assert saved["category"] in {"fix-cached-answer", "fix-routing", "out-of-scope", "no-change"}
    assert [entry.id for entry in AnticipatedBank.load(bank_path, all_statuses=True).entries] == ["q001"]


def test_refused_sonnet_reanswer_does_not_approve(tmp_path: Path):
    calls = []
    queue, before, bank_path = _queue(tmp_path, calls)
    day = _now().date()
    miss_id = queue["candidates"][0]["id"]
    queue_path = tmp_path / "q" / f"{day.isoformat()}.json"
    snapshot = queue_path.read_text()
    with pytest.raises(RuntimeError, match="Nothing was marked approved"):
        promote(day, [miss_id], "send-to-sonnet", queue_dir=tmp_path / "q", bank_path=bank_path,
                answer_fn=lambda question: {"answer_text": "no", "refused": True})
    assert queue_path.read_text() == snapshot
    assert bank_path.read_text() == before


def test_feedback_is_promoted_separately_from_new_questions(tmp_path: Path):
    calls = []
    queue, before, bank_path = _queue(tmp_path, calls)
    day = _now().date()
    ids = [item["id"] for item in queue["candidates"]]
    with pytest.raises(ValueError, match="separately"):
        promote(day, ids, "no-change", queue_dir=tmp_path / "q", bank_path=bank_path)
    assert bank_path.read_text() == before
    saved = json.loads((tmp_path / "q" / f"{day.isoformat()}.json").read_text())
    assert [item["status"] for item in saved["candidates"]] == ["CANDIDATE", "CANDIDATE"]


def test_routing_doc_describes_feedback_review():
    text = (ROOT / "docs" / "ROUTING.md").read_text()
    for phrase in (
        "data/feedback.jsonl",
        "This didn't answer my question",
        "What I expected",
        "send-to-sonnet",
        "fix-cached-answer",
        "fix-routing",
        "out-of-scope",
        "no-change",
        "user-reported miss",
        "cached_recheck.json",
        "explicit categorization",
    ):
        assert phrase in text
