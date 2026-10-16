"""List-price table for eval cost. Edit prices.json to change a rate."""

from __future__ import annotations

import json
from pathlib import Path

PRICES_PATH = Path(__file__).with_name("prices.json")
SCHEMA_VERSION = "model-run-v1"


class UnpricedModel(KeyError):
    """The prices table has no row for this model id."""


def load_prices(path: Path | None = None) -> dict:
    return json.loads((path or PRICES_PATH).read_text())


def price_row(model: str, path: Path | None = None) -> dict:
    table = load_prices(path)
    row = (table.get("models") or {}).get(model)
    if not row:
        raise UnpricedModel(
            f"No list price for model '{model}'. Add a row to {PRICES_PATH.name} "
            "(provider, input_per_million_usd, output_per_million_usd, source, as_of) and rerun."
        )
    for key in ("input_per_million_usd", "output_per_million_usd", "source", "as_of", "provider"):
        if key not in row:
            raise UnpricedModel(f"Price row for '{model}' is missing {key}.")
    return row


def quote(model: str, input_tokens: int, output_tokens: int, path: Path | None = None) -> float:
    row = price_row(model, path)
    cost = (
        int(input_tokens) * float(row["input_per_million_usd"])
        + int(output_tokens) * float(row["output_per_million_usd"])
    ) / 1_000_000
    return round(cost, 8)


def over_budget(spent_usd: float, cap_usd: float | None) -> bool:
    if cap_usd is None:
        return False
    return spent_usd >= cap_usd
