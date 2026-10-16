from flight_analyst.analyst import compose_answer
from flight_analyst.compare import prose_rubric
from flight_analyst.definitions import GRADER_RUBRIC
from flight_analyst.questions import build_questions, sql_american_lateness


def test_daniel_review_counts_and_variant_sql():
    questions = build_questions()
    by_status = {}
    for item in questions:
        by_status[item["status"]] = by_status.get(item["status"], 0) + 1
    assert by_status["APPROVED"] == 34
    assert by_status.get("DRAFT-REVISED", 0) == 0
    assert by_status["DRAFT"] == len(questions) - 34
    lateness = [item for item in questions if item["sql"] == sql_american_lateness().strip()]
    assert len(lateness) == 3
    assert {item["question"] for item in lateness} == {
        "How late is American?",
        "Is American usually late?",
        "American's on-time record?",
    }
    dice = [item for item in questions if "dice of busier" in item["question"]]
    assert len(dice) == 1
    assert dice[0]["status"] == "DRAFT"
    assert "I read this as" in GRADER_RUBRIC


def test_prose_rubric_requires_the_reading_and_keeps_the_nudge_advisory():
    question = {
        "answer_type": "set",
        "ambiguous": True,
        "alternatives": "both directions",
        "expected": {"rows": [{"label": "DFW-ORD", "on_time_rate": 0.68}, {"label": "ORD-DFW", "on_time_rate": 0.70}]},
    }
    spoken = compose_answer(
        "Are flights between DFW and ORD on time?",
        [{"label": "DFW-ORD", "on_time_rate": 0.68}, {"label": "ORD-DFW", "on_time_rate": 0.70}],
        ["label", "on_time_rate"],
    )
    ok, detail, nudge = prose_rubric(question, spoken)
    assert ok, detail
    assert nudge
    thin = "DFW-ORD: 68\nORD-DFW: 70"
    ok, detail, nudge = prose_rubric(question, thin)
    assert not ok
    assert nudge is False
