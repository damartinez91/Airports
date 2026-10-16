"""Download the latest 12 BTS on-time months and load a local DuckDB file.

Raw zips and the database stay under data/ and are gitignored. A small vintage
summary is written to docs/data_window.json so the draft bank can name its window.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

from flight_analyst.paths import DATA_WINDOW_PATH, DB_PATH, RAW_DIR, ensure_dirs
from flight_analyst.schema import BTS_FIELDS, CARRIER_FAMILIES, CARRIER_NAMES, FLIGHT_COLUMNS

BTS_URL = (
    "https://transtats.bts.gov/PREZIP/"
    "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{year}_{month}.zip"
)
AIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
USER_AGENT = "Mozilla/5.0 (compatible; flight-analyst-ingest/0.1; research)"
AIRPORT_COUNTRIES = ("US", "PR", "GU", "VI", "AS", "MP")


def _request(url: str, method: str = "GET", timeout: int = 180) -> urllib.request.Request:
    return urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})


def _month_exists(year: int, month: int) -> bool:
    url = BTS_URL.format(year=year, month=month)
    try:
        with urllib.request.urlopen(_request(url, method="HEAD", timeout=40), timeout=40) as resp:
            length = int(resp.headers.get("Content-Length") or 0)
            ctype = (resp.headers.get("Content-Type") or "").lower()
            return resp.status == 200 and length > 1_000_000 and "html" not in ctype
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise


def discover_months(n: int = 12, lookback: int = 36, today: date | None = None) -> list[tuple[int, int]]:
    """Walk backward from the current month and keep the n newest files BTS has published."""
    cursor = today or date.today()
    year, month = cursor.year, cursor.month
    found: list[tuple[int, int]] = []
    for _ in range(lookback):
        print(f"checking {year}-{month:02d}", flush=True)
        if _month_exists(year, month):
            found.append((year, month))
            if len(found) == n:
                break
        month -= 1
        if month == 0:
            year -= 1
            month = 12
    if len(found) < n:
        raise RuntimeError(f"Found only {len(found)} BTS months; needed {n}.")
    found.reverse()
    return found


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, dest: Path, attempts: int = 3, min_bytes: int = 1_000_000) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > min_bytes:
        print(f"reuse {dest.name}", flush=True)
        return
    partial = dest.with_suffix(dest.suffix + ".partial")
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            print(f"download {url} (attempt {attempt})", flush=True)
            with urllib.request.urlopen(_request(url), timeout=300) as resp, partial.open("wb") as handle:
                shutil.copyfileobj(resp, handle)
            partial.replace(dest)
            return
        except Exception as exc:  # noqa: BLE001 - retry network failures
            last_error = exc
            if partial.exists():
                partial.unlink()
    raise RuntimeError(f"Failed to download {url}: {last_error}")


def _hour_sql(column: str) -> str:
    cast = f'TRY_CAST("{column}" AS INTEGER)'
    return f"""CASE
        WHEN {cast} IS NULL THEN NULL
        WHEN {cast} IN (2400, 24) THEN 0
        WHEN {cast} // 100 BETWEEN 0 AND 23 THEN {cast} // 100
        ELSE NULL
    END"""


def create_database(db_path: Path) -> duckdb.DuckDBPyConnection:
    if db_path.exists():
        db_path.unlink()
    wal = Path(str(db_path) + ".wal")
    if wal.exists():
        wal.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    columns = [f"{name} {typ}" for name, typ, _desc in FLIGHT_COLUMNS]
    con.execute(f"CREATE TABLE flights ({', '.join(columns)})")
    con.execute("CREATE TABLE metadata (key VARCHAR PRIMARY KEY, value VARCHAR)")
    return con


def _bts_expr(column: str) -> str:
    if column == "sched_dep_hour":
        return _hour_sql("CRSDepTime")
    if column == "sched_arr_hour":
        return _hour_sql("CRSArrTime")
    if column == "season":
        return """CASE TRY_CAST("Month" AS INTEGER)
            WHEN 12 THEN 'winter' WHEN 1 THEN 'winter' WHEN 2 THEN 'winter'
            WHEN 3 THEN 'spring' WHEN 4 THEN 'spring' WHEN 5 THEN 'spring'
            WHEN 6 THEN 'summer' WHEN 7 THEN 'summer' WHEN 8 THEN 'summer'
            ELSE 'fall'
        END"""
    source = BTS_FIELDS[column]
    if column == "flight_date":
        return f'TRY_CAST("{source}" AS DATE)'
    if column in {"cancelled", "diverted"}:
        return f'COALESCE(TRY_CAST("{source}" AS DOUBLE), 0) >= 0.5'
    if column == "cancellation_code":
        return f"NULLIF(UPPER(TRIM(\"{source}\")), '')"
    if column in {"airline_code", "origin", "dest", "tail_number", "flight_number", "origin_state", "dest_state"}:
        return f"NULLIF(UPPER(TRIM(\"{source}\")), '')"
    if column in {
        "year",
        "quarter",
        "month",
        "day_of_month",
        "day_of_week",
        "crs_dep_time",
        "dep_time",
        "crs_arr_time",
        "arr_time",
    }:
        return f'TRY_CAST("{source}" AS INTEGER)'
    if column.endswith("_name"):
        return f"NULLIF(TRIM(\"{source}\"), '')"
    return f'TRY_CAST("{source}" AS DOUBLE)'


def load_bts_csv(con: duckdb.DuckDBPyConnection, csv_path: Path) -> int:
    """Insert one BTS monthly CSV. Returns rows inserted from this file."""
    before = con.execute("SELECT COUNT(*) FROM flights").fetchone()[0]
    select_bits = [f"{_bts_expr(name)} AS {name}" for name, _typ, _desc in FLIGHT_COLUMNS]
    sql = f"""
        INSERT INTO flights
        SELECT {", ".join(select_bits)}
        FROM read_csv(?, header=true, all_varchar=true, nullstr='')
        WHERE TRY_CAST("FlightDate" AS DATE) IS NOT NULL
          AND NULLIF(TRIM("Origin"), '') IS NOT NULL
          AND NULLIF(TRIM("Dest"), '') IS NOT NULL
          AND NULLIF(TRIM("Reporting_Airline"), '') IS NOT NULL
    """
    con.execute(sql, [str(csv_path)])
    after = con.execute("SELECT COUNT(*) FROM flights").fetchone()[0]
    return int(after - before)


def _extract_csv(zip_path: Path, dest_dir: Path) -> Path:
    with zipfile.ZipFile(zip_path) as archive:
        csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not csv_names:
            raise RuntimeError(f"No CSV inside {zip_path}")
        name = max(csv_names, key=lambda item: archive.getinfo(item).file_size)
        archive.extract(name, dest_dir)
        return dest_dir / name


def load_lookups(con: duckdb.DuckDBPyConnection, airports_csv: Path | None) -> None:
    con.execute("CREATE TABLE carrier_name_map (airline_code VARCHAR PRIMARY KEY, airline_name VARCHAR)")
    con.executemany("INSERT INTO carrier_name_map VALUES (?, ?)", list(CARRIER_NAMES.items()))
    con.execute(
        """
        CREATE TABLE carriers AS
        SELECT codes.airline_code,
               COALESCE(carrier_name_map.airline_name, codes.airline_code) AS airline_name
        FROM (SELECT DISTINCT airline_code FROM flights) codes
        LEFT JOIN carrier_name_map USING (airline_code)
        ORDER BY 1
        """
    )
    con.execute(
        """
        CREATE TABLE carrier_families (
            airline_code VARCHAR PRIMARY KEY,
            family_code VARCHAR,
            family_name VARCHAR,
            affiliate_role VARCHAR
        )
        """
    )
    con.executemany(
        "INSERT INTO carrier_families VALUES (?, ?, ?, ?)",
        CARRIER_FAMILIES,
    )
    con.execute(
        """
        CREATE TABLE airports (
            airport_code VARCHAR PRIMARY KEY,
            airport_name VARCHAR,
            city VARCHAR,
            region VARCHAR,
            country VARCHAR
        )
        """
    )
    if airports_csv is not None and airports_csv.exists():
        countries = ", ".join(f"'{code}'" for code in AIRPORT_COUNTRIES)
        con.execute(
            f"""
            INSERT INTO airports
            SELECT airport_code, airport_name, city, region, country
            FROM (
                SELECT
                    UPPER(TRIM(iata_code)) AS airport_code,
                    name AS airport_name,
                    municipality AS city,
                    iso_region AS region,
                    iso_country AS country,
                    ROW_NUMBER() OVER (
                        PARTITION BY UPPER(TRIM(iata_code))
                        ORDER BY
                            CASE WHEN scheduled_service = 'yes' THEN 0 ELSE 1 END,
                            CASE type
                                WHEN 'large_airport' THEN 0
                                WHEN 'medium_airport' THEN 1
                                WHEN 'small_airport' THEN 2
                                ELSE 3
                            END
                    ) AS rn
                FROM read_csv(?, header=true, all_varchar=true, nullstr='')
                WHERE iata_code IS NOT NULL
                  AND TRIM(iata_code) <> ''
                  AND iso_country IN ({countries})
            )
            WHERE rn = 1
            """,
            [str(airports_csv)],
        )
    con.execute(
        """
        INSERT INTO airports
        SELECT code, code, NULL, NULL, NULL
        FROM (
            SELECT origin AS code FROM flights
            UNION
            SELECT dest AS code FROM flights
        ) codes
        WHERE code NOT IN (SELECT airport_code FROM airports)
        """
    )
    con.execute(
        """
        CREATE OR REPLACE VIEW flights_v AS
        SELECT
            f.*,
            COALESCE(c.airline_name, f.airline_code) AS airline_name,
            COALESCE(ao.airport_name, f.origin) AS origin_airport_name,
            COALESCE(ad.airport_name, f.dest) AS dest_airport_name,
            (NOT f.cancelled AND NOT f.diverted AND f.arr_delay IS NOT NULL) AS operated,
            CASE
                WHEN f.cancelled OR f.diverted OR f.arr_delay IS NULL THEN NULL
                WHEN f.arr_delay < 15 THEN TRUE
                ELSE FALSE
            END AS on_time_arrival,
            CASE
                WHEN f.cancelled OR f.diverted OR f.arr_delay IS NULL THEN NULL
                WHEN f.arr_delay >= 15 THEN TRUE
                ELSE FALSE
            END AS late_arrival,
            CASE
                WHEN f.cancelled OR f.dep_delay IS NULL THEN NULL
                WHEN f.dep_delay < 15 THEN TRUE
                ELSE FALSE
            END AS on_time_departure,
            CASE
                WHEN f.sched_dep_hour BETWEEN 5 AND 8 THEN 'morning'
                WHEN f.sched_dep_hour BETWEEN 9 AND 16 THEN 'midday'
                WHEN f.sched_dep_hour BETWEEN 17 AND 20 THEN 'evening'
                WHEN f.sched_dep_hour IS NULL THEN NULL
                ELSE 'night'
            END AS dep_time_bucket,
            CASE
                WHEN f.carrier_delay IS NULL
                 AND f.weather_delay IS NULL
                 AND f.nas_delay IS NULL
                 AND f.security_delay IS NULL
                 AND f.late_aircraft_delay IS NULL THEN NULL
                ELSE COALESCE(f.carrier_delay, 0)
                   + COALESCE(f.weather_delay, 0)
                   + COALESCE(f.nas_delay, 0)
                   + COALESCE(f.security_delay, 0)
                   + COALESCE(f.late_aircraft_delay, 0)
            END AS cause_minutes,
            CASE
                WHEN f.carrier_delay IS NULL AND f.late_aircraft_delay IS NULL THEN NULL
                ELSE COALESCE(f.carrier_delay, 0) + COALESCE(f.late_aircraft_delay, 0)
            END AS airline_controlled_minutes,
            fam.family_code,
            fam.family_name
        FROM flights f
        LEFT JOIN carriers c ON f.airline_code = c.airline_code
        LEFT JOIN airports ao ON f.origin = ao.airport_code
        LEFT JOIN airports ad ON f.dest = ad.airport_code
        LEFT JOIN carrier_families fam ON f.airline_code = fam.airline_code
        """
    )


def _put_meta(con: duckdb.DuckDBPyConnection, key: str, value: str) -> None:
    con.execute("INSERT INTO metadata VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value", [key, value])


def summarize(con: duckdb.DuckDBPyConnection) -> dict:
    row = con.execute(
        """
        SELECT
            COUNT(*) AS row_count,
            COUNT(*) FILTER (WHERE operated) AS operated,
            MIN(flight_date) AS flight_date_min,
            MAX(flight_date) AS flight_date_max,
            COUNT(DISTINCT airline_code) AS airline_count,
            COUNT(DISTINCT origin) AS origin_count,
            AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END) FILTER (WHERE operated) AS on_time_arrival_rate,
            AVG(CASE WHEN cancelled THEN 1.0 ELSE 0.0 END) AS cancellation_rate,
            AVG(CASE WHEN diverted THEN 1.0 ELSE 0.0 END) AS diversion_rate
        FROM flights_v
        """
    ).fetchone()
    keys = [
        "row_count",
        "operated",
        "flight_date_min",
        "flight_date_max",
        "airline_count",
        "origin_count",
        "on_time_arrival_rate",
        "cancellation_rate",
        "diversion_rate",
    ]
    summary = {}
    for key, value in zip(keys, row):
        if hasattr(value, "isoformat"):
            summary[key] = value.isoformat()
        elif isinstance(value, float):
            summary[key] = round(value, 6)
        else:
            summary[key] = int(value) if value is not None else None
    dup = con.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM flights)
            - (
                SELECT COUNT(*) FROM (
                    SELECT DISTINCT flight_date, airline_code, flight_number, origin, dest, crs_dep_time
                    FROM flights
                )
            )
        """
    ).fetchone()[0]
    summary["duplicate_operation_keys"] = int(dup)
    missing_names = con.execute(
        """
        SELECT airline_code, airline_name, COUNT(*) AS n
        FROM flights_v
        WHERE airline_name = airline_code
        GROUP BY 1, 2
        ORDER BY n DESC
        """
    ).fetchall()
    summary["carriers_without_readable_name"] = [
        {"airline_code": code, "flights": int(n)} for code, _name, n in missing_names
    ]
    return summary


