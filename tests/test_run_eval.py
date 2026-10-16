import json
from pathlib import Path

from flight_analyst.evaluate import allocate_run_file, run_evaluation, run_label_for
from flight_analyst.ingest import create_database, load_lookups
from flight_analyst.llm import Completion
from flight_analyst.pricing import UnpricedModel, quote
from flight_analyst.run_eval import main


def test_haiku_list_price_and_unknown_model():
    assert quote("claude-haiku-4-5", 1_000_000, 2_000_000) == 11.0
    assert quote("stub", 500, 500) == 0.0
    try:
        quote("not-a-model", 1, 1)
    except UnpricedModel as exc:
        assert "prices.json" in str(exc)
    else:
        raise AssertionError("unlisted model should refuse")


def test_budget_stops_before_the_next_question(tmp_path: Path):
    db_path = tmp_path / "fixture.duckdb"
    con = create_database(db_path)
    airports = tmp_path / "airports.csv"
    airports.write_text(
        "iata_code,name,municipality,iso_region,iso_country,scheduled_service,type\n"
        "DFW,Dallas Fort Worth International Airport,Dallas,US-TX,US,yes,large_airport\n"
    )
    load_lookups(con, airports)
    con.close()

    class Costly:
        name = "anthropic"
        model = "claude-haiku-4-5"

        def __init__(self):
            self.calls = 0

        def complete(self, system, user):
            del system, user
            self.calls += 1
            # Two million input tokens at the Haiku 4.5 list price is $2.
            return Completion(
                '{"sql": null, "refuse": true, "refusal_reason": "budget test"}',
                input_tokens=2_000_000,
                output_tokens=0,
            )

    questions = []
    for index, status in enumerate(("APPROVED", "DRAFT", "DRAFT"), start=1):
        questions.append(
            {
                "id": f"q{index:03d}",
                "question": f"question {index}",
                "difficulty": "easy",
                "category": "airline_reliability",
                "answer_type": "refusal",
                "status": status,
                "sql": None,
                "values": [],
                "expected": {"behavior": "refuse", "rows": []},
            }
        )
    bank_path = tmp_path / "bank.json"
    bank_path.write_text(
        json.dumps(
            {
                "status": "DRAFT",
                "review_note": "candidate answers pending Daniel's review",
                "questions": questions,
                "data_window": {},
            }
        )
    )
    client = Costly()
    out = tmp_path / "run.json"
    report = run_evaluation(
        db_path=db_path,
        bank_path=bank_path,
        client=client,
        out_dir=tmp_path / "reports",
        budget_usd=3,
        model_out=out,
    )
    assert client.calls == 2
    assert report["summary"]["stopped_for_budget"] is True
    assert report["summary"]["n_unscored"] == 1
    assert report["summary"]["spent_usd"] == 4.0
    assert report["summary"]["approved_questions"]["n"] == 1
    assert report["summary"]["all_questions"]["n"] == 2
    saved = json.loads(out.read_text())
    assert saved["schema_version"] == "model-run-v1"
    assert saved["provider"] == "anthropic"
    assert saved["run_label"] == "claude-haiku-4-5"
    assert saved["results"][0]["grade"]["passed"] is True
    assert "input_tokens" in saved["results"][0]
    assert saved["price"]["source"].startswith("https://")


def test_each_run_gets_a_new_file(tmp_path: Path):
    first = allocate_run_file("claude-haiku-4-5", tmp_path)
    first.write_text("{}")
    second = allocate_run_file("claude-haiku-4-5", tmp_path)
    sonnet = allocate_run_file("claude-sonnet-4-5", tmp_path)
    assert first.name == "claude-haiku-4-5-r1.json"
    assert second.name == "claude-haiku-4-5-r2.json"
    assert sonnet.name == "claude-sonnet-4-5-r1.json"
    assert run_label_for("claude-haiku-4-5", second) == "claude-haiku-4-5 r2"
    assert run_label_for("claude-sonnet-4-5", sonnet) == "claude-sonnet-4-5 r1"
    assert run_label_for("stub", tmp_path / "stub.json") == "stub"


def test_run_eval_refuses_to_overwrite_an_existing_file(tmp_path: Path, capsys):
    dest = tmp_path / "claude-haiku-4-5-r1.json"
    dest.write_text("{}\n")
    code = main(["--provider", "stub", "--model", "stub", "--out", str(dest), "--budget-usd", "3"])
    assert code == 2
    assert "already exists" in capsys.readouterr().err
    assert dest.read_text() == "{}\n"


def test_run_eval_refuses_a_missing_key(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    code = main(["--provider", "anthropic", "--model", "claude-haiku-4-5", "--out", "results/ignored.json", "--budget-usd", "3"])
    assert code == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err
