"""Build the static site.

The published folder is HTML plus two JSON extracts: the draft candidate
answers, and the latest eval report. The DuckDB file stays on disk. A
visitor never downloads flight-level rows, and the pages do not need a
server or a database.
"""

from __future__ import annotations

import html
import json
import shutil
from pathlib import Path

from flight_analyst.analyst import _chart
from flight_analyst.bank import REVIEW_NOTE, load_bank
from flight_analyst.paths import BANK_PATH, DB_PATH, REPORTS_DIR, ROOT, SITE_DIR
from flight_analyst.scoreboard import render_scoreboard

_ROW_CAP = 12


def _esc(value: object, quote: bool = False) -> str:
    return html.escape("" if value is None else str(value), quote=quote)


def _months(window: dict) -> list[str]:
    months = []
    for item in window.get("months") or []:
        if isinstance(item, str):
            months.append(item)
        elif isinstance(item, dict) and "year" in item and "month" in item:
            months.append(f"{item['year']}-{int(item['month']):02d}")
    return months


def _public_window(window: dict) -> dict:
    return {
        "flight_date_min": window.get("flight_date_min"),
        "flight_date_max": window.get("flight_date_max"),
        "row_count": window.get("row_count"),
        "operated": window.get("operated"),
        "on_time_arrival_rate": window.get("on_time_arrival_rate"),
        "cancellation_rate": window.get("cancellation_rate"),
        "airline_count": window.get("airline_count"),
        "origin_count": window.get("origin_count"),
        "months": _months(window),
    }


