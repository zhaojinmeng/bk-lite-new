import json
from copy import deepcopy

import pytest
import semantics
from conftest import REPOSITORY_ROOT
from plugins import base_utils
from plugins.base_utils import convert_to_prometheus_format
from service.collection_service import CollectionService
from tasks.utils.nats_helper import convert_prometheus_to_influx

EVIDENCE_ROOT = (
    REPOSITORY_ROOT / "server" / "apps" / "cmdb" / "tests" / "e2e" / "fixtures"
)


@pytest.mark.parametrize("case_id", ("host", "host_proc_usage"))
def test_Host父采集真实容器来源逐模型匹配静态LaneA_Golden(
    case_id, monkeypatch
):
    evidence = EVIDENCE_ROOT / case_id
    source = json.loads((evidence / "01_source_raw.json").read_text(encoding="utf-8"))
    provenance = json.loads(
        (evidence / "00_provenance.json").read_text(encoding="utf-8")
    )
    capture = json.loads(
        (REPOSITORY_ROOT / provenance["source_fixture"]).read_text(
            encoding="utf-8"
        )
    )
    assert capture["raw_stdout"]
    assert capture["container_meta"]["architecture"] == "arm64"

    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)
    normalized = CollectionService(
        {
            "plugin_name": "host_info",
            "model_id": "host",
            "host": "192.0.2.60",
        }
    )._process_result(deepcopy(source))
    actual_prometheus = convert_to_prometheus_format(normalized)
    expected_prometheus = (evidence / "02_prometheus.txt").read_text(
        encoding="utf-8"
    )
    assert semantics.parse_prometheus(
        actual_prometheus
    ) == semantics.parse_prometheus(expected_prometheus)

    actual_line_protocol = convert_prometheus_to_influx(
        actual_prometheus,
        {
            "monitor_type": "host",
            "plugin_name": "host_info",
            "model_id": "host",
            "tags": {
                "agent_id": "agent-contract",
                "instance_id": "cmdb-host",
                "instance_type": "host",
                "collect_type": "discovery",
                "config_type": "production-contract",
            },
        },
    )
    expected_line_protocol = (evidence / "03_line_protocol.txt").read_text(
        encoding="utf-8"
    )
    actual_semantics = semantics.parse_line_protocol(actual_line_protocol)
    assert actual_semantics == semantics.parse_line_protocol(
        expected_line_protocol
    )
    for sample in semantics.parse_prometheus(actual_prometheus).elements():
        matches = [
            record
            for record in actual_semantics.elements()
            if record.measurement == sample.metric_name
            and all(
                dict(record.tags).get(key) == value
                for key, value in sample.labels
                if value != ""
            )
        ]
        assert len(matches) == 1
        assert matches[0].timestamp_ns == sample.timestamp_ms * 1_000_000
