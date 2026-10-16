"""Optional live ask endpoint.

The process listens only when LIVE_ASK=1. The static site does not call it.
The model key is read from the server environment. Callers cannot send a key,
a provider, or a model. A per-IP rate limit and a persisted spend cap run
before any model call. The stub provider is charged 0.
"""

from __future__ import annotations

import json
import os
import threading
import time
from decimal import Decimal, ROUND_HALF_UP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from flight_analyst.paths import DATA_DIR

DEFAULT_SPEND_CAP_USD = "5"
DEFAULT_USD_PER_CALL = "0.05"
DEFAULT_RATE_PER_HOUR = 10
DEFAULT_GLOBAL_PER_HOUR = 30

_FORBIDDEN_FIELDS = {
    "api_key",
    "apikey",
    "authorization",
    "openai_api_key",
    "anthropic_api_key",
    "token",
    "secret",
    "password",
    "provider",
    "model",
}


class LiveDisabled(RuntimeError):
    pass


def live_enabled() -> bool:
    return os.environ.get("LIVE_ASK", "").strip() == "1"


def _cents(raw: str) -> int:
    return int((Decimal(raw) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _env_cents(name: str, default: str) -> int:
    raw = os.environ.get(name, default).strip()
    try:
        value = _cents(raw)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"{name} must be a dollar amount, got {raw!r}.") from exc
    if value < 0:
        raise RuntimeError(f"{name} must be zero or positive.")
    return value


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}.") from exc
    if value < 1:
        raise RuntimeError(f"{name} must be at least 1.")
    return value


class SpendLedger:
    """Persisted cap. A corrupt file refuses to run rather than resetting to zero."""

    def __init__(self, path: Path, cap_cents: int):
        self.path = path
        self.cap_cents = cap_cents
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                payload = json.loads(self.path.read_text())
                self.spent_cents = int(payload["spent_cents"])
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise RuntimeError(f"Spend ledger at {self.path} is unreadable. Refusing to run.") from exc
        else:
            self.spent_cents = 0
            self._save()

    def _save(self) -> None:
        self.path.write_text(json.dumps({"spent_cents": self.spent_cents}) + "\n")

    def charge(self, cents: int) -> bool:
        if self.spent_cents + cents > self.cap_cents:
            return False
        self.spent_cents += cents
        self._save()
        return True

    def view(self) -> dict:
        return {
            "spent_usd": round(self.spent_cents / 100, 2),
            "cap_usd": round(self.cap_cents / 100, 2),
            "remaining_usd": round((self.cap_cents - self.spent_cents) / 100, 2),
        }


class RateLimiter:
    def __init__(self, per_ip: int, global_per_hour: int, now=None):
        self.per_ip = per_ip
        self.global_per_hour = global_per_hour
        self.now = now or time.time
        self._events: list[float] = []
        self._by_ip: dict[str, list[float]] = {}

    def allow(self, ip: str) -> bool:
        current = self.now()
        self._events = [stamp for stamp in self._events if current - stamp < 3600]
        bucket = [stamp for stamp in self._by_ip.get(ip, []) if current - stamp < 3600]
        if len(self._events) >= self.global_per_hour or len(bucket) >= self.per_ip:
            self._by_ip[ip] = bucket
            return False
        self._events.append(current)
        bucket.append(current)
        self._by_ip[ip] = bucket
        return True


LIVE_MODEL_DEFAULT = "claude-haiku-4-5"  # live/new questions always go to the cheap tier


class LiveAsk:
    def __init__(self, answer_fn, ledger: SpendLedger, limiter: RateLimiter, provider: str, usd_per_call_cents: int,
                 router=None, log=None, check_cents: int = 0):
        self.router = router  # flight_analyst.routing.Router or None (old behaviour: every call hits the model)
        self.log = log  # flight_analyst.routing.QuestionLog or None
        self.check_cents = check_cents
        self.answer_fn = answer_fn
        self.ledger = ledger
        self.limiter = limiter
        self.provider = provider
        self.usd_per_call_cents = 0 if provider == "stub" else usd_per_call_cents
        self._lock = threading.Lock()

    def handle(self, payload: object, client_ip: str) -> tuple[int, dict]:
        if not isinstance(payload, dict):
            return 400, {"error": "body must be a JSON object"}
        extra = _FORBIDDEN_FIELDS.intersection(payload)
        if extra:
            return 400, {"error": "credentials and provider overrides are not accepted"}
        question = payload.get("question")
        if not isinstance(question, str) or not question.strip() or len(question) > 500 or "\x00" in question:
            return 400, {"error": "question must be 1 to 500 characters"}
        question = question.strip()
        with self._lock:
            if not self.limiter.allow(client_ip or "unknown"):
                return 429, {"error": "rate_limited", "spend": self.ledger.view()}
        if self.router is not None:
            cached = self._try_cached(question)
            if cached is not None:
                return 200, cached
        with self._lock:
            if not self.ledger.charge(self.usd_per_call_cents):
                return 403, {"error": "spend_cap", "spend": self.ledger.view()}
        try:
            result = self.answer_fn(question)
        except Exception:  # noqa: BLE001
            return 500, {"error": "analyst_failed", "spend": self.ledger.view()}
        body = {
            "answer_text": result.get("answer_text") or "",
            "sql": result.get("sql"),
            "chart": result.get("chart"),
            "refused": bool(result.get("refused")),
            "refusal_reason": result.get("refusal_reason"),
            "sql_error": result.get("sql_error"),
            "error": "database is unavailable" if result.get("infrastructure_error") else None,
            "provider": self.provider,
            "spend": self.ledger.view(),
            "source": "live",
        }
        if self.log is not None:
            self.log.write({
                "question": question, "route": "new", "score": getattr(self, "_last_score", None),
                "nearest_id": getattr(self, "_last_match", None), "model": self.provider,
                "answer_text": body["answer_text"], "sql": body["sql"], "refused": body["refused"],
                "sql_error": body["sql_error"],
            })
        return 200, body

    def _try_cached(self, question: str) -> dict | None:
        router = self.router
        decision = router.decide(question, allow_check=False)
        if decision.borderline and router.config.borderline_check and router.same_question_fn is not None:
            # The borderline check is a paid Haiku yes/no call: charge it before it runs; skip if capped.
            with self._lock:
                can_check = self.ledger.charge(self.check_cents)
            if can_check:
                decision = router.decide(question, allow_check=True)
        self._last_score = round(decision.score, 4)
        self._last_match = decision.match.id if decision.match else None
        if decision.route != "cached" or decision.match is None:
            return None
        if self.log is not None:
            self.log.write({"question": question, "route": "cached", "score": self._last_score,
                            "nearest_id": decision.match.id, "borderline_checked": decision.checked})
        return {
            "answer_text": decision.match.answer_text,
            "sql": decision.match.sql,
            "chart": None,
            "refused": False,
            "refusal_reason": None,
            "sql_error": None,
            "error": None,
            "provider": "cache",
            "source": f"saved answer from {decision.match.model} ({decision.match.source}, {decision.match.status})",
            "matched_question": decision.match.question,
            "match_score": self._last_score,
            "spend": self.ledger.view(),
        }


