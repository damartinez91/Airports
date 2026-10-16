import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "analysis" / "dice_or_airline"
SOURCE = "BTS On-Time Performance, Aug 2025 to Jul 2026"


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def test_charts_use_the_linkedin_sizes_and_the_source_line_is_in_the_note():
    expected = {
        "01_who_arrives_on_time.png": (1200, 1200),
        "02_busier_airports.png": (1200, 627),
        "03_airline_still_shows.png": (1200, 1200),
        "04_whose_minutes.png": (1200, 627),
    }
    for name, size in expected.items():
        assert _png_size(OUT / "charts" / name) == size
    note = (OUT / "findings.md").read_text()
    assert SOURCE in note
    assert "under 15 minutes" in note
    assert "SkyWest" in note
    assert "not a cause" in note
    assert "marketing-airline" in note or "marketing carrier" in note.lower()
    lowered = note.lower()
    assert "gol" + "den" not in lowered
    assert "ground " + "truth" not in lowered


def test_saved_national_rate_matches_the_database():
    db = ROOT / "data" / "flights.duckdb"
    if not db.exists():
        return
    import duckdb

    payload = json.loads((OUT / "results.json").read_text())
    rate, n = duckdb.connect(str(db), read_only=True).execute(
        """
        SELECT AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END), COUNT(*)
        FROM flights_v
        WHERE operated
        """
    ).fetchone()
    assert abs(rate - payload["national_on_time_rate"]) < 1e-9
    assert n == payload["national_operated"]
    brands = {row["brand"]: row for row in payload["brands"]}
    assert brands["American"]["operated"] == 962421 + 309815 + 231130


def test_brand_effect_gap_is_the_shift_between_brands():
    import numpy as np
    import pytest

    pytest.importorskip("sklearn")
    from analysis.dice_or_airline.run import centered_brand_effects

    rng = np.random.default_rng(0)
    n = 5000
    origin = rng.integers(0, 8, n)
    brand = rng.integers(0, 2, n)
    y = 0.2 * brand + (origin - origin.mean()) * 0.01
    effects = centered_brand_effects(y, brand, [origin])
    assert abs((effects[1] - effects[0]) - 0.2) < 0.02
