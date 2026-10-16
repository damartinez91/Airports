"""Score the bank with one model and write a provider-neutral results file.

Example, on a machine that has a key:

    python -m flight_analyst.run_eval --provider anthropic --model claude-haiku-4-5

The default budget is $3 of list-price tokens. The run stops before the next
question once that cap is reached. The stub needs no key and costs $0.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from flight_analyst.evaluate import allocate_run_file, run_evaluation
from flight_analyst.llm import AnthropicClient, LLMError, OpenAIClient, StubClient
from flight_analyst.paths import RESULTS_DIR
from flight_analyst.pricing import UnpricedModel, price_row


def client_for(provider: str, model: str | None):
    provider = provider.strip().lower()
    if provider == "stub":
        return StubClient()
    if provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            raise LLMError("ANTHROPIC_API_KEY is not set. Export it and rerun. No request was sent.")
        chosen = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
        return AnthropicClient(key, chosen)
    if provider == "openai":
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise LLMError("OPENAI_API_KEY is not set. Export it and rerun. No request was sent.")
        chosen = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        return OpenAIClient(key, chosen)
    raise LLMError(
        f"Unknown provider '{provider}'. Use anthropic, openai, or stub. "
        "xAI and a local Qwen use the same results file once a client exists; the site already reads that file."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="flight_analyst.run_eval", description="Score the question bank and write a model-run file.")
    parser.add_argument("--provider", required=True, help="anthropic, openai, or stub")
    parser.add_argument("--model", default=None, help="Model id. Must have a row in prices.json.")
    parser.add_argument(
        "--out",
        default=None,
        help="Optional path. Omit it to write results/{model}-rN.json, a new file each run. An existing path is refused.",
    )
    parser.add_argument("--budget-usd", type=float, default=3.0, help="Hard list-price cap. Default $3. The next question is not sent once spend reaches it.")
    args = parser.parse_args(argv)
    if args.budget_usd < 0:
        print("budget must be zero or positive", file=sys.stderr)
        return 2
    try:
        client = client_for(args.provider, args.model)
        model_name = getattr(client, "model", None) or "stub"
        price_row(model_name)
    except (LLMError, UnpricedModel) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.out:
        model_out = Path(args.out)
        if model_out.exists():
            print(
                f"{model_out} already exists. Omit --out to write the next {model_name} file.",
                file=sys.stderr,
            )
            return 2
    else:
        model_out = allocate_run_file(model_name, RESULTS_DIR)
    report = run_evaluation(client=client, budget_usd=args.budget_usd, model_out=model_out)
    summary = report["summary"]
    approved = summary["approved_questions"]
    overall = summary["all_questions"]
    print(f"provider={report['provider']} model={report['model']}")
    print(
        f"accuracy_all={overall['accuracy']} ({overall['n_correct']}/{overall['n']}) "
        f"accuracy_approved={approved['accuracy']} ({approved['n_correct']}/{approved['n']})"
    )
    print(
        f"spent_usd={summary['spent_usd']} budget_usd={summary['budget_cap_usd']} "
        f"stopped_for_budget={summary['stopped_for_budget']} unscored={summary['n_unscored']}"
    )
    print(f"wrote {report['model_run_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
