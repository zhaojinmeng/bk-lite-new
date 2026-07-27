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
    "nginx",
    "rabbitmq",
    "squid",
    "tomcat",
    "zookeeper",
    "etcd",
    "memcached",
    "influxdb",
)
QCLOUD_CASES = (
    "qcloud_bucket",
    "qcloud_clb",
    "qcloud_cmq",
    "qcloud_cmq_topic",
    "qcloud_cvm",
    "qcloud_domain",
    "qcloud_eip",
    "qcloud_filesystem",
    "qcloud_mongodb",
    "qcloud_mysql",
    "qcloud_pgsql",
    "qcloud_plusar_cluster",
    "qcloud_redis",
    "qcloud_rocketmq",
)
ALIYUN_CASES = (
    "aliyun_bucket",
    "aliyun_clb",
    "aliyun_ecs",
    "aliyun_kafka_inst",
    "aliyun_mongodb",
    "aliyun_mysql",
    "aliyun_pgsql",
    "aliyun_redis",
)
HWCLOUD_CASES = (
    "hwcloud",
    "hwcloud_dcs",
    "hwcloud_ecs",
    "hwcloud_eip",
    "hwcloud_elb",
    "hwcloud_evs",
    "hwcloud_obs",
    "hwcloud_rds",
    "hwcloud_sg",
    "hwcloud_subnet",
    "hwcloud_vpc",
)


def _assert_timestamp_propagation_with_influx_tag_normalization(
    prometheus_semantics, line_protocol_semantics, overridden_labels=()
):
    """InfluxDB Point 不编码空 tag 并裁剪尾随空格；其余身份必须传播。"""
    unmatched_records = list(line_protocol_semantics.elements())
    for sample in prometheus_semantics.elements():
        identity_labels = {
            key: value.rstrip()
            for key, value in sample.labels
            if value != "" and key not in overridden_labels
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
        assert matches
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
    expected_prometheus = (evidence / "02_prometheus.txt").read_text(encoding="utf-8")
    actual_prometheus_semantics = semantics.parse_prometheus(actual_prometheus)
    if "record_count" in review_basis:
        assert sum(actual_prometheus_semantics.values()) == int(
            review_basis["record_count"]
        )
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
    actual_line_protocol_semantics = semantics.parse_line_protocol(actual_line_protocol)
    assert actual_line_protocol_semantics == semantics.parse_line_protocol(
        expected_line_protocol
    )
    _assert_timestamp_propagation_with_influx_tag_normalization(
        actual_prometheus_semantics, actual_line_protocol_semantics,
    )


@pytest.mark.parametrize("case_id", ALIYUN_CASES)
def test_阿里云逐emitted_case只比较自身模型并匹配静态Golden(case_id, monkeypatch):
    evidence = EVIDENCE_ROOT / case_id
    source = json.loads((evidence / "01_source_raw.json").read_text(encoding="utf-8"))
    provenance = json.loads(
        (evidence / "00_provenance.json").read_text(encoding="utf-8")
    )
    source_model_id = provenance["source_model_id"]
    assert provenance["emitted_case_id"] == case_id
    assert set(source["result"]) == {source_model_id}

    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)
    normalized = CollectionService(
        {"plugin_name": "aliyun_info", "model_id": "aliyun", "host": None,}
    )._process_result(deepcopy(source))
    actual_prometheus = convert_to_prometheus_format(normalized)
    expected_prometheus = (evidence / "02_prometheus.txt").read_text(encoding="utf-8")
    actual_prometheus_semantics = semantics.parse_prometheus(actual_prometheus)
    assert actual_prometheus_semantics == semantics.parse_prometheus(
        expected_prometheus
    )

    actual_line_protocol = convert_prometheus_to_influx(
        actual_prometheus,
        {
            "monitor_type": "aliyun_account",
            "plugin_name": "aliyun_info",
            "model_id": "aliyun_account",
            "tags": {
                "agent_id": "agent-contract",
                "instance_id": "cmdb-aliyun_account",
                "instance_type": "aliyun_account",
                "collect_type": "discovery",
                "config_type": "production-contract",
            },
        },
    )
    expected_line_protocol = (evidence / "03_line_protocol.txt").read_text(
        encoding="utf-8"
    )
    actual_line_protocol_semantics = semantics.parse_line_protocol(actual_line_protocol)
    assert actual_line_protocol_semantics == semantics.parse_line_protocol(
        expected_line_protocol
    )
    _assert_timestamp_propagation_with_influx_tag_normalization(
        actual_prometheus_semantics, actual_line_protocol_semantics,
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


@pytest.mark.parametrize("case_id", QCLOUD_CASES)
def test_腾讯云逐emitted_case只比较自身模型并匹配静态Golden(case_id, monkeypatch):
    evidence = EVIDENCE_ROOT / case_id
    source = json.loads((evidence / "01_source_raw.json").read_text(encoding="utf-8"))
    provenance = json.loads(
        (evidence / "00_provenance.json").read_text(encoding="utf-8")
    )
    source_model_id = provenance["source_model_id"]
    assert provenance["emitted_case_id"] == case_id
    assert set(source["result"]) == {source_model_id}

    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)
    normalized = CollectionService(
        {"plugin_name": "qcloud_info", "model_id": "qcloud", "host": None,}
    )._process_result(deepcopy(source))
    actual_prometheus = convert_to_prometheus_format(normalized)
    expected_prometheus = (evidence / "02_prometheus.txt").read_text(encoding="utf-8")
    actual_prometheus_semantics = semantics.parse_prometheus(actual_prometheus)
    assert actual_prometheus_semantics == semantics.parse_prometheus(
        expected_prometheus
    )

    actual_line_protocol = convert_prometheus_to_influx(
        actual_prometheus,
        {
            "monitor_type": "qcloud",
            "plugin_name": "qcloud_info",
            "model_id": "qcloud",
            "tags": {
                "agent_id": "agent-contract",
                "instance_id": "cmdb-qcloud",
                "instance_type": "qcloud",
                "collect_type": "discovery",
                "config_type": "production-contract",
            },
        },
    )
    expected_line_protocol = (evidence / "03_line_protocol.txt").read_text(
        encoding="utf-8"
    )
    actual_line_protocol_semantics = semantics.parse_line_protocol(actual_line_protocol)
    assert actual_line_protocol_semantics == semantics.parse_line_protocol(
        expected_line_protocol
    )
    _assert_timestamp_propagation_with_influx_tag_normalization(
        actual_prometheus_semantics, actual_line_protocol_semantics,
    )


