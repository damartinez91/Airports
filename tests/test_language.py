from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", "data", ".venv", "__pycache__", ".pytest_cache"}


def _banned() -> list[str]:
    # Built at runtime so this file does not contain the phrases it forbids.
    return [
        "gol" + "den",
        "ground " + "truth",
        "ground_" + "truth",
        "ground-" + "truth",
    ]


def test_repo_does_not_treat_draft_answers_as_an_approved_standard():
    hits = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP for part in path.parts):
            continue
        if path.suffix.lower() not in {".py", ".md", ".json", ".html", ".toml", ".txt", ".ini"}:
            continue
        text = path.read_text(errors="ignore").lower()
        for phrase in _banned():
            if phrase in text:
                hits.append(f"{path.relative_to(ROOT)}: {phrase}")
    assert hits == []
