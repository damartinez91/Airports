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
    assert resolver.canon("October 2025 Wednesdays") == "2025-10-wednesdays"
    assert resolver.canon("2025-10-wednesdays") == "2025-10-wednesdays"


def test_extra_major_carrier_is_allowed():
    question = _question(
        expected={"rows": [{"label": "MQ", "on_time_rate": 0.827}, {"label": "AA", "on_time_rate": 0.659}]},
    )
    result = _result(
        columns=["label", "on_time_rate", "operated_flights"],
        rows=[
            {"label": "MQ", "on_time_rate": 0.827, "operated_flights": 62560},
            {"label": "AA", "on_time_rate": 0.659, "operated_flights": 159366},
            {"label": "WN", "on_time_rate": 0.70, "operated_flights": 1000},
        ],
    )
    comparison = compare_result(question, result)
    assert comparison.passed, comparison.detail


def test_literal_answer_must_be_first():
    question = _question(
        expected={"rows": [{"label": "MQ", "on_time_rate": 0.827}, {"label": "AA", "on_time_rate": 0.659}]},
    )
    result = _result(
        columns=["airline_code", "on_time_rate"],
        rows=[
            {"label": "DFW", "airline_code": "AA", "on_time_rate": 0.659},
            {"label": "DFW", "airline_code": "MQ", "on_time_rate": 0.827},
        ],
    )
    comparison = compare_result(question, result)
    assert not comparison.passed
    assert comparison.reason == "order_mismatch"
    assert categorize(question, result, comparison) == "wrong_aggregation"


def test_contradictory_extra_value_fails():
    question = _question(expected={"rows": [{"label": "AA", "on_time_rate": 0.8}]})
    result = _result(
        columns=["label", "on_time_rate"],
        rows=[{"label": "AA", "on_time_rate": 0.8}, {"label": "AA", "on_time_rate": 0.2}],
    )
    comparison = compare_result(question, result)
    assert not comparison.passed
    assert categorize(question, result, comparison) == "wrong_aggregation"


def test_identical_duplicate_of_the_winner_is_allowed():
    question = _question(expected={"rows": [{"label": "OO", "on_time_rate": 0.852}]})
    result = _result(
        columns=["label", "on_time_rate"],
        rows=[{"label": "OO", "on_time_rate": 0.852}, {"label": "OO", "on_time_rate": 0.852}],
    )
    assert compare_result(question, result).passed


def test_late_rate_in_the_on_time_column_fails():
    question = _question(expected={"rows": [{"label": "SBN", "on_time_rate": 0.78867}]})
    result = _result(columns=["label", "on_time_rate"], rows=[{"label": "SBN", "on_time_rate": 0.2113}])
    comparison = compare_result(question, result)
    assert not comparison.passed
    assert "late-vs-on-time flip" in comparison.detail
    assert categorize(question, result, comparison) == "wrong_aggregation"


def test_missing_required_metric_is_not_aligned_to_a_flight_count():
    question = _question(
        answer_type="scalar",
        values=[
            {"name": "on_time_rate", "tolerance": 0.005, "unit": "fraction"},
            {"name": "avg_arrival_delay", "tolerance": 0.05, "unit": "minutes"},
        ],
        expected={"rows": [{"label": "AA", "on_time_rate": 0.7456, "avg_arrival_delay": 13.26}]},
    )
    result = _result(
        columns=["label", "on_time_rate", "operated_flights"],
        rows=[{"label": "AA", "on_time_rate": 0.7456, "operated_flights": 962421}],
    )
    comparison = compare_result(question, result)
    assert not comparison.passed
    assert "missing avg_arrival_delay" in comparison.detail


def test_bare_hour_label_matches_hour_key():
    question = _question(
        expected={"rows": [{"label": "hour_20", "on_time_rate": 0.677}]},
    )
    result = _result(columns=["label", "on_time_rate"], rows=[{"label": "20", "on_time_rate": 0.677}])
    comparison = compare_result(question, result)
    assert comparison.passed, comparison.detail


def test_bucket_column_supplies_the_label():
    question = _question(
        answer_type="scalar",
        expected={"rows": [{"label": "evening", "on_time_rate": 0.683}]},
    )
    result = _result(
        columns=["label", "on_time_rate", "dep_time_bucket"],
        rows=[{"label": "", "on_time_rate": 0.683, "dep_time_bucket": "evening"}],
    )
    comparison = compare_result(question, result)
    assert comparison.passed, comparison.detail


def test_unlabeled_scalar_passes_when_the_number_matches():
    question = _question(
        answer_type="scalar",
        values=[
            {"name": "weather_share", "tolerance": 0.005, "unit": "fraction"},
            {"name": "nas_share", "tolerance": 0.005, "unit": "fraction"},
            {"name": "weather_is_majority", "tolerance": 0, "unit": "flag"},
        ],
        expected={"rows": [{"label": "systemwide", "weather_share": 0.0611, "nas_share": 0.2069, "weather_is_majority": 0}]},
    )
    result = _result(
        columns=["label", "weather_share", "nas_share", "weather_is_majority"],
        rows=[{"label": "", "weather_share": 0.061, "nas_share": 0.207, "weather_is_majority": 0}],
    )
    comparison = compare_result(question, result)
    assert comparison.passed, comparison.detail


def test_wrong_scope_label_does_not_pass_on_the_number_alone():
    question = _question(expected={"rows": [{"label": "WN", "on_time_rate": 0.764}]})
    result = _result(
        columns=["label", "on_time_rate"],
        rows=[{"label": "systemwide", "on_time_rate": 0.764}],
    )
    comparison = compare_result(question, result)
    assert not comparison.passed


def test_readable_airport_size_labels():
    question = _question(
        answer_type="set",
        expected={
            "rows": [
                {"label": "150k_or_more", "on_time_rate": 0.761},
                {"label": "10k_to_49999", "on_time_rate": 0.804},
            ]
        },
    )
    result = _result(
        columns=["label", "on_time_rate"],
        rows=[
            {"label": "large (150k+)", "on_time_rate": 0.761},
            {"label": "medium (10k-49k)", "on_time_rate": 0.804},
        ],
    )
    comparison = compare_result(question, result)
    assert comparison.passed, comparison.detail


def test_spoken_labels_match_by_canon():
    from flight_analyst.compare import prose_rubric

    question = _question(
        answer_type="set",
        expected={"rows": [{"label": "friday_evening", "on_time_rate": 0.664}, {"label": "wednesday_morning", "on_time_rate": 0.898}]},
    )
    text = (
        "I read this as: the figures below.\n"
        "Friday evening: on_time_rate 66.4 percent\n"
        "Wednesday morning: on_time_rate 89.8 percent\n"
    )
    ok, detail, _nudge = prose_rubric(question, text)
    assert ok, detail
