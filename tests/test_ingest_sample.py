import csv
from pathlib import Path

from flight_analyst.ingest import create_database, load_bts_csv, load_lookups
from flight_analyst.schema import BTS_FIELDS


def test_sample_bts_csv_lands_on_time_and_evening(tmp_path: Path):
    header = [source for source in BTS_FIELDS.values()]
    rows = [
        {
            "Year": "2026",
            "Quarter": "3",
            "Month": "7",
            "DayofMonth": "15",
            "DayOfWeek": "3",
            "FlightDate": "2026-07-15",
            "Reporting_Airline": "DL",
            "Tail_Number": "N1",
            "Flight_Number_Reporting_Airline": "100",
            "Origin": "ATL",
            "Dest": "RIC",
            "OriginCityName": "Atlanta, GA",
            "OriginState": "GA",
            "OriginStateName": "Georgia",
            "DestCityName": "Richmond, VA",
            "DestState": "VA",
            "DestStateName": "Virginia",
            "CRSDepTime": "1830",
            "DepTime": "1835",
            "DepDelay": "5",
            "CRSArrTime": "2010",
            "ArrTime": "2020",
            "ArrDelay": "10",
            "Cancelled": "0.00",
            "CancellationCode": "",
            "Diverted": "0.00",
            "CRSElapsedTime": "100",
            "ActualElapsedTime": "105",
            "AirTime": "80",
            "Distance": "480",
            "CarrierDelay": "",
            "WeatherDelay": "",
            "NASDelay": "",
            "SecurityDelay": "",
            "LateAircraftDelay": "",
        },
        {
            "Year": "2026",
            "Quarter": "3",
            "Month": "7",
            "DayofMonth": "15",
            "DayOfWeek": "3",
            "FlightDate": "2026-07-15",
            "Reporting_Airline": "AA",
            "Tail_Number": "N2",
            "Flight_Number_Reporting_Airline": "200",
            "Origin": "DFW",
            "Dest": "ORD",
            "OriginCityName": "Dallas/Fort Worth, TX",
            "OriginState": "TX",
            "OriginStateName": "Texas",
            "DestCityName": "Chicago, IL",
            "DestState": "IL",
            "DestStateName": "Illinois",
            "CRSDepTime": "900",
            "DepTime": "",
            "DepDelay": "",
            "CRSArrTime": "1130",
            "ArrTime": "",
            "ArrDelay": "",
            "Cancelled": "1.00",
            "CancellationCode": "B",
            "Diverted": "0.00",
            "CRSElapsedTime": "150",
            "ActualElapsedTime": "",
            "AirTime": "",
            "Distance": "800",
            "CarrierDelay": "",
            "WeatherDelay": "",
            "NASDelay": "",
            "SecurityDelay": "",
            "LateAircraftDelay": "",
        },
        {
            "Year": "2026",
            "Quarter": "3",
            "Month": "1",
            "DayofMonth": "10",
            "DayOfWeek": "6",
            "FlightDate": "2026-01-10",
            "Reporting_Airline": "WN",
            "Tail_Number": "N3",
            "Flight_Number_Reporting_Airline": "300",
            "Origin": "DAL",
            "Dest": "HOU",
            "OriginCityName": "Dallas, TX",
            "OriginState": "TX",
            "OriginStateName": "Texas",
            "DestCityName": "Houston, TX",
            "DestState": "TX",
            "DestStateName": "Texas",
            "CRSDepTime": "1400",
            "DepTime": "1410",
            "DepDelay": "10",
            "CRSArrTime": "1510",
            "ArrTime": "1540",
            "ArrDelay": "30",
            "Cancelled": "0.00",
            "CancellationCode": "",
            "Diverted": "0.00",
            "CRSElapsedTime": "70",
            "ActualElapsedTime": "90",
            "AirTime": "50",
            "Distance": "240",
            "CarrierDelay": "20",
            "WeatherDelay": "10",
            "NASDelay": "0",
            "SecurityDelay": "0",
            "LateAircraftDelay": "0",
        },
    ]
    csv_path = tmp_path / "sample.csv"
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    airports = tmp_path / "airports.csv"
    airports.write_text(
        "iata_code,name,municipality,iso_region,iso_country,scheduled_service,type\n"
        "DFW,Dallas Fort Worth International Airport,Dallas,US-TX,US,yes,large_airport\n"
        "ATL,Hartsfield Jackson Atlanta International Airport,Atlanta,US-GA,US,yes,large_airport\n"
    )
    db_path = tmp_path / "sample.duckdb"
    con = create_database(db_path)
    inserted = load_bts_csv(con, csv_path)
    load_lookups(con, airports)
    assert inserted == 3
    summary = con.execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE operated) AS operated,
            COUNT(*) FILTER (WHERE on_time_arrival) AS on_time,
            COUNT(*) FILTER (WHERE origin = 'ATL' AND dep_time_bucket = 'evening') AS atl_evening,
            COUNT(*) FILTER (WHERE origin = 'DAL' AND season = 'winter' AND late_arrival) AS dal_late,
            MAX(CASE WHEN origin = 'DFW' THEN origin_airport_name END) AS dfw_name
        FROM flights_v
        """
    ).fetchone()
    con.close()
    assert summary == (2, 1, 1, 1, "Dallas Fort Worth International Airport")
