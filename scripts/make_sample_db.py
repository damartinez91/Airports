#!/usr/bin/env python3
"""Build the small committed CI fixture from the full local DB.

The sample keeps every lookup table, plus one month (default 2026-07) of flights
that touch DFW (origin or dest). It's for smoke and CI tests only, not real analysis.
Usage: python scripts/make_sample_db.py --src data/flights.duckdb [--out tests/fixtures/sample_flights.duckdb] [--month 2026-07]
"""
from __future__ import annotations

import argparse, json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
LOOKUPS = ["airports", "carriers", "carrier_families", "carrier_name_map"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(ROOT / "data" / "flights.duckdb"))
    ap.add_argument("--out", default=str(ROOT / "tests" / "fixtures" / "sample_flights.duckdb"))
    ap.add_argument("--month", default="2026-07")
    ap.add_argument("--airport", default="DFW")
    a = ap.parse_args()
    y, m = (int(x) for x in a.month.split("-"))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    con = duckdb.connect(str(out))
    con.execute(f"ATTACH '{a.src}' AS src (READ_ONLY)")
    for t in LOOKUPS:
        con.execute(f"CREATE TABLE {t} AS SELECT * FROM src.{t}")
    con.execute(
        "CREATE TABLE flights AS SELECT * FROM src.flights WHERE year = ? AND month = ? AND (origin = ? OR dest = ?)",
        [y, m, a.airport, a.airport],
    )
    view_sql = con.execute("SELECT sql FROM duckdb_views() WHERE view_name = 'flights_v' AND database_name = 'src'").fetchone()[0]
    con.execute(view_sql)
    n = con.execute("SELECT COUNT(*) FROM flights").fetchone()[0]
    meta = {"sample": True, "note": f"CI fixture: {a.airport} flights in {a.month} only; not for real analysis",
            "row_count": n, "months": [{"year": y, "month": m}]}
    con.execute("CREATE TABLE metadata (key VARCHAR PRIMARY KEY, value VARCHAR)")
    con.execute("INSERT INTO metadata VALUES ('data_window', ?)", [json.dumps(meta)])
    con.execute("DETACH src")
    con.execute("CHECKPOINT")
    con.close()
    print(f"wrote {out} rows={n} size={out.stat().st_size/1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
