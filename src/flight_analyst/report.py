"""JSON and Markdown eval reports."""

from __future__ import annotations

import json
from pathlib import Path


def _fence(sql: str | None) -> str:
    if not sql:
        return "_none_"
    return f"```sql\n{sql.strip()}\n```"


def render_markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# Draft eval report",
        "",
        f"Status: **{report['status']}**. {report.get('review_note')}",
        "",
        report.get("scoring_basis", ""),
        "",
        f"Provider: `{report.get('provider')}`"
        + (f" (`{report['model']}`)" if report.get("model") else ""),
        f"Run: {report.get('created_at')}",
        "",
        "## Overall",
        "",
        f"- Questions: {summary['n']}",
        f"- Matching the candidate answer: {summary['n_correct']}",
        f"- Accuracy: {summary['accuracy']:.1%}" if summary.get("accuracy") is not None else "- Accuracy: n/a",
        "",
        "## By difficulty",
        "",
        "| Difficulty | Correct | N | Accuracy |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, slot in sorted(summary.get("by_difficulty", {}).items()):
        accuracy = slot.get("accuracy")
        shown = f"{accuracy:.1%}" if accuracy is not None else ""
        lines.append(f"| {name} | {slot['n_correct']} | {slot['n']} | {shown} |")
    lines.extend(["", "## By category", "", "| Category | Correct | N | Accuracy |", "| --- | ---: | ---: | ---: |"])
    for name, slot in sorted(summary.get("by_category", {}).items()):
        accuracy = slot.get("accuracy")
        shown = f"{accuracy:.1%}" if accuracy is not None else ""
        lines.append(f"| {name} | {slot['n_correct']} | {slot['n']} | {shown} |")
    lines.extend(["", "## Failure categories", ""])
    failures = summary.get("by_failure") or {}
    if not failures:
        lines.append("No misses.")
    else:
        for name, count in sorted(failures.items()):
            lines.append(f"- {name}: {count}")
    lines.extend(
        [
            "",
            "Failure labels are a harness heuristic. `wrong_sql` means the statement did not run. "
            "`wrong_aggregation` means the entities lined up but a figure did not. "
            "`misread_question` means the shape or the entities were different. "
            "`refused` means the analyst declined a question that has a candidate answer. "
            "`error` means the run broke before a SQL result.",
            "",
            "## Misses",
            "",
        ]
    )
    misses = [row for row in report["results"] if not row["passed"]]
    if not misses:
        lines.append("None.")
    for row in misses:
        lines.extend(
            [
                f"### {row['id']} ({row['difficulty']}, {row['failure_category']})",
                "",
                row["question"],
                "",
                row.get("detail") or "",
                "",
                "Analyst SQL:",
                "",
                _fence(row.get("analyst_sql")),
                "",
                "Reference SQL:",
                "",
                _fence(row.get("reference_sql")),
                "",
            ]
        )
    lines.extend(["## Matches", ""])
    matches = [row["id"] for row in report["results"] if row["passed"]]
    lines.append(", ".join(matches) if matches else "None.")
    lines.append("")
    return "\n".join(lines)


def write_reports(report: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = report["created_at"].replace(":", "").replace("-", "")
    json_path = out_dir / f"eval-{stamp}.json"
    md_path = out_dir / f"eval-{stamp}.md"
    payload = json.dumps(report, indent=2) + "\n"
    markdown = render_markdown(report)
    json_path.write_text(payload)
    md_path.write_text(markdown)
    (out_dir / "latest.json").write_text(payload)
    (out_dir / "latest.md").write_text(markdown)
    return json_path, md_path
