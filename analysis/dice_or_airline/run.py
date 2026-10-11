"""Is it the dice or the airline?

Reproducible cuts on the local DuckDB extract (Aug 2025 through Jul 2026).
Writes analysis/dice_or_airline/results.json and the PNG charts next to it.

The reporting-carrier file has no marketing-airline column. Single-partner
regionals roll into a brand. SkyWest and Republic fly for more than one brand,
so they stay their own rows and are not assigned.

Run from the repo root:

    pip install matplotlib numpy scipy scikit-learn
    PYTHONPATH=src python3 analysis/dice_or_airline/run.py
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from sklearn.linear_model import LogisticRegression

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "flights.duckdb"
OUT_DIR = Path(__file__).resolve().parent
CHART_DIR = OUT_DIR / "charts"
SOURCE = "BTS On-Time Performance, Aug 2025 to Jul 2026"

# Minimum operated flights on each side of a matched comparison.
MIN_SIDE = 100

BRAND_SQL = """
CASE airline_code
    WHEN 'AA' THEN 'American'
    WHEN 'MQ' THEN 'American'
    WHEN 'OH' THEN 'American'
    WHEN 'PT' THEN 'American'
    WHEN 'DL' THEN 'Delta'
    WHEN '9E' THEN 'Delta'
    WHEN 'UA' THEN 'United'
    WHEN 'C5' THEN 'United'
    WHEN 'G7' THEN 'United'
    WHEN 'ZW' THEN 'United'
    WHEN 'AS' THEN 'Alaska'
    WHEN 'QX' THEN 'Alaska'
    WHEN 'WN' THEN 'Southwest'
    WHEN 'B6' THEN 'JetBlue'
    WHEN 'F9' THEN 'Frontier'
    WHEN 'NK' THEN 'Spirit'
    WHEN 'G4' THEN 'Allegiant'
    WHEN 'HA' THEN 'Hawaiian'
    WHEN 'OO' THEN 'SkyWest'
    WHEN 'YX' THEN 'Republic'
    ELSE 'Other'
