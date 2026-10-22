import json
from pathlib import Path

from flight_analyst.bank import REVIEW_NOTE
from flight_analyst.sitebuild import build_site, load_model_runs

ROOT = Path(__file__).resolve().parents[1]


def _bank() -> dict:
    return {
        "status": "DRAFT",
        "review_note": REVIEW_NOTE,
        "scoring_basis": "DRAFT candidate answers computed from reference SQL. Not an approved standard.",
        "data_window": {
            "flight_date_min": "2025-10-01",
            "flight_date_max": "2025-10-01",
            "row_count": 240,
            "months": [{"year": 2025, "month": 10}],
        },
        "questions": [
            {
                "id": "q001",
                "question": "Which airline is most on time out of DFW?",
                "difficulty": "easy",
                "category": "airline_reliability",
                "ambiguous": False,
                "answer_type": "ranking",
                "notes": "Fixture note.",
                "sql": "SELECT airline_code AS label FROM flights_v",
                "expected": {
                    "rows": [
                        {"label": "AA", "on_time_rate": 0.8, "n": 120},
                        {"label": "WN", "on_time_rate": 0.4, "n": 120},
                    ]
                },
            },
            {
                "id": "q002",
                "question": "What was the on-time rate yesterday?",
                "difficulty": "hard",
                "category": "ambiguous_or_trick",
                "ambiguous": True,
                "answer_type": "refusal",
                "notes": "No live feed.",
                "sql": None,
                "expected": {"behavior": "refuse", "rows": []},
            },
        ],
    }


def _report() -> dict:
    return {
        "status": "DRAFT",
        "review_note": REVIEW_NOTE,
        "scoring_basis": "Scored against DRAFT candidate answers. Not an approved standard.",
        "created_at": "2026-10-10T00:00:00+00:00",
        "provider": "stub",
        "model": None,
        "data_window": {"flight_date_min": "2025-10-01", "flight_date_max": "2025-10-01", "row_count": 240, "months": ["2025-10"]},
        "summary": {
            "n": 2,
            "n_correct": 1,
            "accuracy": 0.5,
            "by_difficulty": {"easy": {"n": 1, "n_correct": 1, "accuracy": 1}, "hard": {"n": 1, "n_correct": 0, "accuracy": 0}},
            "by_category": {},
            "by_failure": {"misread_question": 1},
        },
        "results": [
            {"id": "q001", "passed": True, "difficulty": "easy", "question": "Which airline is most on time out of DFW?"},
            {
                "id": "q002",
                "passed": False,
                "difficulty": "hard",
                "category": "ambiguous_or_trick",
                "question": "What was the on-time rate yesterday?",
                "failure_category": "misread_question",
                "detail": "answered",
                "analyst_sql": "SELECT 1",
                "reference_sql": None,
            },
        ],
    }


def test_fixture_site_is_draft_static_and_has_a_chart(tmp_path: Path):
    bank_path = tmp_path / "bank.json"
    report_path = tmp_path / "report.json"
    bank_path.write_text(json.dumps(_bank()))
    report_path.write_text(json.dumps(_report()))
    dest = tmp_path / "site"
    manifest = build_site(bank_path, report_path, dest)
    assert manifest["status"] == "DRAFT"
    assert manifest["review_note"] == REVIEW_NOTE
    assert manifest["flight_rows_published"] == 0
    assert manifest["database_included"] is False
    assert manifest["site_bytes"] == sum(path.stat().st_size for path in dest.rglob("*") if path.is_file())
    page = (dest / "index.html").read_text()
    assert "DRAFT" in page
    assert REVIEW_NOTE in page
    assert 'href="explore/"' in page
    assert 'href="scoreboard/"' in page
    assert 'href="compare/"' in page
    compare = (dest / "compare" / "index.html").read_text()
    assert "Compare models" in compare
    assert "Price versus accuracy" in compare or "aria-label=\"Price versus accuracy\"" in compare
    assert "not run" in compare
    explore = json.loads((dest / "data" / "explore.json").read_text())
    assert explore["status"] == "DRAFT"
    assert explore["questions"][0]["chart"]["points"][0]["label"] == "AA"
    explore_html = (dest / "explore" / "index.html").read_text()
    assert "Candidate answer" in explore_html
    assert "expects a refusal" in explore_html
    assert "class='fill'" in explore_html
    score = (dest / "scoreboard" / "index.html").read_text()
    assert "50.0%" in score
    assert "SELECT 1" in score
    assert REVIEW_NOTE in score
    blob = "\n".join(path.read_text(errors="ignore") for path in dest.rglob("*") if path.is_file())
    assert "OPENAI_API_KEY" not in blob
    assert "sk-" not in blob
    for path in dest.rglob("*"):
        assert path.suffix.lower() not in {".duckdb", ".parquet", ".csv", ".zip"}


