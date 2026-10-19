"""Command line for ingest, the draft bank, the analyst, the eval, and the scoreboard."""

from __future__ import annotations

import argparse
import json

from flight_analyst.llm import get_client


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="flight_analyst", description="Draft self-grading flight-delay analyst")
    sub = parser.add_subparsers(dest="cmd", required=True)

    ingest = sub.add_parser("ingest", help="Download the latest 12 BTS months into DuckDB")
    ingest.add_argument("--months", type=int, default=12)

    sub.add_parser("build-bank", help="Compute draft candidate answers from reference SQL")

    ask = sub.add_parser("ask", help="Ask the analyst one question")
    ask.add_argument("question")
    ask.add_argument("--provider", default=None, help="stub, openai, or anthropic. Defaults from the environment.")

    evaluate = sub.add_parser("eval", help="Score the analyst against the draft bank")
    evaluate.add_argument("--provider", default=None)

    sub.add_parser("scoreboard", help="Write scoreboard/index.html from the latest eval report")
    sub.add_parser("build-site", help="Write the static site/ folder from the draft bank and the latest eval")
    sub.add_parser("mcp", help="Serve read-only flight tools over MCP stdio.")

    live = sub.add_parser("live", help="Listen for questions. Refuses unless LIVE_ASK=1.")
    live.add_argument("--host", default="127.0.0.1")
    live.add_argument("--port", type=int, default=8080)

    bank_cmd = sub.add_parser("build-anticipated", help="Precompute the anticipated bank (cached Sonnet answers + vectors). $0.")
    bank_cmd.add_argument("--run", default="results/claude-sonnet-4-5-r1.json")
    bank_cmd.add_argument("--include-failed", action="store_true", help="Also cache answers the grader marked wrong.")

    review = sub.add_parser(
        "daily-review",
        help="Queue one day of new live questions and user-reported misses. New questions are graded by Sonnet (paid, capped). Misses are not sent to a model.",
    )
    review.add_argument("--day", default=None, help="YYYY-MM-DD (UTC). Default: yesterday.")

    promote_cmd = sub.add_parser("review-promote", help="Categorize a queued question, or approve or reject a pending preview.")
    promote_cmd.add_argument("--day", default=None, help="YYYY-MM-DD. Required with --category.")
    promote_cmd.add_argument("--id", action="append", default=None, help="Queue id. Required with --category. Repeatable.")
    mode = promote_cmd.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--category",
        default=None,
        help="Required for a queue row. New questions: bank category. Feedback misses: send-to-sonnet, "
        "fix-cached-answer, fix-routing, out-of-scope, or no-change. send-to-sonnet saves a preview and does not write the bank.",
    )
    mode.add_argument("--approve", default=None, help="Write one PENDING preview into the anticipated bank as APPROVED.")
    mode.add_argument("--reject", default=None, help="Drop one PENDING preview. Does not write the bank.")
    promote_cmd.add_argument("--note", default="", help="Optional note stored with --reject.")

    feedback_cmd = sub.add_parser("feedback", help="Record that a live answer missed. $0. No model call.")
    feedback_cmd.add_argument("--question", required=True)
    feedback_cmd.add_argument("--answer", required=True)
    feedback_cmd.add_argument("--route", required=True, choices=["cached-sonnet", "haiku"])
    feedback_cmd.add_argument("--model", required=True)
    feedback_cmd.add_argument("--expected", default="", help="What I expected. Capped at 1000 characters.")
    feedback_cmd.add_argument("--match-score", type=float, default=None)
    feedback_cmd.add_argument("--matched-id", default=None)
    feedback_cmd.add_argument("--file", default=None, help="JSONL path. Default: data/feedback.jsonl")

    args = parser.parse_args(argv)
    if args.cmd == "build-anticipated":
        from pathlib import Path

        from flight_analyst.routing import bank_from_run

        bank = bank_from_run(Path(args.run), passed_only=not args.include_failed)
        bank.save()
        print(f"anticipated entries={len(bank.entries)} embedder={bank.embedder.name}")
        return
    if args.cmd in {"daily-review", "review-promote"}:
        import datetime as dt

        from flight_analyst import daily_review as dr

        if args.cmd == "review-promote":
            try:
                if args.approve:
                    n = dr.approve_pending([args.approve])
                    print(f"approved={n} id={args.approve}")
                elif args.reject:
                    n = dr.reject_pending([args.reject], note=args.note)
                    print(f"rejected={n} id={args.reject}")
                else:
                    if not args.day or not args.id:
                        raise SystemExit("review-promote --category needs --day and --id.")
                    n = dr.promote(dt.date.fromisoformat(args.day), args.id, args.category)
                    if dr.category_is_send_to_sonnet(args.category):
                        pending = dr.load_pending()
                        for item_id in args.id:
                            item = pending.get(item_id)
                            if item:
                                print(dr.format_preview(item))
                        print(f"pending={n} category=send-to-sonnet")
                    else:
                        print(f"categorized={n} category={args.category}")
            except ValueError as exc:
                raise SystemExit(str(exc)) from exc
            return
        day = dt.date.fromisoformat(args.day) if args.day else dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
        # Sonnet is built on the first new question, so a day that is only feedback makes no model call.
        holder: dict = {}

        def answer(question: str) -> dict:
            if "answer" not in holder:
                holder["answer"], holder["grade"] = dr.sonnet_functions()
            return holder["answer"](question)

        def grade(system: str, user: str) -> str:
            if "grade" not in holder:
                holder["answer"], holder["grade"] = dr.sonnet_functions()
            return holder["grade"](system, user)

        queue = dr.run_review(day, answer, grade)
        print(json.dumps({k: v for k, v in queue.items() if k != "candidates"} | {"candidates": len(queue["candidates"])}))
        return
    if args.cmd == "feedback":
        from pathlib import Path

        from flight_analyst.feedback import FeedbackLog, record_feedback

        log = FeedbackLog(Path(args.file)) if args.file else None
        try:
            record = record_feedback({
                "question": args.question,
                "answer_text": args.answer,
                "route": args.route,
                "model": args.model,
                "expected": args.expected,
                "match_score": args.match_score,
                "matched_id": args.matched_id,
            }, log=log)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        recheck = f" recheck={record['matched_id']}" if record["recheck_cached"] and record["matched_id"] else ""
        print(f"recorded id={record['id']} route={record['route']}{recheck}")
        return
    if args.cmd == "ingest":
        from flight_analyst.ingest import ingest as run_ingest

        run_ingest(args.months)
        return
    if args.cmd == "build-bank":
        from flight_analyst.bank import build_and_write

        path = build_and_write()
        print(path)
        return
    if args.cmd == "ask":
        from flight_analyst.analyst import Analyst
        from flight_analyst.paths import DB_PATH

        client = _client(args.provider)
        result = Analyst(DB_PATH, client=client).answer(args.question)
        print(result.answer_text)
        if result.sql:
            print("\nSQL:\n" + result.sql)
        if result.chart:
            print("\nChart:\n" + json.dumps(result.chart, indent=2))
        return
    if args.cmd == "eval":
        from flight_analyst.evaluate import run_evaluation

        report = run_evaluation(client=_client(args.provider))
        summary = report["summary"]
        print(f"provider={report['provider']} accuracy={summary['accuracy']} correct={summary['n_correct']}/{summary['n']}")
        print("failures", json.dumps(summary["by_failure"], sort_keys=True))
        return
    if args.cmd == "scoreboard":
        from flight_analyst.scoreboard import build_scoreboard

        print(build_scoreboard())
        return
    if args.cmd == "build-site":
        from flight_analyst.sitebuild import build_site

        manifest = build_site()
        print(
            f"site_bytes={manifest['site_bytes']} extract_bytes={manifest['extract_bytes']} "
            f"flight_rows_published={manifest['flight_rows_published']}"
        )
        return
    if args.cmd == "live":
        from flight_analyst.live import serve

        serve(args.host, args.port)
        return
    if args.cmd == "mcp":
        from flight_analyst.mcp_server import main as serve_mcp

        serve_mcp()


def _client(provider: str | None):
    if not provider:
        return get_client()
    import os

    os.environ["LLM_PROVIDER"] = provider
    return get_client()


if __name__ == "__main__":
    main()
