from decimal import Decimal

import conftest
import pytest
import semantics
from plugins import base_utils
from plugins.base_utils import convert_to_prometheus_format
from tasks.utils.nats_helper import convert_prometheus_to_influx


def test_binding清单覆盖全部可测试生产三元组():
    failures = conftest.lane_a_coverage_failures()
    assert not failures, "Lane A生产覆盖缺口:\n" + "\n".join(failures)
    assert (
        len(conftest.covered_lane_a_contracts())
        == len(conftest.validation_contracts())
        == 79
    )


def test_每个生产binding贯通标准化formatter与publish链路(production_adapter_binding, monkeypatch):
    binding = production_adapter_binding
    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)

    normalized_payload = binding.run_real_normalizer()
    assert set(normalized_payload) == set(binding.source_model_ids)

    prometheus_text = convert_to_prometheus_format(normalized_payload)
    prometheus_samples = list(semantics.parse_prometheus(prometheus_text).elements())
    assert len(prometheus_samples) == len(binding.emitted_model_ids)

    expected_host = None if binding.task_type == "cloud" else "192.0.2.100"
    for emitted_model_id, source_model_id in zip(
        binding.emitted_model_ids, binding.source_model_ids
    ):
        matching_samples = [
            sample
            for sample in prometheus_samples
            if sample.metric_name == f"{source_model_id}_info"
        ]
        assert (
            len(matching_samples) == 1
        ), f"{binding.case_id}/{emitted_model_id}: 原始模型{source_model_id}必须且只能发布一次"
        sample = matching_samples[0]
        labels = dict(sample.labels)
        expected_labels = {
            "bk_obj_id": source_model_id,
            "collect_status": "success",
            "contract_empty": "",
            "contract_false": "False",
            "contract_zero": "0",
            "inst_name": f"{source_model_id}-contract",
            "model_id": source_model_id,
            "resource_id": f"{source_model_id}-001",
        }
        if expected_host:
            expected_labels["host"] = expected_host
        assert labels == expected_labels
        assert "contract_none" not in labels
        assert "contract_nested" not in labels
        assert sample.numeric_value == Decimal("1")
        assert sample.field_name == "gauge"
        assert sample.timestamp_ms == 1_700_000_000_123

    line_protocol = convert_prometheus_to_influx(
        prometheus_text, binding.publish_params
    )
    line_records = list(semantics.parse_line_protocol(line_protocol).elements())
    assert len(line_records) == len(prometheus_samples)
    common_tags = binding.publish_params["tags"]
    for sample in prometheus_samples:
        matching_records = [
            record
            for record in line_records
            if record.measurement == sample.metric_name
        ]
        assert len(matching_records) == 1
        record = matching_records[0]
        # InfluxDB Line Protocol 不表达空 tag；合法的0/False字符串仍必须贯通。
        nonempty_labels = {
            key: value for key, value in dict(sample.labels).items() if value != ""
        }
        assert dict(record.tags) == {**nonempty_labels, **common_tags}
        assert "contract_empty" not in dict(record.tags)
        assert dict(record.tags)["contract_zero"] == "0"
        assert dict(record.tags)["contract_false"] == "False"
        assert dict(record.typed_fields) == {
            "gauge": semantics.TypedField("integer", 1)
        }
        assert record.timestamp_ns == sample.timestamp_ms * 1_000_000


def test_representative_sample_runs_real_prometheus_and_line_protocol_conversion(
    representative_lane_a_case, monkeypatch,
):
    case, evidence = representative_lane_a_case
    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)

    normalized_payload = case.run_real_adapter(evidence.source_raw)
    prometheus_text = convert_to_prometheus_format(normalized_payload)
    actual_prometheus = semantics.parse_prometheus(prometheus_text)

    assert sum(actual_prometheus.values()) == evidence.expected_record_count
    assert actual_prometheus == semantics.parse_prometheus(evidence.prometheus_text)
    assert all(
        dict(sample.labels)["collect_status"] == "success"
        for sample in actual_prometheus.elements()
    )

    line_protocol = convert_prometheus_to_influx(prometheus_text, case.publish_params)
    actual_lines = semantics.parse_line_protocol(line_protocol)

    assert sum(actual_lines.values()) == evidence.expected_record_count
    assert actual_lines == semantics.parse_line_protocol(evidence.line_protocol_text)
    semantics.assert_timestamp_propagation(actual_prometheus, actual_lines)


