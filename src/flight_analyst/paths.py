"""Repository paths. Data files stay under data/ and are gitignored."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("FLIGHT_DATA_DIR", ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
DB_PATH = Path(os.environ.get("FLIGHT_DB_PATH", DATA_DIR / "flights.duckdb"))
QUESTIONS_DIR = ROOT / "questions"
BANK_PATH = QUESTIONS_DIR / "bank.json"
REPORTS_DIR = ROOT / "reports"
SCOREBOARD_PATH = ROOT / "scoreboard" / "index.html"
DOCS_DIR = ROOT / "docs"
DATA_WINDOW_PATH = DOCS_DIR / "data_window.json"


def ensure_dirs() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    QUESTIONS_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    SCOREBOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