def _format_value(name: str, value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        lowered = name.lower()
        if any(token in lowered for token in ("rate", "share", "pct", "percent")):
            return f"{float(value) * 100:.1f}%"
        if isinstance(value, float):
            text = f"{value:.2f}".rstrip("0").rstrip(".")
            return text or "0"
        return f"{value:,}"
    return str(value)


def _columns(rows: list[dict]) -> list[str]:
    if not rows:
        return []
    keys = list(rows[0].keys())
    if "label" in keys:
        keys.remove("label")
        return ["label", *keys]
    return keys


def _public_question(item: dict) -> dict:
    expected = item.get("expected") or {}
    rows = list(expected.get("rows") or [])[:_ROW_CAP]
    behavior = expected.get("behavior")
    columns = _columns(rows)
    chart = _chart(rows, columns) if rows else None
    return {
        "id": item["id"],
        "question": item["question"],
        "difficulty": item["difficulty"],
        "category": item["category"],
        "ambiguous": bool(item.get("ambiguous")),
        "answer_type": item.get("answer_type"),
        "notes": item.get("notes") or "",
        "status": item.get("status") or "DRAFT",
        "interpretation": item.get("interpretation") or "",
        "follow_up": item.get("follow_up") or _default_follow_up(item),
        "alternatives": item.get("alternatives") or "",
        "why_limit": item.get("why_limit") or "",
        "minimum_rule": item.get("minimum_rule") or "",
        "sql": item.get("sql"),
        "candidate": {
            "behavior": behavior,
            "rows": rows,
            "truncated": len(expected.get("rows") or []) > _ROW_CAP,
        },
        "chart": chart,
    }


def _public_report(report: dict) -> dict:
    misses = []
    matches = []
    for row in report.get("results") or []:
        if row.get("passed"):
            matches.append(row.get("id"))
            continue
        misses.append(
            {
                "id": row.get("id"),
                "difficulty": row.get("difficulty"),
                "category": row.get("category"),
                "question": row.get("question"),
                "failure_category": row.get("failure_category"),
                "detail": row.get("detail"),
                "analyst_sql": row.get("analyst_sql"),
                "reference_sql": row.get("reference_sql"),
            }
        )
    return {
        "status": report.get("status"),
        "review_note": report.get("review_note"),
        "scoring_basis": report.get("scoring_basis"),
        "created_at": report.get("created_at"),
        "provider": report.get("provider"),
        "model": report.get("model"),
        "data_window": report.get("data_window") or {},
        "summary": report.get("summary") or {},
        "misses": misses,
        "matches": matches,
    }


def _bars(chart: dict | None) -> str:
    if not chart:
        return ""
    points = chart.get("points") or []
    values = [
        point.get("value")
        for point in points
        if isinstance(point.get("value"), (int, float)) and not isinstance(point.get("value"), bool)
    ]
    if not values or min(values) < 0 or max(values) <= 0:
        return ""
    peak = max(values)
    # Fractions (rates and shares) use an absolute 0–100 scale so 82% does not
    # look the same length as 50%. Counts and minutes stay scaled to the largest bar.
    absolute = peak <= 1
    rows = []
    y_name = str(chart.get("y") or "value")
    for point in points:
        value = point.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            width = 0
        elif absolute:
            width = max(0.0, float(value) * 100)
        else:
            width = max(0.0, float(value) / peak * 100)
        rows.append(
            "<div class='bar'>"
            f"<span>{_esc(point.get('label'))}</span>"
            f"<span class='track'><span class='fill' style='width:{width:.1f}%'></span></span>"
            f"<span>{_esc(_format_value(y_name, value))}</span>"
            "</div>"
        )
    return f"<div class='chart' role='img' aria-label='{_esc(y_name, quote=True)}'>{''.join(rows)}</div>"


def _table(rows: list[dict]) -> str:
    columns = _columns(rows)
    if not columns:
        return ""
    head = "".join(f"<th>{_esc(column)}</th>" for column in columns)
    body = []
    for row in rows:
        cells = "".join(f"<td>{_esc(_format_value(column, row.get(column)))}</td>" for column in columns)
        body.append(f"<tr>{cells}</tr>")
    return f"<div class='table-wrap'><table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"


def _default_follow_up(item: dict) -> str:
    category = item.get("category") or ""
    if category == "delay_causes":
        return "The BTS codes cannot say why. Want the carrier, weather, airspace, security, and late-aircraft split?"
    if category in {"airport_reliability"}:
        return "Want the same cut for large airports, with the minimum-flights rule stated?"
    if category == "routes":
        return "Want each direction named on its own, with the minutes on every route?"
    return "Want to see this across the big 3 business airlines, with and without operating partners?"


def _status_label(status: str) -> str:
    if status == "APPROVED":
        return "Daniel-approved"
    if status == "DRAFT-REVISED":
        return "Draft, revised"
    return "Draft"


def _question_card(item: dict) -> str:
    search = _esc(f"{item['id']} {item['question']} {item['category']}".lower(), quote=True)
    badges = [
        f"<span class='badge'>{_esc(_status_label(item.get('status') or 'DRAFT'))}</span>",
        f"<span class='badge'>{_esc(item['difficulty'])}</span>",
        f"<span class='badge'>{_esc(item['category'].replace('_', ' '))}</span>",
    ]
    if item["ambiguous"]:
        badges.append("<span class='badge'>ambiguous</span>")
    if item["candidate"]["behavior"] == "refuse":
        answer = "<p>The draft bank expects a refusal. There is no candidate row.</p>"
    else:
        answer = _bars(item["chart"]) + _table(item["candidate"]["rows"])
        if item["candidate"]["truncated"]:
            answer += f"<p class='meta'>Showing the first {_ROW_CAP} candidate rows.</p>"
    sql = item.get("sql") or ""
    sql_block = f"<details><summary>Reference SQL</summary><pre>{_esc(sql)}</pre></details>" if sql else ""
    notes = f"<p class='note'>{_esc(item['notes'])}</p>" if item.get("notes") else ""
    reading = f"<h3>Reading</h3><p>{_esc(item['interpretation'])}</p>" if item.get("interpretation") else ""
    cuts = f"<h3>Alternative cuts</h3><p>{_esc(item['alternatives'])}</p>" if item.get("alternatives") else ""
    why = f"<h3>What the codes cannot say</h3><p>{_esc(item['why_limit'])}</p>" if item.get("why_limit") else ""
    floor = f"<h3>Minimum flights</h3><p>{_esc(item['minimum_rule'])}</p>" if item.get("minimum_rule") else ""
    follow = f"<h3>Likely follow-up</h3><p>{_esc(item.get('follow_up') or '')}</p>" if item.get("follow_up") else ""
    return (
        f"<article class='question' id='{_esc(item['id'], quote=True)}' "
        f"data-difficulty='{_esc(item['difficulty'], quote=True)}' "
        f"data-category='{_esc(item['category'], quote=True)}' "
        f"data-search='{search}'>"
        f"<p class='meta'>{_esc(item['id'])}</p>"
        f"<h2>{_esc(item['question'])}</h2>"
        f"<p class='badges'>{''.join(badges)}</p>"
        f"{notes}"
        f"{reading}"
        f"<h3>Candidate answer</h3>"
        f"{answer}"
        f"{cuts}"
        f"{why}"
        f"{floor}"
        f"{follow}"
        f"{sql_block}"
        "</article>"
    )


def _nav(prefix: str) -> str:
    return (
        "<nav>"
        f"<a href='{prefix}index.html'>Overview</a>"
        f"<a href='{prefix}explore/'>Explore</a>"
        f"<a href='{prefix}scoreboard/'>Scoreboard</a>"
        "</nav>"
    )


_CSS = """
:root { color-scheme: light; }
body { margin: 0; background: #f4f1ea; color: #1c1915; font: 18px/1.45 "Iowan Old Style", Palatino, Georgia, serif; }
main { max-width: 900px; margin: 0 auto; padding: 32px 20px 72px; }
h1 { font-weight: 600; font-size: 34px; letter-spacing: -0.02em; margin-bottom: 8px; }
h2 { font-size: 22px; margin: 8px 0; }
h3 { font: 13px/1.3 ui-sans-serif, sans-serif; letter-spacing: 0.04em; text-transform: uppercase; color: #5c564c; margin: 16px 0 8px; }
a { color: #1c1915; }
nav { font: 15px/1.4 ui-sans-serif, sans-serif; margin-bottom: 18px; }
nav a { margin-right: 16px; }
.banner { background: #efe6d2; border: 1px solid #d9cba8; padding: 12px 14px; margin: 18px 0 28px; }
.meta, .note { color: #5c564c; font: 15px/1.4 ui-sans-serif, sans-serif; }
.cards { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.cards a { display: block; background: white; border: 1px solid #e2dccd; padding: 14px 16px; text-decoration: none; }
.cards strong { display: block; font-size: 22px; margin-bottom: 4px; }
article.question { background: white; border: 1px solid #e2dccd; padding: 14px 16px; margin: 12px 0; }
.badges { display: flex; flex-wrap: wrap; gap: 6px; }
.badge { font: 12px/1.2 ui-sans-serif, sans-serif; letter-spacing: 0.03em; text-transform: uppercase; border: 1px solid #d9cba8; padding: 3px 6px; }
.filters { position: sticky; top: 0; background: #f4f1ea; padding: 8px 0 12px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
button, select, input { font: 15px/1.2 ui-sans-serif, sans-serif; background: white; color: #1c1915; border: 1px solid #d9cba8; padding: 6px 8px; }
button[aria-pressed="true"] { background: #1c1915; color: #f4f1ea; }
table { width: 100%; border-collapse: collapse; font-size: 15px; }
th, td { border-bottom: 1px solid #e2dccd; text-align: left; vertical-align: top; padding: 6px; }
th { font: 12px/1.3 ui-sans-serif, sans-serif; letter-spacing: 0.04em; text-transform: uppercase; color: #5c564c; }
pre { white-space: pre-wrap; font: 12px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.bar { display: grid; grid-template-columns: 6.5rem 1fr auto; gap: 8px; align-items: center; margin: 4px 0; font: 14px/1.3 ui-sans-serif, sans-serif; }
.track { background: #efe6d2; height: 10px; }
.fill { display: block; height: 10px; background: #8c4a2f; }
article[hidden] { display: none; }
.table-wrap { overflow-x: auto; }
@media (max-width: 700px) { .cards { grid-template-columns: 1fr; } .bar { grid-template-columns: 4.5rem 1fr auto; } }
"""

_FILTER_JS = """
(function () {
  var input = document.getElementById("find");
  var select = document.getElementById("category");
  var buttons = document.querySelectorAll("[data-difficulty]");
  var difficulty = "all";
  function apply() {
    var text = input.value.trim().toLowerCase();
    var category = select.value;
    document.querySelectorAll("article.question").forEach(function (card) {
      var hay = card.getAttribute("data-search") || "";
      var show = (difficulty === "all" || card.getAttribute("data-difficulty") === difficulty)
        && (category === "all" || card.getAttribute("data-category") === category)
        && (!text || hay.indexOf(text) !== -1);
      card.hidden = !show;
    });
  }
  buttons.forEach(function (button) {
    button.addEventListener("click", function () {
      difficulty = button.getAttribute("data-difficulty");
      buttons.forEach(function (other) { other.setAttribute("aria-pressed", other === button ? "true" : "false"); });
      apply();
    });
  });
  input.addEventListener("input", apply);
  select.addEventListener("change", apply);
})();
"""


def _page(title: str, prefix: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<link rel="stylesheet" href="{prefix}assets/site.css">
</head>
<body>
<main>
{_nav(prefix)}
{body}
</main>
</body>
</html>
"""


def _overview(bank: dict, manifest_hint: str) -> str:
    window = _public_window(bank.get("data_window") or {})
    rate = window.get("on_time_arrival_rate")
    rate_text = f"{rate * 100:.1f}%" if isinstance(rate, (int, float)) else "n/a"
    months = ", ".join(window.get("months") or [])
    row_count = window.get("row_count")
    row_text = f"{row_count:,}" if isinstance(row_count, int) else ""
    return _page(
        "Flight delay analyst, draft",
        "",
        f"""
<p class="meta">US DOT on-time performance</p>
<h1>Flight delay analyst</h1>
<div class="banner"><strong>DRAFT.</strong> {_esc(bank.get("review_note") or "")}. These pages show candidate answers and the latest eval. They are pending review.</div>
<p>The answers below were computed ahead of time. This site does not ship the flight database, and it does not run SQL in the browser.</p>
<p class="meta">Window {_esc(window.get("flight_date_min"))} to {_esc(window.get("flight_date_max"))}. {_esc(row_text)} scheduled flights. On-time arrival rate {rate_text}. Months {_esc(months)}.</p>
<div class="cards">
  <a href="explore/"><strong>Explore</strong> Draft candidate answers, charts, and reference SQL for the question bank.</a>
  <a href="scoreboard/"><strong>Scoreboard</strong> Latest eval: overall accuracy, difficulty, and misses with both SQL statements.</a>
</div>
<p class="meta">{_esc(manifest_hint)} Live questions are a separate process and are off on this site.</p>
""",
    )


def _explore(questions: list[dict], review_note: str) -> str:
    categories = sorted({item["category"] for item in questions})
    options = "<option value='all'>All categories</option>" + "".join(
        f"<option value='{_esc(category, quote=True)}'>{_esc(category.replace('_', ' '))}</option>" for category in categories
    )
    cards = "".join(_question_card(item) for item in questions)
    body = f"""
<p class="meta">US DOT on-time performance</p>
<h1>Explore</h1>
<div class="banner"><strong>DRAFT.</strong> {_esc(review_note)}. Each block is a candidate answer from the reference SQL, pending review.</div>
<div class="filters">
  <button type="button" data-difficulty="all" aria-pressed="true">All</button>
  <button type="button" data-difficulty="easy" aria-pressed="false">Easy</button>
  <button type="button" data-difficulty="medium" aria-pressed="false">Medium</button>
  <button type="button" data-difficulty="hard" aria-pressed="false">Hard</button>
  <select id="category">{options}</select>
  <input id="find" type="search" placeholder="Find a question" aria-label="Find a question">
</div>
{cards}
<script>{_FILTER_JS}</script>
"""
    return _page("Explore draft answers", "../", body)


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _directory_bytes(dest: Path) -> int:
    return sum(path.stat().st_size for path in dest.rglob("*") if path.is_file())


def build_site(
    bank_path: Path | None = None,
    report_path: Path | None = None,
    dest: Path | None = None,
) -> dict:
    bank_path = bank_path or BANK_PATH
    report_path = report_path or (REPORTS_DIR / "latest.json")
    dest = (dest or SITE_DIR).resolve()
    if dest == ROOT.resolve() or dest == Path(dest.anchor):
        raise RuntimeError("Refusing to replace the repository root.")
    bank = load_bank(bank_path)
    if bank.get("status") != "DRAFT" or bank.get("review_note") != REVIEW_NOTE:
        raise RuntimeError("Refusing to build the site unless the bank is DRAFT and still pending review.")
    if not report_path.exists():
        raise FileNotFoundError(f"No eval report at {report_path}. Run the eval harness first.")
    report = json.loads(report_path.read_text())
    if report.get("status") != "DRAFT" or report.get("review_note") != REVIEW_NOTE:
        raise RuntimeError("Refusing to build the site unless the eval report is DRAFT and still pending review.")

    questions = [_public_question(item) for item in bank["questions"]]
    public_report = _public_report(report)
    explore_payload = {
        "status": "DRAFT",
        "review_note": REVIEW_NOTE,
        "scoring_basis": bank.get("scoring_basis"),
        "data_window": _public_window(bank.get("data_window") or {}),
        "question_count": len(questions),
        "questions": questions,
    }
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "assets").mkdir(parents=True)
    (dest / "data").mkdir()
    (dest / "explore").mkdir()
    (dest / "scoreboard").mkdir()
    (dest / "assets" / "site.css").write_text(_CSS)
    (dest / ".nojekyll").write_text("")
    _write_json(dest / "data" / "explore.json", explore_payload)
    _write_json(dest / "data" / "report.json", public_report)
    (dest / "explore" / "index.html").write_text(_explore(questions, REVIEW_NOTE))
    score_nav = _nav("../")
    (dest / "scoreboard" / "index.html").write_text(render_scoreboard(report, nav=score_nav))

    extract_bytes = (dest / "data" / "explore.json").stat().st_size + (dest / "data" / "report.json").stat().st_size
    omitted = DB_PATH.stat().st_size if DB_PATH.exists() else None
    hint = (
        f"Published extracts are {extract_bytes / 1000:.1f} KB "
        "(data/explore.json and data/report.json). "
        "The folder total is in data/manifest.json. Flight-level rows published: 0."
    )
    (dest / "index.html").write_text(_overview(bank, hint))
    manifest = {
        "status": "DRAFT",
        "review_note": REVIEW_NOTE,
        "extract_bytes": extract_bytes,
        "flight_rows_published": 0,
        "database_included": False,
        "omitted_database_bytes": omitted,
        "question_count": len(questions),
        "site_bytes": 0,
    }
    manifest_path = dest / "data" / "manifest.json"
    for _ in range(6):
        _write_json(manifest_path, manifest)
        total = _directory_bytes(dest)
        if manifest["site_bytes"] == total:
            break
        manifest["site_bytes"] = total
    return manifest
