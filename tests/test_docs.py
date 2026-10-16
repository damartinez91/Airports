from pathlib import Path

from flight_analyst.analyst import build_system_prompt
from flight_analyst.definitions import DOC_PHRASES, SHAPE_EXAMPLES
from flight_analyst.schema import render_schema_md

ROOT = Path(__file__).resolve().parents[1]


def test_definition_phrases_are_in_the_doc_and_the_briefing():
    doc = (ROOT / "docs" / "definitions.md").read_text()
    prompt = build_system_prompt("2025-08-01 through 2026-07-31")
    for phrase in DOC_PHRASES:
        assert phrase in doc
        assert phrase in prompt
    assert SHAPE_EXAMPLES.strip() in prompt
    for line in SHAPE_EXAMPLES.splitlines():
        if line.startswith("SELECT") or line.startswith("WITH") or line.startswith("Answer-shape"):
            assert line in doc


def test_schema_doc_matches_the_dictionary():
    assert (ROOT / "docs" / "schema.md").read_text().strip() == render_schema_md().strip()