def test_repeat_runs_keep_distinct_labels(tmp_path: Path):
    folder = tmp_path / "results"
    folder.mkdir()
    shared = {
        "schema_version": "model-run-v1",
        "provider": "anthropic",
        "model": "claude-haiku-4-5",
        "summary": {"all": {"accuracy": 0.5}, "cost_per_100_usd": 0.4},
        "results": [],
    }
    (folder / "claude-haiku-4-5-r1.json").write_text(json.dumps({**shared, "run_label": "claude-haiku-4-5 r1"}))
    (folder / "claude-haiku-4-5-r2.json").write_text(json.dumps(shared))
    (folder / "claude-sonnet-4-5.json").write_text(
        json.dumps({**shared, "model": "claude-sonnet-4-5", "provider": "anthropic"})
    )
    labels = [run["run_label"] for run in load_model_runs(folder)]
    assert labels == ["claude-haiku-4-5 r1", "claude-haiku-4-5 r2", "claude-sonnet-4-5"]


def test_real_bank_site_stays_small(tmp_path: Path):
    manifest = build_site(dest=tmp_path / "site")
    assert manifest["question_count"] >= 100
    assert manifest["flight_rows_published"] == 0
    assert manifest["database_included"] is False
    assert manifest["extract_bytes"] < 1_200_000  # five saved runs (stub, Haiku r1-r3, Sonnet r1)
    assert manifest["site_bytes"] < 2_200_000  # compare page embeds all five runs
    explore = json.loads((tmp_path / "site" / "data" / "explore.json").read_text())
    assert explore["status"] == "DRAFT"
    assert explore["review_note"] == REVIEW_NOTE
    assert any(item["chart"] for item in explore["questions"])


def test_build_refuses_a_bank_that_is_not_draft(tmp_path: Path):
    bank = _bank()
    bank["status"] = "READY"
    bank_path = tmp_path / "bank.json"
    report_path = tmp_path / "report.json"
    bank_path.write_text(json.dumps(bank))
    report_path.write_text(json.dumps(_report()))
    try:
        build_site(bank_path, report_path, tmp_path / "site")
    except RuntimeError as exc:
        assert "DRAFT" in str(exc)
    else:
        raise AssertionError("build_site accepted a bank that is not DRAFT")


def test_image_recipe_leaves_the_database_and_the_flag_off():
    docker = (ROOT / "Dockerfile").read_text()
    assert "COPY data" not in docker
    assert "COPY questions" not in docker
    assert "LIVE_ASK=0" in docker
    fly = (ROOT / "fly.toml").read_text()
    assert "LIVE_ASK=" not in fly
    assert "min_machines_running = 0" in fly


def test_pages_workflow_deploys_the_committed_site_on_main():
    text = (ROOT / ".github" / "workflows" / "pages.yml").read_text()
    assert "push:" in text
    assert "branches: [main]" in text
    assert "pull_request:" not in text
    assert "actions/upload-pages-artifact@v3" in text
    assert "path: site" in text
    assert "actions/deploy-pages@v4" in text
    assert "pages: write" in text
    assert "id-token: write" in text
    assert "concurrency:" in text
    assert "group: pages" in text
    assert "cancel-in-progress: false" in text
    # The committed site is the artifact. CI must not rebuild it.
    assert "build-site" not in text
    assert "pip install" not in text


def test_gitignore_blocks_data_and_secrets_and_keeps_the_site():
    import subprocess

    def ignored(rel: str) -> bool:
        result = subprocess.run(["git", "check-ignore", "-q", rel], cwd=ROOT, check=False)
        return result.returncode == 0

    assert ignored("data/flights.duckdb")
    assert ignored("extract.parquet")
    assert ignored("download.csv")
    assert ignored(".env")
    assert ignored("secrets/openai.key")
    assert ignored("key.pem")
    assert not ignored(".env.example")
    assert not ignored("site/index.html")
    assert not ignored("site/data/explore.json")


def test_only_the_sample_fixture_db_is_tracked():
    import subprocess
    root = Path(__file__).resolve().parents[1]
    def ign(p):
        return subprocess.run(["git", "check-ignore", "-q", p], cwd=root).returncode == 0
    assert ign("data/flights.duckdb")
    assert ign("other.duckdb")
    assert not ign("tests/fixtures/sample_flights.duckdb")
