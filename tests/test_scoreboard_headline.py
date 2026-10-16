from flight_analyst.scoreboard import render_scoreboard
from flight_analyst.sitebuild import _compare_page, is_baseline


def _run(label, provider, n_correct, model=None):
    return {"run_label": label, "provider": provider, "model": model or label.split(" ")[0],
            "summary": {"all": {"n": 120, "n_correct": n_correct, "accuracy": n_correct / 120},
                        "approved": {"n": 34, "n_correct": 28}, "cost_usd": 2.27, "cost_per_100_usd": 1.89},
            "results": []}


def test_stub_is_baseline_and_left_off_compare():
    stub = _run("stub", "stub", 116, model="stub")
    sonnet = _run("claude-sonnet-4-5 r1", "anthropic", 92)
    assert is_baseline(stub) and not is_baseline(sonnet)
    page = _compare_page([], [stub, sonnet], "note")
    assert "<td>stub</td>" not in page
    assert "claude-sonnet-4-5 r1" in page


def test_scoreboard_leads_with_headline_model():
    report = {"provider": "stub", "summary": {"accuracy": 0.967, "n_correct": 116, "n": 120}, "results": []}
    html = render_scoreboard(report, headline=_run("claude-sonnet-4-5 r1", "anthropic", 92),
                             low_cost=_run("claude-haiku-4-5 r3", "anthropic", 83))
    assert html.index("claude-sonnet-4-5 r1: 92 / 120 (77%)") < html.index("Rules baseline")
    assert "Low-cost tier" in html and "83 / 120" in html
