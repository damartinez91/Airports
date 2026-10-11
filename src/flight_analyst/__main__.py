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

    live = sub.add_parser("live", help="Listen for questions. Refuses unless LIVE_ASK=1.")
    live.add_argument("--host", default="127.0.0.1")
    live.add_argument("--port", type=int, default=8080)

    args = parser.parse_args(argv)
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


def _client(provider: str | None):
    if not provider:
        return get_client()
    import os

    os.environ["LLM_PROVIDER"] = provider
    return get_client()


if __name__ == "__main__":
    main()
