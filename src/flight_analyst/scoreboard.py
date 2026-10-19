"""Static HTML scoreboard for the latest draft eval report."""

from __future__ import annotations

import html
import json
from pathlib import Path

from flight_analyst.paths import REPORTS_DIR, SCOREBOARD_PATH


def _esc(value: object) -> str:
    # Element text, so apostrophes stay literal (the review note contains one).
    return html.escape("" if value is None else str(value), quote=False)


def _run_line(run: dict | None, role: str) -> str:
    if not run:
        return ""
    summary = run.get("summary") or {}
    overall = summary.get("all") or {}
    approved = summary.get("approved") or {}
    acc = overall.get("accuracy")
    pct = f"{acc * 100:.0f}%" if isinstance(acc, (int, float)) else "n/a"
    cost = summary.get("cost_usd")
    cost_text = f"${cost:.2f} list price for the full bank" if isinstance(cost, (int, float)) else ""
    appr = f"{approved.get('n_correct')}/{approved.get('n')} on Daniel-approved answers" if approved.get("n") else ""
    bits = " · ".join(b for b in (appr, cost_text) if b)
    return (
        f"<section class='headline-run'><p class='meta'>{_esc(role)}</p>"
        f"<p class='big'>{_esc(run.get('run_label'))}: {overall.get('n_correct')} / {overall.get('n')} ({pct})</p>"
        f"<p class='meta'>{_esc(bits)}</p></section>"
    )


def _headline_html(headline: dict | None, low_cost: dict | None) -> str:
    if not headline and not low_cost:
        return ""
    return (
        "<h2>Model scores</h2><div class='cards two'>"
        + _run_line(headline, "Headline model")
        + _run_line(low_cost, "Low-cost tier")
        + "</div><p class='meta'>One saved run per model, graded on all 120 questions. "
        "Per-question answers are on the Compare page.</p>"
    )


