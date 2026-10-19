import datetime as dt
import json

from flight_analyst.daily_review import ReviewConfig, promote, run_review
from flight_analyst.live import SpendLedger, RateLimiter, LiveAsk
from flight_analyst.routing import AnticipatedBank, BankEntry, HashEmbedder, QuestionLog, RouteConfig, Router


def _bank():
    return AnticipatedBank([
        BankEntry(id="q001", question="Which airline is most on time out of DFW?", answer_text="DFW answer"),
        BankEntry(id="q010", question="What is the cancellation rate at ORD?", answer_text="ORD answer"),
    ], HashEmbedder())


def _now():
    return dt.datetime(2026, 10, 16, 12, tzinfo=dt.timezone.utc)


def test_exact_and_paraphrase_hit_cache():
    router = Router(_bank(), RouteConfig())
    assert router.decide("what airline is most on-time out of dfw").route == "cached"


def test_different_airport_never_served_cached_answer():
    router = Router(_bank(), RouteConfig(high=0.1, low=0.05))
    assert router.decide("Which airline is most on time out of PHX?").route == "new"


def test_low_score_goes_new():
    assert Router(_bank(), RouteConfig()).decide("Is Delta better than United at Newark in winter?").route == "new"


def test_borderline_check_is_optional_and_configurable():
    calls = []
    def same(a, b):
        calls.append((a, b)); return True
    q = "Which airline has the best on time record out of DFW?"
    off = Router(_bank(), RouteConfig(high=0.99, low=0.1), same_question_fn=same).decide(q)
    assert off.route == "new" and off.borderline and not calls
    on = Router(_bank(), RouteConfig(high=0.99, low=0.1, borderline_check=True), same_question_fn=same).decide(q)
    assert on.route == "cached" and on.checked and len(calls) == 1


def _service(tmp_path, answers):
    ledger = SpendLedger(tmp_path / "spend.json", 10)  # 10 cents
    log = QuestionLog(tmp_path / "log", now=_now)
    def answer(q):
        answers.append(q); return {"answer_text": "haiku says"}
    svc = LiveAsk(answer, ledger, RateLimiter(100, 100), "anthropic", 5,
                  router=Router(_bank(), RouteConfig()), log=log)
    return svc, ledger, log


def test_cached_hits_are_free_and_new_questions_are_logged_and_capped(tmp_path):
    answers = []
    svc, ledger, log = _service(tmp_path, answers)
    status, body = svc.handle({"question": "Which airline is most on time out of DFW?"}, "1.1.1.1")
    assert status == 200 and body["provider"] == "cache" and ledger.spent_cents == 0 and not answers
    for _ in range(2):
        assert svc.handle({"question": "Is Delta better than United at EWR?"}, "1.1.1.1")[0] == 200
    assert svc.handle({"question": "Is Delta better than United at EWR?"}, "1.1.1.1")[0] == 403
    routes = [r["route"] for r in log.read(_now().date())]
    assert routes == ["cached", "new", "new"]


def test_daily_review_queues_by_default_and_promote_needs_category(tmp_path):
    log = QuestionLog(tmp_path / "log", now=_now)
    for q in ["Is Delta better than United at EWR?", "is delta better than united at ewr", "How late is JetBlue at BOS?"]:
        log.write({"question": q, "route": "new", "answer_text": "haiku"})
    bank_path = tmp_path / "bank.json"
    _bank().save(bank_path)
    grade = lambda s, u: json.dumps({"correct": True, "agrees_with_reference": True})
    queue = run_review(_now().date(), lambda q: {"answer_text": "sonnet"}, grade, log=log,
                       bank=AnticipatedBank.load(bank_path), config=ReviewConfig(), queue_dir=tmp_path / "q",
                       bank_path=bank_path)
    assert queue["mode"] == "queue-for-approval"
    assert [c["status"] for c in queue["candidates"]] == ["CANDIDATE", "CANDIDATE"]  # duplicate dropped
    assert len(AnticipatedBank.load(bank_path).entries) == 2  # nothing promoted without Daniel
    try:
        promote(_now().date(), [queue["candidates"][0]["id"]], "", queue_dir=tmp_path / "q", bank_path=bank_path)
        raise AssertionError("empty category must be refused")
    except ValueError:
        pass
    assert promote(_now().date(), [queue["candidates"][0]["id"]], "head-to-head", queue_dir=tmp_path / "q",
                   bank_path=bank_path) == 1
    entry = [e for e in AnticipatedBank.load(bank_path).entries if e.id.startswith("live-")][0]
    assert entry.status == "APPROVED" and entry.category == "head-to-head"


def test_daily_review_respects_spend_cap(tmp_path):
    log = QuestionLog(tmp_path / "log", now=_now)
    for code in ["BOS", "SEA", "DEN", "MSP"]:
        log.write({"question": f"How late is JetBlue at {code} on Mondays?", "route": "new"})
    queue = run_review(_now().date(), lambda q: {"answer_text": "x"}, lambda s, u: "{}", log=log, bank=_bank(),
                       config=ReviewConfig(spend_cap_usd=__import__("decimal").Decimal("0.20")),
                       queue_dir=tmp_path / "q")
    assert len(queue["candidates"]) == 2 and queue["skipped_for_cap"] == 2