def analyst_answer(question: str) -> dict:
    from flight_analyst.analyst import Analyst
    from flight_analyst.llm import get_client
    from flight_analyst.paths import DB_PATH

    os.environ.setdefault("ANTHROPIC_MODEL", os.environ.get("LIVE_MODEL", LIVE_MODEL_DEFAULT))
    result = Analyst(DB_PATH, client=get_client()).answer(question)
    return {
        "answer_text": result.answer_text,
        "sql": result.sql,
        "chart": result.chart,
        "refused": result.refused,
        "refusal_reason": result.refusal_reason,
        "sql_error": result.sql_error,
        "infrastructure_error": result.infrastructure_error,
    }


def build_service(answer_fn=None, ledger_path: Path | None = None, provider: str | None = None,
                  use_router: bool | None = None) -> LiveAsk:
    """use_router=None: on for the real analyst (LIVE_ROUTER=0 turns it off), off when a test injects answer_fn."""
    if provider is None:
        from flight_analyst.llm import get_client

        provider = get_client().name
    ledger = SpendLedger(
        ledger_path or (DATA_DIR / "live_spend.json"),
        _env_cents("LIVE_SPEND_CAP_USD", DEFAULT_SPEND_CAP_USD),
    )
    limiter = RateLimiter(
        _env_int("LIVE_RATE_PER_HOUR", DEFAULT_RATE_PER_HOUR),
        _env_int("LIVE_RATE_GLOBAL_PER_HOUR", DEFAULT_GLOBAL_PER_HOUR),
    )
    router = log = None
    if use_router is None:
        use_router = answer_fn is None and os.environ.get("LIVE_ROUTER", "1").strip() == "1"
    if use_router:
        from flight_analyst.routing import AnticipatedBank, QuestionLog, Router

        check_fn = None
        if os.environ.get("ROUTE_BORDERLINE_CHECK", "").strip() == "1" and os.environ.get("ANTHROPIC_API_KEY"):
            from flight_analyst.llm import AnthropicClient
            from flight_analyst.routing import haiku_same_question

            check_fn = haiku_same_question(AnthropicClient(os.environ["ANTHROPIC_API_KEY"], LIVE_MODEL_DEFAULT))
        router = Router(AnticipatedBank.load(), same_question_fn=check_fn)
        log = QuestionLog(ledger_path.parent / "live_questions" if ledger_path else None)
    return LiveAsk(
        answer_fn or analyst_answer,
        ledger,
        limiter,
        provider,
        _env_cents("LIVE_USD_PER_CALL", DEFAULT_USD_PER_CALL),
        router=router,
        log=log,
        check_cents=0 if provider == "stub" else _env_cents("ROUTE_CHECK_USD_PER_CALL", "0.01"),
    )


def make_server(service: LiveAsk, host: str, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path != "/health":
                self._send(404, {"error": "not_found"})
                return
            self._send(200, {"live_ask": True, "provider": service.provider, "spend": service.ledger.view()})

        def do_POST(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path != "/ask":
                self._send(404, {"error": "not_found"})
                return
            length = int(self.headers.get("Content-Length") or "0")
            if length > 8000:
                self._send(413, {"error": "body too large"})
                return
            try:
                payload = json.loads(self.rfile.read(length).decode() or "{}")
            except (UnicodeError, json.JSONDecodeError):
                self._send(400, {"error": "body must be JSON"})
                return
            status, body = service.handle(payload, self.client_address[0])
            self._send(status, body)

        def _send(self, status: int, body: dict) -> None:
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, fmt: str, *args) -> None:
            # Path and status only. The body is never written here.
            print("%s %s" % (self.address_string(), fmt % args))

    return ThreadingHTTPServer((host, port), Handler)


def serve(host: str = "127.0.0.1", port: int = 8080) -> None:
    if not live_enabled():
        print("LIVE_ASK is not 1. Refusing to listen. The static site does not call this endpoint.")
        raise SystemExit(2)
    service = build_service()
    server = make_server(service, host, port)
    print(f"live ask on http://{host}:{port} provider={service.provider} cap_usd={service.ledger.view()['cap_usd']}")
    server.serve_forever()
