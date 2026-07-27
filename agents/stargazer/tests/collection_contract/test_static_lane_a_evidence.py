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
