"""Route live questions: cached Sonnet answer, or Haiku plus a log entry.

Matching never calls a model. A visitor question is embedded with a cheap local
embedder and compared (cosine) against the precomputed anticipated bank.

    score >= ROUTE_HIGH              -> serve the cached Sonnet answer ($0)
    ROUTE_LOW <= score < ROUTE_HIGH  -> borderline: optional cheap Haiku
                                        "same question?" check (ROUTE_BORDERLINE_CHECK=1),
                                        otherwise treated as new
    score < ROUTE_LOW                -> new question: Haiku answers, logged for review

All thresholds are env-configurable. Every live question is logged with its route.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from flight_analyst.paths import DATA_DIR, QUESTIONS_DIR

DEFAULT_HIGH = 0.86
DEFAULT_LOW = 0.62
BANK_PATH = QUESTIONS_DIR / "anticipated_bank.json"  # committed; holds no flight rows
LOG_DIR = DATA_DIR / "live_questions"
_DIM = 2048


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    value = float(raw)
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return value


@dataclass(frozen=True)
class RouteConfig:
    high: float = DEFAULT_HIGH
    low: float = DEFAULT_LOW
    borderline_check: bool = False

    @classmethod
    def from_env(cls) -> "RouteConfig":
        cfg = cls(
            high=_env_float("ROUTE_HIGH", DEFAULT_HIGH),
            low=_env_float("ROUTE_LOW", DEFAULT_LOW),
            borderline_check=os.environ.get("ROUTE_BORDERLINE_CHECK", "").strip() == "1",
        )
        if cfg.low > cfg.high:
            raise ValueError("ROUTE_LOW must not exceed ROUTE_HIGH")
        return cfg


class Embedder(Protocol):
    name: str

    def embed(self, text: str) -> list[float]: ...


_STOP = {"the", "a", "an", "of", "to", "in", "is", "at", "for", "on", "and", "what", "which", "how", "out", "from"}


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9%$ ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class HashEmbedder:
    """$0, dependency-free embedding: hashed word + character-trigram features, L2-normalized.

    Good at near-paraphrases ("most on time out of DFW" vs "most punctual airline at DFW?"
    scores lower than a learned model would). Swap in a learned embedder (e.g. a small
    local sentence model, or a paid embeddings API) via the Embedder protocol; the
    thresholds will need re-tuning per embedder, which is why they are env-configurable.
    """

    name = "hash-v1"

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * _DIM
        words = [w for w in normalize(text).split() if w not in _STOP]
        feats = [f"w:{w}" for w in words]
        feats += [f"b:{a}_{b}" for a, b in zip(words, words[1:])]
        for w in words:
            padded = f" {w} "
            feats += [f"c:{padded[i:i + 3]}" for i in range(len(padded) - 2)]
        for feat in feats:
            digest = hashlib.blake2b(feat.encode(), digest_size=8).digest()
            idx = int.from_bytes(digest[:4], "little") % _DIM
            sign = 1.0 if digest[4] & 1 else -1.0
            weight = 2.0 if feat.startswith(("w:", "b:")) else 0.5
            vec[idx] += sign * weight
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


_CARRIERS = {"american", "delta", "united", "southwest", "alaska", "jetblue", "spirit", "frontier", "hawaiian",
             "allegiant", "skywest", "envoy", "republic", "endeavor", "psa", "sun", "breeze", "avelo"}
_TIME_WORDS = {"yesterday", "today", "week", "month", "year", "winter", "summer", "spring", "fall", "january",
               "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
               "december", "morning", "evening", "night", "weekend", "holiday", "thanksgiving", "christmas"}


def entities(text: str, codes: set[str]) -> frozenset[str]:
    """Airports, airlines, years and time words. A cached hit needs the same set, whatever the score,
    so "most on time out of PHX" can never be served the DFW answer."""
    words = normalize(text).split()
    found = {w for w in words if w in codes or w in _CARRIERS or w in _TIME_WORDS or re.fullmatch(r"(19|20)\d\d", w)}
    return frozenset(found)


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


@dataclass
class BankEntry:
    id: str
    question: str
    answer_text: str
    sql: str | None = None
    model: str = "claude-sonnet-4-5"
    source: str = ""
    status: str = "DRAFT"
    category: str | None = None
    vector: list[float] = field(default_factory=list, repr=False)


class AnticipatedBank:
    def __init__(self, entries: list[BankEntry], embedder: Embedder):
        self.embedder = embedder
        self.entries = entries
        for entry in self.entries:
            if not entry.vector:
                entry.vector = embedder.embed(entry.question)
        self.codes = {t.lower() for e in self.entries for t in re.findall(r"\b[A-Z]{3}\b", e.question)}

    @classmethod
    def load(cls, path: Path | None = None, embedder: Embedder | None = None, all_statuses: bool = False) -> "AnticipatedBank":
        embedder = embedder or HashEmbedder()
        path = path or BANK_PATH
        payload = json.loads(path.read_text()) if path.exists() else {"entries": []}
        if payload.get("embedder") not in (None, embedder.name):
            # Vectors from another embedder are not comparable; recompute.
            for row in payload.get("entries", []):
                row.pop("vector", None)
        # ROUTE_SERVE_STATUSES: which review statuses may be served from cache. Default serves Sonnet answers
        # that passed grading on APPROVED and DRAFT bank questions (the public site already shows DRAFT answers,
        # labeled as such). Set to "APPROVED" to serve only Daniel-approved ones.
        allowed = {x.strip().upper() for x in os.environ.get("ROUTE_SERVE_STATUSES", "APPROVED,DRAFT").split(",") if x.strip()}
        entries = [BankEntry(**row) for row in payload.get("entries", []) if all_statuses or str(row.get("status", "DRAFT")).upper() in allowed]
        return cls(entries, embedder)

    def save(self, path: Path | None = None) -> None:
        path = path or BANK_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        # The hash embedder is deterministic and fast, so its vectors are recomputed on load, not stored.
        keep = self.embedder.name != HashEmbedder.name
        rows = [{**e.__dict__, "vector": [round(v, 5) for v in e.vector] if keep else []} for e in self.entries]
        path.write_text(json.dumps({"embedder": self.embedder.name, "entries": rows}, indent=1))

    def best(self, question: str) -> tuple[BankEntry | None, float]:
        if not self.entries:
            return None, 0.0
        vec = self.embedder.embed(question)
        scored = max(((cosine(vec, e.vector), e) for e in self.entries), key=lambda pair: pair[0])
        return scored[1], scored[0]

    def same_entities(self, a: str, b: str) -> bool:
        return entities(a, self.codes) == entities(b, self.codes)

    def add(self, entry: BankEntry) -> None:
        entry.vector = entry.vector or self.embedder.embed(entry.question)
        self.codes |= {t.lower() for t in re.findall(r"\b[A-Z]{3}\b", entry.question)}
        self.entries = [e for e in self.entries if e.id != entry.id] + [entry]


def bank_from_run(run_path: Path, embedder: Embedder | None = None, passed_only: bool = True) -> AnticipatedBank:
    """Precompute the bank from a saved Sonnet run (results/claude-sonnet-4-5-r1.json).

    Only graded passes are cached by default, so a known-wrong Sonnet answer is never served.
    Entries keep the bank's review status; nothing here marks anything approved.
    """
    run = json.loads(run_path.read_text())
    entries = []
    for row in run.get("results", []):
        if passed_only and not (row.get("grade") or {}).get("passed"):
            continue
        entries.append(BankEntry(
            id=row["id"], question=row.get("question") or "", answer_text=row.get("answer") or "",
            sql=row.get("sql"), model=str(run.get("model")), source=str(run.get("run_label") or run_path.stem),
            status=str(row.get("status") or "DRAFT"),
        ))
    return AnticipatedBank(entries, embedder or HashEmbedder())


class QuestionLog:
    """Append-only JSONL, one file per UTC day. Every live question lands here."""

    def __init__(self, folder: Path | None = None, now: Callable[[], dt.datetime] | None = None):
        self.folder = folder or LOG_DIR
        self.now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self._lock = threading.Lock()

    def path_for(self, day: dt.date) -> Path:
        return self.folder / f"{day.isoformat()}.jsonl"

    def write(self, record: dict) -> None:
        stamp = self.now()
        record = {"ts": stamp.isoformat(), **record}
        with self._lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            with self.path_for(stamp.date()).open("a") as fh:
                fh.write(json.dumps(record) + "\n")

    def read(self, day: dt.date) -> list[dict]:
        path = self.path_for(day)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@dataclass
class Decision:
    route: str  # "cached" | "new" ; borderline resolves to one of them
    score: float
    match: BankEntry | None
    borderline: bool = False
    checked: bool = False


class Router:
    def __init__(self, bank: AnticipatedBank, config: RouteConfig | None = None,
                 same_question_fn: Callable[[str, str], bool] | None = None):
        self.bank = bank
        self.config = config or RouteConfig.from_env()
        # same_question_fn(visitor_q, bank_q) -> bool. A cheap Haiku yes/no call; charged by the caller.
        self.same_question_fn = same_question_fn

    def decide(self, question: str, allow_check: bool = True) -> Decision:
        match, score = self.bank.best(question)
        if match is not None and not self.bank.same_entities(question, match.question):
            # Different airport/airline/period: never serve the cached answer, however similar the wording.
            return Decision("new", score, match)
        if match is not None and score >= self.config.high:
            return Decision("cached", score, match)
        if match is not None and score >= self.config.low:
            if allow_check and self.config.borderline_check and self.same_question_fn is not None:
                same = bool(self.same_question_fn(question, match.question))
                return Decision("cached" if same else "new", score, match, borderline=True, checked=True)
            return Decision("new", score, match, borderline=True)
        return Decision("new", score, match)


SAME_QUESTION_PROMPT = (
    "Answer only YES or NO. Do these two questions about US flight on-time data ask for exactly "
    "the same figures (same airports, airlines, routes, metric and time window)?"
)


def haiku_same_question(client) -> Callable[[str, str], bool]:
    """Build the optional borderline check from an llm client pinned to claude-haiku-4-5."""

    def check(a: str, b: str) -> bool:
        out = client.complete(SAME_QUESTION_PROMPT, f"Q1: {a}\nQ2: {b}")
        return str(getattr(out, "text", out)).strip().upper().startswith("YES")

    return check