def ingest(n_months: int = 12, db_path: Path | None = None) -> dict:
    ensure_dirs()
    db_path = db_path or DB_PATH
    months = discover_months(n_months)
    print("window", ", ".join(f"{year}-{month:02d}" for year, month in months), flush=True)
    month_records = []
    zip_paths = []
    for year, month in months:
        url = BTS_URL.format(year=year, month=month)
        dest = RAW_DIR / f"On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{year}_{month}.zip"
        download(url, dest)
        zip_paths.append((year, month, url, dest))
    airports_path = RAW_DIR / "ourairports.csv"
    download(AIRPORTS_URL, airports_path, min_bytes=100_000)

    con = create_database(db_path)
    try:
        for year, month, url, zip_path in zip_paths:
            print(f"load {year}-{month:02d}", flush=True)
            csv_path = _extract_csv(zip_path, RAW_DIR / "_extract")
            try:
                inserted = load_bts_csv(con, csv_path)
            finally:
                if csv_path.exists():
                    csv_path.unlink()
            record = {
                "year": year,
                "month": month,
                "url": url,
                "sha256": _sha256(zip_path),
                "rows": inserted,
            }
            month_records.append(record)
            print(f"  rows {inserted}", flush=True)
        load_lookups(con, airports_path)
        summary = summarize(con)
        if summary["row_count"] < 1_000_000:
            raise RuntimeError(f"Row count {summary['row_count']} is too small for 12 months of BTS data.")
        rate = summary["on_time_arrival_rate"]
        if rate is None or not (0.45 <= rate <= 0.95):
            raise RuntimeError(f"On-time arrival rate {rate} is outside the plausible band. Check parsing.")
        retrieved = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        window = {
            "source": "US DOT BTS Reporting Carrier On-Time Performance (1987-present)",
            "source_url_pattern": BTS_URL,
            "retrieved_at": retrieved,
            "months": month_records,
            "airports_lookup": {
                "source": "OurAirports",
                "url": AIRPORTS_URL,
                "sha256": _sha256(airports_path),
                "license": "public domain",
            },
            "carriers_lookup": {
                "source": "project map of BTS reporting-carrier names in schema.py",
            },
            **summary,
        }
        _put_meta(con, "data_window", json.dumps(window))
        DATA_WINDOW_PATH.parent.mkdir(parents=True, exist_ok=True)
        DATA_WINDOW_PATH.write_text(json.dumps(window, indent=2) + "\n")
        con.execute("CHECKPOINT")
        print(json.dumps({k: window[k] for k in ("flight_date_min", "flight_date_max", "row_count", "on_time_arrival_rate", "cancellation_rate")}, indent=2))
        return window
    finally:
        con.close()
        extract_dir = RAW_DIR / "_extract"
        if extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)


def connect_readonly(db_path: Path | None = None) -> duckdb.DuckDBPyConnection:
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"No database at {path}. Run `PYTHONPATH=src python3 -m flight_analyst ingest` first."
        )
    return duckdb.connect(str(path), read_only=True)