def render_scoreboard(report: dict, nav: str = "", headline: dict | None = None, low_cost: dict | None = None) -> str:
    summary = report["summary"]
    is_stub = str(report.get("provider") or "") == "stub"
    accuracy = summary.get("accuracy")
    accuracy_text = f"{accuracy * 100:.1f}%" if accuracy is not None else "n/a"
    difficulty_cards = []
    for name in ("easy", "medium", "hard"):
        slot = summary.get("by_difficulty", {}).get(name)
        if not slot:
            continue
        shown = f"{slot['accuracy'] * 100:.1f}%" if slot.get("accuracy") is not None else "n/a"
        difficulty_cards.append(
            f"<section><h3>{_esc(name)}</h3><p class='big'>{shown}</p><p>{slot['n_correct']} / {slot['n']}</p></section>"
        )
    failure_bits = []
    for name, count in sorted((summary.get("by_failure") or {}).items()):
        failure_bits.append(f"<li>{_esc(name)}: {count}</li>")
    failure_html = "<ul>" + "".join(failure_bits) + "</ul>" if failure_bits else "<p>No misses.</p>"

    miss_rows = []
    for row in report["results"]:
        if row["passed"]:
            continue
        miss_rows.append(
            "<tr>"
            f"<td>{_esc(row['id'])}</td>"
            f"<td>{_esc(row['difficulty'])}</td>"
            f"<td>{_esc(row['failure_category'] or '')}</td>"
            f"<td>{_esc(row['question'])}<p class='detail'>{_esc(row.get('detail') or '')}</p></td>"
            f"<td><pre>{_esc(row.get('analyst_sql') or '')}</pre></td>"
            f"<td><pre>{_esc(row.get('reference_sql') or '')}</pre></td>"
            "</tr>"
        )
    match_ids = ", ".join(row["id"] for row in report["results"] if row["passed"]) or "None"
    window = report.get("data_window") or {}
    months = ", ".join(window.get("months") or [])
    provider = report.get("provider") or ""
    model = f" / {report['model']}" if report.get("model") else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Flight delay analyst, draft scoreboard</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ margin: 0; background: #f4f1ea; color: #1c1915; font: 18px/1.45 "Iowan Old Style", Palatino, Georgia, serif; }}
  main {{ max-width: 1100px; margin: 0 auto; padding: 40px 24px 80px; }}
  h1 {{ font-weight: 600; font-size: 34px; letter-spacing: -0.02em; margin-bottom: 8px; }}
  h2 {{ font-size: 22px; margin-top: 36px; }}
  .banner {{ background: #efe6d2; border: 1px solid #d9cba8; padding: 12px 14px; margin: 18px 0 28px; }}
  .overall {{ font-size: 64px; line-height: 1; margin: 8px 0; font-variant-numeric: tabular-nums; }}
  .cards {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }}
  .cards.two {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
  .cards section {{ min-width: 0; overflow-wrap: anywhere; }}
  .table-wrap {{ overflow-x: auto; }}
  td pre {{ max-width: 320px; overflow-x: auto; }}
  .cards section {{ background: white; border: 1px solid #e2dccd; padding: 12px 14px; }}
  .big {{ font-size: 28px; margin: 4px 0; font-variant-numeric: tabular-nums; }}
  table {{ width: 100%; border-collapse: collapse; background: white; font-size: 15px; }}
  th, td {{ border-bottom: 1px solid #e2dccd; text-align: left; vertical-align: top; padding: 8px; }}
  th {{ font: 13px/1.3 ui-sans-serif, sans-serif; letter-spacing: 0.04em; text-transform: uppercase; color: #5c564c; }}
  pre {{ white-space: pre-wrap; font: 12px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; margin: 0; }}
  .detail {{ color: #5c564c; margin: 6px 0 0; }}
  .meta {{ color: #5c564c; font: 15px/1.4 ui-sans-serif, sans-serif; }}
  nav {{ font: 15px/1.4 ui-sans-serif, sans-serif; margin-bottom: 18px; }}
  nav a {{ color: inherit; margin-right: 16px; }}
  @media (max-width: 800px) {{ .cards {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<main>
  {nav}
  <p class="meta">US DOT on-time performance, draft analyst</p>
  <h1>Scoreboard</h1>
  <div class="banner">
    <strong>DRAFT.</strong> {_esc((report.get("review_note") or "").rstrip(".") + ".")}
    This page scores the analyst against candidate answers that are still pending review.
  </div>
  {_headline_html(headline, low_cost)}
  <h2>{"Rules baseline (harness check, not a model)" if is_stub else "Latest eval report"}</h2>
  {"<p class='meta'>The stub reads the reference SQL, so its score only shows the grader and the answer key agree. It is not an accuracy claim.</p>" if is_stub else ""}
  <p class="meta">Provider {_esc(provider)}{_esc(model)} · {_esc(report.get("created_at") or "")}</p>
  <p class="meta">Window {_esc(str(window.get("flight_date_min") or ""))} to {_esc(str(window.get("flight_date_max") or ""))} · {_esc(months)}</p>
  <p class="{'big' if is_stub and headline else 'overall'}">{accuracy_text}</p>
  <p class="meta">{summary.get("n_correct")} of {summary.get("n")} questions matched the candidate answer.</p>
  <div class="cards">
    {''.join(difficulty_cards)}
  </div>
  <h2>Failure categories</h2>
  {failure_html}
  <p class="meta">wrong_sql: the statement did not run. wrong_aggregation: the entities lined up and a figure did not. misread_question: the shape or the entities differed. refused: the analyst declined a question that has a candidate answer. error: the run broke before a SQL result.</p>
  <h2>Misses</h2>
  <div class="table-wrap"><table>
    <thead><tr><th>Id</th><th>Difficulty</th><th>Failure</th><th>Question</th><th>Analyst SQL</th><th>Reference SQL</th></tr></thead>
    <tbody>
      {''.join(miss_rows) or '<tr><td colspan="6">No misses.</td></tr>'}
    </tbody>
  </table></div>
  <h2>Matches</h2>
  <p>{_esc(match_ids)}</p>
</main>
</body>
</html>
"""


def build_scoreboard(report_path: Path | None = None, html_path: Path | None = None) -> Path:
    source = report_path or (REPORTS_DIR / "latest.json")
    if not source.exists():
        raise FileNotFoundError(f"No eval report at {source}. Run the eval harness first.")
    report = json.loads(source.read_text())
    dest = html_path or SCOREBOARD_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render_scoreboard(report))
    return dest
