from flight_analyst.compare import EntityResolver
from flight_analyst.sitebuild import _format_value
from flight_analyst.llm import plan_question
from flight_analyst.questions import build_questions, sql_most_on_time_out_of
from flight_analyst.sql_guard import prepare_sql


def test_batch4_major_carriers_and_hawaiian_note():
    questions = {item["question"]: item for item in build_questions()}
    bos = questions["Which airline is most on time out of BOS?"]
    lax = questions["Which airline is most on time out of LAX?"]
    top = questions["Which five airlines have the highest systemwide on-time arrival rate?"]
    evening = questions["What are the five worst airports for evening delays?"]
    worst = questions["What are the five worst airports by on-time arrival rate?"]
    assert bos["status"] == "DRAFT-REVISED"
    assert lax["status"] == "DRAFT-REVISED"
    assert "SkyWest (flies for several airlines)" in lax["sql"]
    assert "0.80" in lax["sql"]
    assert "vol_rank <= 3" in lax["sql"]
    assert top["status"] == "DRAFT-REVISED"
    assert top["required_phrases"] == ["Hawaiian benefits from island weather."]
    assert evening["status"] == "DRAFT"
    assert worst["status"] == "DRAFT"
    assert len(build_questions()) == 120
    prepare_sql(sql_most_on_time_out_of("BOS"))
    stub = plan_question("Which airline is most on time out of LAX?")
    assert "SkyWest (flies for several airlines)" in stub["sql"]
    assert _format_value("operated_flights", 40364) == "40,364"
    assert _format_value("on_time_rate", 0.8516) == "85.2%"
    assert _format_value("percentage_points", 19.85) == "19.85"
    assert EntityResolver().canon("SkyWest (flies for several airlines)") == "OO"
    assert EntityResolver().canon("OO") == "OO"
