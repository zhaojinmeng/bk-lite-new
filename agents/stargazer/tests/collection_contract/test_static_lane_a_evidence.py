import json
from copy import deepcopy

import pytest
import semantics
from conftest import FIXED_TIMESTAMP_MS, REPOSITORY_ROOT
from plugins import base_utils
from plugins.base_utils import convert_to_prometheus_format
from service.collection_service import CollectionService
from tasks.utils.nats_helper import convert_prometheus_to_influx

EVIDENCE_ROOT = (
    REPOSITORY_ROOT / "server" / "apps" / "cmdb" / "tests" / "e2e" / "fixtures"
)

STATIC_REAL_ENVIRONMENT_CASES = (
    "mongodb",
    "redis",
    "activemq",
    "apache",
    "consul",
    "haproxy",
    "kafka",
    "minio",
)


def _assert_timestamp_propagation_with_influx_tag_normalization(
    prometheus_semantics, line_protocol_semantics
):
    """InfluxDB Point 不编码空 tag 并裁剪尾随空格；其余身份必须传播。"""
    unmatched_records = list(line_protocol_semantics.elements())
    for sample in prometheus_semantics.elements():
        identity_labels = {
            key: value.rstrip() for key, value in sample.labels if value != ""
        }
        matches = [
            record
            for record in unmatched_records
            if record.measurement == sample.metric_name
            and all(
                dict(record.tags).get(key) == value
                for key, value in identity_labels.items()
            )
        ]
        assert len(matches) == 1
        match = matches[0]
        assert match.timestamp_ns == sample.timestamp_ms * 1_000_000
        unmatched_records.remove(match)
    assert unmatched_records == []


@pytest.mark.parametrize("case_id", STATIC_REAL_ENVIRONMENT_CASES)
def test_可审计非云来源经过生产转换匹配逐case静态Golden(case_id, monkeypatch):
    evidence = EVIDENCE_ROOT / case_id
    source = json.loads((evidence / "01_source_raw.json").read_text(encoding="utf-8"))
    provenance = json.loads(
        (evidence / "00_provenance.json").read_text(encoding="utf-8")
    )
    review_basis = provenance["review_basis"]
    assert review_basis["source_commit"]
    assert review_basis["scalar_labels"]
    assert review_basis["none_or_nested_exclusion"]
    assert review_basis["derived_labels"]
    assert review_basis["common_tags"]
    assert review_basis["timestamp"]

    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)
    normalized = CollectionService(
        {
            "plugin_name": f"{case_id}_info",
            "model_id": case_id,
            "host": f"{case_id}.example.invalid",
        }
    )._process_result(deepcopy(source))
    actual_prometheus = convert_to_prometheus_format(normalized)
    expected_prometheus = (evidence / "02_prometheus.txt").read_text(
        encoding="utf-8"
    )
    actual_prometheus_semantics = semantics.parse_prometheus(actual_prometheus)
    assert actual_prometheus_semantics == semantics.parse_prometheus(
        expected_prometheus
    )

    actual_line_protocol = convert_prometheus_to_influx(
        actual_prometheus,
        {
            "monitor_type": case_id,
            "plugin_name": f"{case_id}_info",
            "model_id": case_id,
            "tags": {
                "agent_id": "agent-contract",
                "instance_id": f"cmdb-{case_id}",
                "instance_type": case_id,
                "collect_type": "discovery",
                "config_type": "production-contract",
            },
        },
    )
    expected_line_protocol = (evidence / "03_line_protocol.txt").read_text(
        encoding="utf-8"
    )
    actual_line_protocol_semantics = semantics.parse_line_protocol(
        actual_line_protocol
    )
    assert actual_line_protocol_semantics == semantics.parse_line_protocol(
        expected_line_protocol
    )
    _assert_timestamp_propagation_with_influx_tag_normalization(
        actual_prometheus_semantics,
        actual_line_protocol_semantics,
    )


def test_mysql真实来源经过生产转换匹配静态LaneA_Golden(monkeypatch):
    evidence = EVIDENCE_ROOT / "mysql"
    source = json.loads((evidence / "01_source_raw.json").read_text(encoding="utf-8"))
    expected_prometheus = (evidence / "02_prometheus.txt").read_text(encoding="utf-8")
    expected_line_protocol = (evidence / "03_line_protocol.txt").read_text(
        encoding="utf-8"
    )
    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)

    normalized = CollectionService(
        {
            "plugin_name": "mysql_info",
            "model_id": "mysql",
            "host": "mysql.example.invalid",
        }
    )._process_result(deepcopy(source))
    actual_prometheus = convert_to_prometheus_format(normalized)
    assert semantics.parse_prometheus(actual_prometheus) == semantics.parse_prometheus(
        expected_prometheus
    )

    publish_params = {
        "monitor_type": "mysql",
        "plugin_name": "mysql_info",
        "model_id": "mysql",
        "tags": {
            "agent_id": "agent-contract",
            "instance_id": "cmdb-mysql",
            "instance_type": "mysql",
            "collect_type": "discovery",
            "config_type": "production-contract",
        },
    }
    actual_line_protocol = convert_prometheus_to_influx(
        actual_prometheus, publish_params
    )
    assert semantics.parse_line_protocol(
        actual_line_protocol
    ) == semantics.parse_line_protocol(expected_line_protocol)
    semantics.assert_timestamp_propagation(
        semantics.parse_prometheus(actual_prometheus),
        semantics.parse_line_protocol(actual_line_protocol),
    )
    assert str(FIXED_TIMESTAMP_MS) in expected_prometheus


@pytest.mark.parametrize(
    ("case_id", "instance_id"),
    (
        ("postgresql", "cmdb-postgresql"),
        ("protocol_postgresql", "cmdb-protocol-postgresql"),
    ),
)
def test_PostgreSQL共享真实父来源但逐三元组匹配独立静态Golden(case_id, instance_id, monkeypatch):
    evidence = EVIDENCE_ROOT / case_id
    source = json.loads((evidence / "01_source_raw.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)
    normalized = CollectionService(
        {
            "plugin_name": "postgresql_info",
            "model_id": "postgresql",
            "host": "postgresql.example.invalid",
        }
    )._process_result(deepcopy(source))
    actual_prometheus = convert_to_prometheus_format(normalized)
    expected_prometheus = (evidence / "02_prometheus.txt").read_text(encoding="utf-8")
    assert semantics.parse_prometheus(actual_prometheus) == semantics.parse_prometheus(
        expected_prometheus
    )

    actual_line_protocol = convert_prometheus_to_influx(
        actual_prometheus,
        {
            "monitor_type": "postgresql",
            "plugin_name": "postgresql_info",
            "model_id": "postgresql",
            "tags": {
                "agent_id": "agent-contract",
                "instance_id": instance_id,
                "instance_type": "postgresql",
                "collect_type": "discovery",
                "config_type": "production-contract",
            },
        },
    )
    expected_line_protocol = (evidence / "03_line_protocol.txt").read_text(
        encoding="utf-8"
    )
    assert semantics.parse_line_protocol(
        actual_line_protocol
    ) == semantics.parse_line_protocol(expected_line_protocol)