END
"""

# Marketing brands a reader can name. SkyWest and Republic are operators.
MARKETING = [
    "Alaska",
    "Allegiant",
    "American",
    "Delta",
    "Frontier",
    "Hawaiian",
    "JetBlue",
    "Southwest",
    "Spirit",
    "United",
]

PAPER = "#f4f1ea"
INK = "#1c1915"
MUTED = "#5c564c"
BAR = "#8c4a2f"
BAR_SOFT = "#c4b59a"
TEAL = "#2f5d62"


def connect() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(DB_PATH), read_only=True)


def pct(rate: float) -> float:
    return round(rate * 100, 1)


def pp(gap: float) -> float:
    return round(gap * 100, 2)


def absorbing_residual(values: np.ndarray, groups: list[np.ndarray], rounds: int = 50) -> np.ndarray:
    """Residual after additive group means. Iterated demeaning."""
    out = np.asarray(values, dtype=np.float64).copy()
    for _ in range(rounds):
        previous = out.copy()
        for group in groups:
            counts = np.bincount(group)
            means = np.bincount(group, weights=out) / counts
            out -= means[group]
        if np.max(np.abs(out - previous)) < 1e-10:
            break
    return out


def centered_brand_effects(y: np.ndarray, brand: np.ndarray, groups: list[np.ndarray]) -> np.ndarray:
    """Percentage-point-ready gaps versus the flight-weighted mean, after the groups.

    One brand is omitted in the regression, then the coefficients are shifted so the
    flight-weighted average effect is zero. That shift is the gap versus the
    average flight, which is the national comparison once the groups are removed.
    """
    y_resid = absorbing_residual(y, groups)
    brands = np.unique(brand)
    kept = brands[1:]
    demeaned = []
    for code in kept:
        demeaned.append(absorbing_residual((brand == code).astype(np.float64), groups))
    x = np.column_stack(demeaned)
    xtx = x.T @ x
    xty = x.T @ y_resid
    beta = np.linalg.solve(xtx, xty)
    gamma = np.zeros(len(brands), dtype=np.float64)
    gamma[1:] = beta
    weights = np.bincount(brand, minlength=len(brands)).astype(np.float64)
    weights /= weights.sum()
    return gamma - np.sum(weights * gamma)


def matched_gap(con: duckdb.DuckDBPyConnection, keys: str) -> list[dict]:
    """Flight-weighted (brand rate - other-flights rate) inside each key cell."""
    sql = f"""
    WITH base AS (
        SELECT {BRAND_SQL} AS brand, {keys},
               CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END AS y
        FROM flights_v
        WHERE operated
    ),
    cell AS (
        SELECT brand, {keys}, COUNT(*) AS n, AVG(y) AS rate
        FROM base
        GROUP BY brand, {keys}
    ),
    totals AS (
        SELECT {keys}, SUM(n) AS n_all, SUM(n * rate) AS on_time_all
        FROM cell
        GROUP BY {keys}
    )
    SELECT c.brand,
           SUM(c.n) AS matched_flights,
           SUM(c.n * (c.rate - (t.on_time_all - c.n * c.rate) / (t.n_all - c.n)))
               / SUM(c.n) AS gap
    FROM cell c
    JOIN totals t USING ({keys})
    WHERE c.n >= {MIN_SIDE}
      AND (t.n_all - c.n) >= {MIN_SIDE}
    GROUP BY c.brand
    ORDER BY c.brand
    """
    rows = []
    for brand, flights, gap in con.execute(sql).fetchall():
        rows.append({"brand": brand, "matched_flights": int(flights), "gap": float(gap)})
    return rows


def volume_and_causes(con: duckdb.DuckDBPyConnection) -> dict:
    carriers = [
        {"airline_code": code, "scheduled": int(scheduled), "operated": int(operated)}
        for code, scheduled, operated in con.execute(
            """
            SELECT airline_code, COUNT(*) AS scheduled, SUM(CASE WHEN operated THEN 1 ELSE 0 END) AS operated
            FROM flights_v
            GROUP BY airline_code
            ORDER BY operated DESC
            """
        ).fetchall()
    ]
    brands = []
    for brand, operated, rate, first_month, last_month in con.execute(
        f"""
        SELECT {BRAND_SQL} AS brand,
               COUNT(*) AS operated,
               AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END) AS rate,
               MIN(printf('%04d-%02d', year, month)) AS first_month,
               MAX(printf('%04d-%02d', year, month)) AS last_month
        FROM flights_v
        WHERE operated
        GROUP BY 1
        ORDER BY rate DESC
        """
    ).fetchall():
        brands.append(
            {
                "brand": brand,
                "operated": int(operated),
                "on_time_rate": float(rate),
                "first_month": first_month,
                "last_month": last_month,
            }
        )
    national = con.execute(
        """
        SELECT COUNT(*) AS operated,
               AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END) AS rate
        FROM flights_v
        WHERE operated
        """
    ).fetchone()
    composition = [
        {"brand": brand, "airline_code": code, "operated": int(operated)}
        for brand, code, operated in con.execute(
            f"""
            SELECT {BRAND_SQL} AS brand, airline_code, COUNT(*) AS operated
            FROM flights_v
            WHERE operated
            GROUP BY 1, 2
            ORDER BY 1, 3 DESC
            """
        ).fetchall()
    ]
    buckets = [
        {
            "bucket": bucket,
            "airports": int(airports),
            "operated": int(operated),
            "on_time_rate": float(rate),
        }
        for bucket, airports, operated, rate in con.execute(
            """
            WITH airport AS (
                SELECT origin, COUNT(*) AS n,
                       AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END) AS rate
                FROM flights_v
                WHERE operated
                GROUP BY origin
            ),
            labeled AS (
                SELECT *,
                       CASE
                           WHEN n < 10000 THEN 'Under 10,000 departures'
                           WHEN n < 50000 THEN '10,000 to 49,999'
                           WHEN n < 150000 THEN '50,000 to 149,999'
                           ELSE '150,000 or more'
                       END AS bucket,
                       CASE
                           WHEN n < 10000 THEN 1
                           WHEN n < 50000 THEN 2
                           WHEN n < 150000 THEN 3
                           ELSE 4
                       END AS ord
                FROM airport
            )
            SELECT bucket, COUNT(*) AS airports, SUM(n) AS operated,
                   SUM(n * rate) / SUM(n) AS rate
            FROM labeled
            GROUP BY bucket, ord
            ORDER BY ord
            """
        ).fetchall()
    ]
    airport_corr = con.execute(
        """
        WITH airport AS (
            SELECT origin, COUNT(*) AS n,
                   AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END) AS rate
            FROM flights_v
            WHERE operated
            GROUP BY origin
            HAVING COUNT(*) >= 500
        )
        SELECT corr(ln(n), rate) AS corr_log_volume, COUNT(*) AS airports
        FROM airport
        """
    ).fetchone()
    hours = [
        {"hour": int(hour), "operated": int(operated), "on_time_rate": float(rate)}
        for hour, operated, rate in con.execute(
            """
            SELECT sched_dep_hour, COUNT(*) AS operated,
                   AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END) AS rate
            FROM flights_v
            WHERE operated
            GROUP BY 1
            ORDER BY 1
            """
        ).fetchall()
    ]
    causes = [
        {
            "brand": brand,
            "late_flights": int(late),
            "carrier_share": float(carrier),
            "weather_share": float(weather),
            "nas_share": float(nas),
            "security_share": float(security),
            "late_aircraft_share": float(late_aircraft),
            "airline_controlled_share": float(own),
        }
        for brand, late, carrier, weather, nas, security, late_aircraft, own in con.execute(
            f"""
            SELECT {BRAND_SQL} AS brand,
                   COUNT(*) AS late_flights,
                   SUM(carrier_delay) / SUM(cause_minutes) AS carrier_share,
                   SUM(weather_delay) / SUM(cause_minutes) AS weather_share,
                   SUM(nas_delay) / SUM(cause_minutes) AS nas_share,
                   SUM(security_delay) / SUM(cause_minutes) AS security_share,
                   SUM(late_aircraft_delay) / SUM(cause_minutes) AS late_aircraft_share,
                   SUM(airline_controlled_minutes) / SUM(cause_minutes) AS own_share
            FROM flights_v
            WHERE late_arrival AND cause_minutes > 0
            GROUP BY 1
            ORDER BY 1
            """
        ).fetchall()
    ]
    return {
        "carriers": carriers,
        "brands": brands,
        "national_operated": int(national[0]),
        "national_on_time_rate": float(national[1]),
        "composition": composition,
        "airport_buckets": buckets,
        "airport_log_volume_correlation": float(airport_corr[0]),
        "airports_in_correlation": int(airport_corr[1]),
        "hours": hours,
        "causes": causes,
    }


def dfw_spotlight(con: duckdb.DuckDBPyConnection) -> dict:
    at_dfw = [
        {
            "brand": brand,
            "operated": int(operated),
            "on_time_rate": float(rate),
        }
        for brand, operated, rate in con.execute(
            f"""
            SELECT {BRAND_SQL} AS brand, COUNT(*) AS operated,
                   AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END) AS rate
            FROM flights_v
            WHERE operated AND origin = 'DFW'
            GROUP BY 1
            ORDER BY operated DESC
            """
        ).fetchall()
    ]
    shared = [
        {
            "brand": brand,
            "routes": int(routes),
            "operated": int(operated),
            "gap_vs_others": float(gap),
        }
        for brand, routes, operated, gap in con.execute(
            f"""
            WITH base AS (
                SELECT {BRAND_SQL} AS brand, dest,
                       CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END AS y
                FROM flights_v
                WHERE operated AND origin = 'DFW'
            ),
            cell AS (
                SELECT brand, dest, COUNT(*) AS n, AVG(y) AS rate
                FROM base GROUP BY 1, 2
            ),
            totals AS (
                SELECT dest, SUM(n) AS n_all, SUM(n * rate) AS on_time_all
                FROM cell GROUP BY 1
            )
            SELECT c.brand, COUNT(*) AS routes, SUM(c.n) AS operated,
                   SUM(c.n * (c.rate - (t.on_time_all - c.n * c.rate) / (t.n_all - c.n)))
                       / SUM(c.n) AS gap
            FROM cell c
            JOIN totals t USING (dest)
            WHERE c.n >= {MIN_SIDE} AND (t.n_all - c.n) >= {MIN_SIDE}
            GROUP BY 1
            ORDER BY 1
            """
        ).fetchall()
    ]
    city_pairs = [
        {
            "city": city,
            "american_flights": int(aa_n),
            "american_on_time_rate": float(aa_rate),
            "southwest_flights": int(wn_n),
            "southwest_on_time_rate": float(wn_rate),
            "gap_american_minus_southwest": float(aa_rate - wn_rate),
        }
        for city, aa_n, aa_rate, wn_n, wn_rate in con.execute(
            f"""
            WITH legs AS (
                SELECT dest_city_name AS city,
                       CASE
                           WHEN {BRAND_SQL} = 'American' AND origin = 'DFW' THEN 'American'
                           WHEN airline_code = 'WN' AND origin = 'DAL' THEN 'Southwest'
                       END AS side,
                       CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END AS y
                FROM flights_v
                WHERE operated
                  AND (
                      ({BRAND_SQL} = 'American' AND origin = 'DFW')
                      OR (airline_code = 'WN' AND origin = 'DAL')
                  )
            )
            SELECT city,
                   SUM(CASE WHEN side = 'American' THEN 1 ELSE 0 END) AS aa_n,
                   AVG(CASE WHEN side = 'American' THEN y END) AS aa_rate,
                   SUM(CASE WHEN side = 'Southwest' THEN 1 ELSE 0 END) AS wn_n,
                   AVG(CASE WHEN side = 'Southwest' THEN y END) AS wn_rate
            FROM legs
            WHERE side IS NOT NULL
            GROUP BY city
            HAVING aa_n >= 200 AND wn_n >= 200
            ORDER BY aa_n + wn_n DESC
            """
        ).fetchall()
    ]
    if city_pairs:
        weight = sum(row["american_flights"] for row in city_pairs)
        weighted_gap = sum(row["american_flights"] * row["gap_american_minus_southwest"] for row in city_pairs) / weight
    else:
        weighted_gap = None
    return {
        "origin_dfw": at_dfw,
        "shared_dfw_routes": shared,
        "dallas_city_pairs": city_pairs,
        "dallas_weighted_gap_american_minus_southwest": weighted_gap,
        "dallas_pair_rule": "American-brand departures from DFW versus Southwest departures from DAL to the same BTS destination city, each side at least 200 operated flights. The gap is weighted by American's flights.",
    }


def fit_models(con: duckdb.DuckDBPyConnection) -> dict:
    frame = con.execute(
        f"""
        SELECT {BRAND_SQL} AS brand,
               origin,
               dest,
               sched_dep_hour,
               year * 100 + month AS year_month,
               CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END AS y
        FROM flights_v
        WHERE operated
        """
    ).fetchnumpy()
    brand_names, brand = np.unique(frame["brand"], return_inverse=True)
    origin = np.unique(frame["origin"], return_inverse=True)[1].astype(np.int32)
    dest = np.unique(frame["dest"], return_inverse=True)[1].astype(np.int32)
    hour = frame["sched_dep_hour"].astype(np.int32)
    month = np.unique(frame["year_month"], return_inverse=True)[1].astype(np.int32)
    y = frame["y"].astype(np.float64)
    groups = [origin, dest, hour, month]
    effects = centered_brand_effects(y, brand.astype(np.int32), groups)
    lpm = []
    for name, effect in zip(brand_names.tolist(), effects.tolist()):
        lpm.append({"brand": name, "gap_after_origin_dest_hour_month": float(effect)})

    # Logistic check on about one flight in seven, deterministic hash, same controls.
    sample = con.execute(
        f"""
        SELECT {BRAND_SQL} AS brand,
               origin, dest, sched_dep_hour AS hour, year * 100 + month AS year_month,
               CASE WHEN on_time_arrival THEN 1 ELSE 0 END AS y
        FROM flights_v
        WHERE operated
          AND abs(hash(airline_code || origin || dest || CAST(flight_date AS VARCHAR) || COALESCE(flight_number, ''))) % 7 = 0
        """
    ).fetchnumpy()
    sample_brand_names, sample_brand = np.unique(sample["brand"], return_inverse=True)
    sample_origin_names, sample_origin = np.unique(sample["origin"], return_inverse=True)
    sample_dest_names, sample_dest = np.unique(sample["dest"], return_inverse=True)
    sample_hour = sample["hour"].astype(np.int32)
    sample_month_names, sample_month = np.unique(sample["year_month"], return_inverse=True)
    sample_y = sample["y"].astype(np.int8)
    # Drop tiny airports so a rare dummy does not separate the logit.
    origin_n = np.bincount(sample_origin)
    dest_n = np.bincount(sample_dest)
    keep = (origin_n[sample_origin] >= 80) & (dest_n[sample_dest] >= 80)
    logit = _logit_gaps(
        sample_y[keep],
        sample_brand[keep],
        sample_origin[keep],
        sample_dest[keep],
        sample_hour[keep],
        sample_month[keep],
        sample_brand_names.tolist(),
    )
    return {
        "lpm": lpm,
        "logit": logit,
        "logit_rows": int(keep.sum()),
        "logit_rows_before_airport_floor": int(len(sample_y)),
        "logit_converged": bool(getattr(_logit_gaps, "converged", False)),
        "logit_iterations": int(getattr(_logit_gaps, "n_iter", 0)),
        "logit_sample": "about 1 in 7 operated flights, abs(hash(airline, origin, dest, date, flight number)) % 7 = 0, then origins and destinations with at least 80 sample flights",
        "lpm_rows": int(len(y)),
        "brand_names_in_lpm": brand_names.tolist(),
        "sample_brand_names": sample_brand_names.tolist(),
        "sample_airports_kept": {
            "origins": int(len(np.unique(sample_origin[keep]))),
            "destinations": int(len(np.unique(sample_dest[keep]))),
        },
        "unused_name_counts": {
            "origins_in_sample": int(len(sample_origin_names)),
            "destinations_in_sample": int(len(sample_dest_names)),
            "months": int(len(sample_month_names)),
        },
    }


def _logit_gaps(y, brand, origin, dest, hour, month, brand_names: list[str]) -> list[dict]:
    from scipy import sparse

    def dummies(codes: np.ndarray, drop_first: bool) -> sparse.csr_matrix:
        columns = int(codes.max()) + 1
        start = 1 if drop_first else 0
        rows = np.flatnonzero(codes >= start)
        cols = codes[rows] - start
        data = np.ones(len(rows), dtype=np.float64)
        return sparse.csr_matrix((data, (rows, cols)), shape=(len(codes), columns - start))

    pieces = [
        dummies(brand, drop_first=True),
        dummies(np.unique(origin, return_inverse=True)[1], drop_first=True),
        dummies(np.unique(dest, return_inverse=True)[1], drop_first=True),
        dummies(hour, drop_first=True),
        dummies(np.unique(month, return_inverse=True)[1], drop_first=True),
    ]
    x = sparse.hstack(pieces, format="csr")
    model = LogisticRegression(solver="saga", C=1e6, max_iter=80, tol=1e-3, fit_intercept=True)
    model.fit(x, y)
    n_iter = int(np.max(model.n_iter_))
    _logit_gaps.converged = n_iter < 80
    _logit_gaps.n_iter = n_iter
    # Predictive margin: force every sampled flight onto one brand, keep the rest.
    n_brand = len(brand_names) - 1
    base = x.tolil()
    base[:, :n_brand] = 0
    means = []
    probe = min(40000, x.shape[0])
    base = base[:probe].tocsr()
    for index in range(len(brand_names)):
        forced = base.copy()
        if index > 0:
            forced[:, index - 1] = 1
        means.append(float(model.predict_proba(forced)[:, 1].mean()))
    actual = float(model.predict_proba(x[:probe])[:, 1].mean())
    rows = []
    for name, mean in zip(brand_names, means):
        rows.append(
            {
                "brand": name,
                "predicted_on_time_rate": mean,
                "gap_vs_sample_prediction": mean - actual,
            }
        )
    return rows


def draw_charts(payload: dict) -> list[str]:
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "text.color": INK,
            "axes.labelcolor": INK,
            "xtick.color": INK,
            "ytick.color": INK,
            "axes.edgecolor": "#d9cba8",
        }
    )
    paths = [
        _chart_brands(payload),
        _chart_volume(payload),
        _chart_gaps(payload),
        _chart_causes(payload),
    ]
    return [str(path.relative_to(ROOT)) for path in paths]


def _finish(fig, path: Path) -> Path:
    fig.savefig(path, dpi=100)
    plt.close(fig)
    return path


def _source(ax) -> None:
    ax.text(0.0, -0.08, SOURCE, transform=ax.transAxes, fontsize=14, color=MUTED, ha="left", va="top")


def _chart_brands(payload: dict) -> Path:
    rows = [row for row in payload["brands"] if row["brand"] in MARKETING]
    rows = sorted(rows, key=lambda row: row["on_time_rate"])
    labels = [_brand_label(row) for row in rows]
    rates = [row["on_time_rate"] * 100 for row in rows]
    national = payload["national_on_time_rate"] * 100
    fig, ax = plt.subplots(figsize=(12, 12), dpi=100, facecolor=PAPER)
    ax.set_facecolor(PAPER)
    colors = [TEAL if rate >= national else BAR for rate in rates]
    ax.barh(labels, rates, color=colors, height=0.72)
    ax.axvline(national, color=INK, linewidth=1.5, linestyle="--")
    ax.set_xlim(55, 96)
    ax.set_xlabel("Flights on time, percent", fontsize=18)
    ax.tick_params(axis="both", labelsize=16)
    for label, rate in zip(labels, rates):
        ax.text(rate + 0.4, label, f"{rate:.1f}", va="center", fontsize=14, color=INK)
    ax.set_title("Who arrives on time", fontsize=32, loc="left", pad=16, color=INK)
    ax.text(
        0.0,
        1.02,
        "Share of flights that landed under 15 minutes late. Dashed line is every airline.",
        transform=ax.transAxes,
        fontsize=16,
        color=MUTED,
    )
    fig.text(0.08, 0.055, "*Hawaiian reported through Dec 2025. Spirit reported through May 2026.", fontsize=13, color=MUTED)
    _source(ax)
    fig.tight_layout(rect=(0, 0.07, 1, 0.96))
    return _finish(fig, CHART_DIR / "01_who_arrives_on_time.png")


def _brand_label(row: dict) -> str:
    if row["brand"] == "Hawaiian":
        return "Hawaiian*"
    if row["brand"] == "Spirit":
        return "Spirit*"
    return row["brand"]


def _chart_volume(payload: dict) -> Path:
    buckets = payload["airport_buckets"]
    hours = payload["hours"]
    fig, axes = plt.subplots(1, 2, figsize=(12, 6.27), dpi=100, facecolor=PAPER)
    for ax in axes:
        ax.set_facecolor(PAPER)
    axes[0].bar(
        [row["bucket"].replace(" departures", "").replace(" to ", "\nto ") for row in buckets],
        [row["on_time_rate"] * 100 for row in buckets],
        color=BAR,
    )
    axes[0].set_ylim(70, 85)
    axes[0].set_ylabel("On time, percent", fontsize=16)
    axes[0].set_title("Busier airports", fontsize=22, loc="left", color=INK)
    axes[0].tick_params(axis="both", labelsize=12)
    axes[1].plot([row["hour"] for row in hours], [row["on_time_rate"] * 100 for row in hours], color=TEAL, linewidth=3)
    axes[1].set_xlim(0, 23)
    axes[1].set_ylim(60, 100)
    axes[1].set_xlabel("Scheduled departure hour, local", fontsize=16)
    axes[1].set_title("Time of day", fontsize=22, loc="left", color=INK)
    axes[1].tick_params(axis="both", labelsize=14)
    fig.suptitle("Evenings are much later. The biggest airports, a little.", fontsize=26, x=0.06, ha="left", color=INK)
    fig.text(0.06, 0.02, SOURCE, fontsize=13, color=MUTED)
    fig.tight_layout(rect=(0.02, 0.06, 1, 0.88))
    return _finish(fig, CHART_DIR / "02_busier_airports.png")


def _chart_gaps(payload: dict) -> Path:
    raw = {row["brand"]: row["on_time_rate"] - payload["national_on_time_rate"] for row in payload["brands"]}
    controlled = {row["brand"]: row["gap_after_origin_dest_hour_month"] for row in payload["lpm"]}
    names = [name for name in MARKETING if name in raw and name in controlled]
    names = sorted(names, key=lambda name: raw[name])
    fig, ax = plt.subplots(figsize=(12, 12), dpi=100, facecolor=PAPER)
    ax.set_facecolor(PAPER)
    ypos = np.arange(len(names))
    raw_pp = [raw[name] * 100 for name in names]
    controlled_pp = [controlled[name] * 100 for name in names]
    for y, left, right in zip(ypos, raw_pp, controlled_pp):
        ax.plot([left, right], [y, y], color="#d9cba8", linewidth=3, zorder=1)
    ax.scatter(raw_pp, ypos, s=90, color=BAR_SOFT, label="Before, versus all flights", zorder=2)
    ax.scatter(controlled_pp, ypos, s=90, color=BAR, label="After same airports, cities, hour, and month", zorder=2)
    ax.axvline(0, color=INK, linewidth=1)
    ax.set_xlim(-9, 8)
    ax.set_yticks(ypos)
    ax.set_yticklabels(["Hawaiian*" if name == "Hawaiian" else "Spirit*" if name == "Spirit" else name for name in names], fontsize=16)
    for y, value in zip(ypos, controlled_pp):
        ax.text(value + (0.15 if value >= 0 else -0.15), y, f"{value:+.1f}", va="center", ha="left" if value >= 0 else "right", fontsize=13, color=INK)
    ax.tick_params(axis="x", labelsize=14)
    ax.set_xlabel("Percentage points versus the average flight", fontsize=18)
    ax.set_title("Holding the schedule fixed", fontsize=32, loc="left", pad=18, color=INK)
    ax.text(
        0.0,
        1.02,
        "Dark dot holds the airport, destination, hour, and month fixed. Light dot is the raw gap.",
        transform=ax.transAxes,
        fontsize=16,
        color=MUTED,
    )
    ax.legend(frameon=False, fontsize=14, loc="lower right")
    _source(ax)
    fig.tight_layout(rect=(0, 0.04, 1, 0.95))
    return _finish(fig, CHART_DIR / "03_airline_still_shows.png")


def _chart_causes(payload: dict) -> Path:
    order = sorted(
        [row for row in payload["causes"] if row["brand"] in MARKETING],
        key=lambda row: row["airline_controlled_share"],
    )
    labels = [_brand_label(row) for row in order]
    own = np.array([row["airline_controlled_share"] * 100 for row in order])
    other = 100 - own
    fig, ax = plt.subplots(figsize=(12, 6.27), dpi=100, facecolor=PAPER)
    ax.set_facecolor(PAPER)
    ax.barh(labels, own, color=BAR, label="Airline and late inbound plane")
    ax.barh(labels, other, left=own, color="#d9cba8", label="Weather, airspace, and security")
    ax.set_xlim(0, 112)
    for label, value in zip(labels, own):
        ax.text(value + 1, label, f"{value:.0f}%", va="center", fontsize=13, color=INK)
    ax.tick_params(axis="both", labelsize=14)
    ax.set_xlabel("Share of late-arrival minutes, percent", fontsize=16)
    ax.set_title("Whose minutes are whose", fontsize=28, loc="left", color=INK)
    ax.legend(frameon=False, fontsize=14, loc="lower right")
    fig.text(0.01, 0.02, SOURCE, fontsize=13, color=MUTED)
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    return _finish(fig, CHART_DIR / "04_whose_minutes.png")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    con = connect()
    try:
        payload = volume_and_causes(con)
        payload["origin_matched"] = matched_gap(con, "origin")
        payload["route_matched"] = matched_gap(con, "origin, dest")
        payload["hour_matched"] = matched_gap(con, "sched_dep_hour")
        payload["dfw"] = dfw_spotlight(con)
        print("fitting controls...", flush=True)
        payload.update(fit_models(con))
    finally:
        con.close()
    payload["definitions"] = {
        "on_time": "Operated flight with arrival delay under 15 minutes. Exactly 15 minutes is late.",
        "operated": "Not cancelled, not diverted, and arrival delay is present.",
        "brand_rule": "American is AA, MQ, OH, PT. Delta is DL, 9E. United is UA, C5, G7, ZW. Alaska is AS, QX. Other marketing brands are their own reporting code. SkyWest (OO) and Republic (YX) are not assigned.",
        "marketing_carrier_field": "The BTS reporting-carrier extract used here has no marketing-airline column. Checked on the July 2026 file header.",
        "matched_minimum": MIN_SIDE,
        "own_fault": "Carrier delay plus late-aircraft delay, divided by the five BTS cause columns, on late arrivals.",
        "source": SOURCE,
    }
    paths = draw_charts(payload)
    payload["charts"] = paths
    destination = OUT_DIR / "results.json"
    destination.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {destination}")
    national = payload["national_on_time_rate"]
    print(f"national {national:.4f} n={payload['national_operated']}")
    controlled = {row["brand"]: row["gap_after_origin_dest_hour_month"] for row in payload["lpm"]}
    for row in payload["brands"]:
        gap = row["on_time_rate"] - national
        after = controlled.get(row["brand"])
        after_text = f"{after * 100:+.2f} pp" if after is not None else "n/a"
        print(f"{row['brand']:12} {row['on_time_rate']*100:5.1f}%  raw {gap*100:+5.2f} pp  after {after_text}  n={row['operated']}")


if __name__ == "__main__":
    main()
