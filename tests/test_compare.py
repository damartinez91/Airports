from types import SimpleNamespace

from flight_analyst.compare import EntityResolver, categorize, compare_result


def _result(**kwargs):
    base = dict(sql="SELECT 1", rows=[], columns=[], refused=False, sql_error=None, infrastructure_error=None)
    base.update(kwargs)
    return SimpleNamespace(**base)


def _question(**kwargs):
    base = dict(
        answer_type="ranking",
        key_columns=["label"],
        values=[{"name": "on_time_rate", "aliases": ["on_time_rate"], "tolerance": 0.005, "unit": "fraction"}],
        expected={"rows": [{"label": "AA", "on_time_rate": 0.8}]},
    )
    base.update(kwargs)
    return base


def test_ranking_passes_on_code_and_rate():
    question = _question(expected={"rows": [{"label": "WN", "on_time_rate": 0.81}, {"label": "AA", "on_time_rate": 0.79}]})
    result = _result(
        columns=["label", "on_time_rate"],
        rows=[{"label": "WN", "on_time_rate": 0.812}, {"label": "AA", "on_time_rate": 0.79}],
    )
    comparison = compare_result(question, result, EntityResolver())
    assert comparison.passed
    assert categorize(question, result, comparison) is None


def test_percent_scale_and_name_alias():
    question = _question()
    result = _result(columns=["airline_name", "on_time_rate"], rows=[{"airline_name": "American Airlines", "on_time_rate": 80.1}])
    resolver = EntityResolver.from_pairs([("AA", "American Airlines")])
    comparison = compare_result(question, result, resolver)
    assert comparison.passed


def test_set_ignores_order():
    question = _question(
        answer_type="set",
        expected={"rows": [{"label": "AA", "on_time_rate": 0.7}, {"label": "WN", "on_time_rate": 0.6}]},
    )
    result = _result(
        columns=["label", "on_time_rate"],
        rows=[{"label": "WN", "on_time_rate": 0.6}, {"label": "AA", "on_time_rate": 0.7}],
    )
    assert compare_result(question, result).passed


def test_order_mismatch_is_wrong_aggregation():
    question = _question(
        expected={"rows": [{"label": "AA", "on_time_rate": 0.8}, {"label": "WN", "on_time_rate": 0.7}]},
    )
    result = _result(
        columns=["label", "on_time_rate"],
        rows=[{"label": "WN", "on_time_rate": 0.7}, {"label": "AA", "on_time_rate": 0.8}],
    )
    comparison = compare_result(question, result)
    assert not comparison.passed
    assert categorize(question, result, comparison) == "wrong_aggregation"


def test_value_mismatch_is_wrong_aggregation():
    question = _question()
    result = _result(columns=["label", "on_time_rate"], rows=[{"label": "AA", "on_time_rate": 0.5}])
    comparison = compare_result(question, result)
    assert categorize(question, result, comparison) == "wrong_aggregation"


def test_wrong_entity_is_misread():
    question = _question()
    result = _result(columns=["label", "on_time_rate"], rows=[{"label": "DL", "on_time_rate": 0.8}])
    comparison = compare_result(question, result)
    assert categorize(question, result, comparison) == "misread_question"


def test_sql_error_is_wrong_sql():
    question = _question()
    result = _result(sql_error="parser error", rows=[])
    comparison = compare_result(question, result)
    assert categorize(question, result, comparison) == "wrong_sql"


def test_refusal_of_an_answerable_question():
    question = _question()
    result = _result(refused=True, sql=None, rows=[])
    comparison = compare_result(question, result)
    assert categorize(question, result, comparison) == "refused"


def test_correct_refusal():
    question = _question(answer_type="refusal", key_columns=[], values=[], expected={"rows": []})
    result = _result(refused=True, sql=None, rows=[])
    comparison = compare_result(question, result)
    assert comparison.passed
    assert categorize(question, result, comparison) is None


def test_answering_a_refusal_question_is_misread():
    question = _question(answer_type="refusal", key_columns=[], values=[], expected={"rows": []})
    result = _result(rows=[{"label": "AA", "on_time_rate": 0.8}], columns=["label", "on_time_rate"])
    comparison = compare_result(question, result)
    assert categorize(question, result, comparison) == "misread_question"


def test_infrastructure_error():
    question = _question()
    result = _result(infrastructure_error="database missing", sql=None, rows=[])
    comparison = compare_result(question, result)
    assert categorize(question, result, comparison) == "error"


def test_hour_and_route_labels():
    resolver = EntityResolver()
    assert resolver.canon("hour_7") == "hour_07"
    assert resolver.canon("DFW to ORD") == "DFW-ORD"
    assert resolver.canon("Monday") == "monday"
    assert resolver.canon("nas") == "NAS"
