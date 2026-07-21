import pytest
from plugins import base_utils
from plugins.base_utils import convert_to_prometheus_format
from semantics import (
    SemanticParseError,
    assert_timestamp_propagation,
    parse_line_protocol,
    parse_prometheus,
)
from tasks.utils.nats_helper import convert_prometheus_to_influx


def test_representative_sample_runs_real_prometheus_and_line_protocol_conversion(
    representative_lane_a_case, monkeypatch,
):
    case, evidence = representative_lane_a_case
    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)

    normalized_payload = case.run_real_adapter(evidence.source_raw)
    prometheus_text = convert_to_prometheus_format(normalized_payload)
    actual_prometheus = parse_prometheus(prometheus_text)

    assert sum(actual_prometheus.values()) == evidence.expected_record_count
    assert actual_prometheus == parse_prometheus(evidence.prometheus_text)
    assert all(
        dict(sample.labels)["collect_status"] == "success"
        for sample in actual_prometheus.elements()
    )

    line_protocol = convert_prometheus_to_influx(prometheus_text, case.publish_params)
    actual_lines = parse_line_protocol(line_protocol)

    assert sum(actual_lines.values()) == evidence.expected_record_count
    assert actual_lines == parse_line_protocol(evidence.line_protocol_text)
    assert_timestamp_propagation(actual_prometheus, actual_lines)


def test_prometheus_comparison_is_order_independent_but_preserves_duplicates():
    first = 'metric_total{zone="a",host="one"} 1 1700000000123'
    second = 'metric_total{host="two",zone="b"} 2 1700000000456'

    assert parse_prometheus(f"{first}\n{second}\n") == parse_prometheus(
        f"{second}\n{first}\n"
    )
    assert parse_prometheus(f"{first}\n{first}\n") != parse_prometheus(f"{first}\n")


def test_line_protocol_comparison_is_order_independent_but_preserves_duplicates():
    first = "metric,zone=a,host=one gauge=1i 1700000000123000000"
    second = "metric,host=two,zone=b value=2.5 1700000000456000000"

    assert parse_line_protocol([first, second]) == parse_line_protocol([second, first])
    assert parse_line_protocol([first, first]) != parse_line_protocol([first])


def test_timestamp_propagation_is_bound_to_each_metric_identity():
    prometheus = parse_prometheus(
        'metric{host="one"} 1 1700000000123\n' 'metric{host="two"} 1 1700000000456\n'
    )
    swapped_line_protocol = parse_line_protocol(
        "metric,host=one gauge=1i 1700000000456000000\n"
        "metric,host=two gauge=1i 1700000000123000000\n"
    )

    with pytest.raises(AssertionError, match="timestamp propagation"):
        assert_timestamp_propagation(prometheus, swapped_line_protocol)


@pytest.mark.parametrize(
    "invalid_text",
    [
        'metric{host="one"} NaN 1700000000123',
        'metric{host="one"} +Inf 1700000000123',
        'metric{host="one"} 1 yesterday',
        'metric{host="one"} 1 1700000000',
        'metric{host="one" 1 1700000000123',
    ],
)
def test_prometheus_parser_rejects_non_finite_values_bad_timestamps_and_bad_lines(
    invalid_text,
):
    with pytest.raises(SemanticParseError):
        parse_prometheus(invalid_text)


@pytest.mark.parametrize(
    "invalid_line",
    [
        "metric,host=one value=NaN 1700000000123000000",
        "metric,host=one value=+Inf 1700000000123000000",
        "metric,host=one value=1 yesterday",
        "metric,host=one value=1 1700000000123",
        "metric,host=one 1700000000123000000",
    ],
)
def test_line_protocol_parser_rejects_non_finite_values_bad_timestamps_and_bad_lines(
    invalid_line,
):
    with pytest.raises(SemanticParseError):
        parse_line_protocol(invalid_line)