def test_prometheus_comparison_is_order_independent_but_preserves_duplicates():
    first = 'metric_total{zone="a",host="one"} 1 1700000000123'
    second = 'metric_total{host="two",zone="b"} 2 1700000000456'

    assert semantics.parse_prometheus(
        f"{first}\n{second}\n"
    ) == semantics.parse_prometheus(f"{second}\n{first}\n")
    assert semantics.parse_prometheus(
        f"{first}\n{first}\n"
    ) != semantics.parse_prometheus(f"{first}\n")


def test_line_protocol_comparison_is_order_independent_but_preserves_duplicates():
    first = "metric,zone=a,host=one gauge=1i 1700000000123000000"
    second = "metric,host=two,zone=b value=2.5 1700000000456000000"

    assert semantics.parse_line_protocol(
        [first, second]
    ) == semantics.parse_line_protocol([second, first])
    assert semantics.parse_line_protocol(
        [first, first]
    ) != semantics.parse_line_protocol([first])


def test_line_protocol_parser_preserves_every_supported_field_type():
    parsed = semantics.parse_line_protocol(
        r'metric text="a\"b\\c",enabled=true,count=42u,delta=-7i,ratio=1.25 1700000000123000000'
    )

    record = next(parsed.elements())
    assert dict(record.typed_fields) == {
        "text": semantics.TypedField("string", 'a"b\\c'),
        "enabled": semantics.TypedField("boolean", True),
        "count": semantics.TypedField("unsigned", 42),
        "delta": semantics.TypedField("integer", -7),
        "ratio": semantics.TypedField("float", Decimal("1.25")),
    }


def test_timestamp_propagation_is_bound_to_each_metric_identity():
    prometheus = semantics.parse_prometheus(
        'metric{host="one"} 1 1700000000123\n' 'metric{host="two"} 1 1700000000456\n'
    )
    swapped_line_protocol = semantics.parse_line_protocol(
        "metric,host=one gauge=1i 1700000000456000000\n"
        "metric,host=two gauge=1i 1700000000123000000\n"
    )

    with pytest.raises(AssertionError, match="timestamp propagation"):
        semantics.assert_timestamp_propagation(prometheus, swapped_line_protocol)


def test_timestamp_propagation_is_bound_to_corresponding_typed_field():
    prometheus = semantics.parse_prometheus(
        "# TYPE metric gauge\n"
        'metric{host="same"} 1 1700000000123\n'
        'metric{host="same"} 2 1700000000456\n'
    )
    swapped_line_protocol = semantics.parse_line_protocol(
        "metric,host=same gauge=2i 1700000000123000000\n"
        "metric,host=same gauge=1i 1700000000456000000\n"
    )

    with pytest.raises(AssertionError, match="timestamp propagation"):
        semantics.assert_timestamp_propagation(prometheus, swapped_line_protocol)


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
    with pytest.raises(semantics.SemanticParseError):
        semantics.parse_prometheus(invalid_text)


@pytest.mark.parametrize(
    "invalid_line",
    [
        "metric,host=one value=NaN 1700000000123000000",
        "metric,host=one value=+Inf 1700000000123000000",
        "metric,host=one value=1 yesterday",
        "metric,host=one value=1 1700000000123",
        "metric,host=one 1700000000123000000",
        'metric,host=one text="a""b" 1700000000123000000',
        'metric,host=one text="a\\q" 1700000000123000000',
        'metric,host=one text="a\\ 1700000000123000000',
    ],
)
def test_line_protocol_parser_rejects_non_finite_values_bad_timestamps_and_bad_lines(
    invalid_line,
):
    with pytest.raises(semantics.SemanticParseError):
        semantics.parse_line_protocol(invalid_line)