@pytest.mark.parametrize("case_id", HWCLOUD_CASES)
def test_华为云逐emitted_case只比较自身模型并匹配静态Golden(case_id, monkeypatch):
    evidence = EVIDENCE_ROOT / case_id
    source = json.loads((evidence / "01_source_raw.json").read_text(encoding="utf-8"))
    provenance = json.loads(
        (evidence / "00_provenance.json").read_text(encoding="utf-8")
    )
    source_model_id = provenance["source_model_id"]
    assert provenance["emitted_case_id"] == case_id
    assert set(source["result"]) == {source_model_id}

    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)
    normalized = CollectionService(
        {"plugin_name": "hwcloud_info", "model_id": "hwcloud", "host": None,}
    )._process_result(deepcopy(source))
    actual_prometheus = convert_to_prometheus_format(normalized)
    expected_prometheus = (evidence / "02_prometheus.txt").read_text(encoding="utf-8")
    actual_prometheus_semantics = semantics.parse_prometheus(actual_prometheus)
    assert actual_prometheus_semantics == semantics.parse_prometheus(
        expected_prometheus
    )

    actual_line_protocol = convert_prometheus_to_influx(
        actual_prometheus,
        {
            "monitor_type": "hwcloud",
            "plugin_name": "hwcloud_info",
            "model_id": "hwcloud",
            "tags": {
                "agent_id": "agent-contract",
                "instance_id": "cmdb-hwcloud",
                "instance_type": "hwcloud",
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
        overridden_labels=("instance_type",) if case_id == "hwcloud_ecs" else (),
    )
