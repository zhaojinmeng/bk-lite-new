import json
from copy import deepcopy

import pytest
import semantics
from conftest import REPOSITORY_ROOT, contract_instance_id_for_case
from plugins import base_utils
from plugins.base_utils import convert_to_prometheus_format
from service.collection_service import CollectionService
from tasks.utils.nats_helper import convert_prometheus_to_influx

EVIDENCE_ROOT = (
    REPOSITORY_ROOT / "server" / "apps" / "cmdb" / "tests" / "e2e" / "fixtures"
)
PRIVATE_CLOUD_CASES = (
    "fusioninsight_cluster",
    "fusioninsight_host",
    "storage",
    "storage_disk",
    "storage_pool",
    "storage_volume",
)


def _assert_timestamp_propagation(prometheus_semantics, line_protocol_semantics):
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


@pytest.mark.parametrize("case_id", PRIVATE_CLOUD_CASES)
def test_私有云HTTP边界逐emitted_case匹配静态LaneA_Golden(case_id, monkeypatch):
    evidence = EVIDENCE_ROOT / case_id
    source = json.loads((evidence / "01_source_raw.json").read_text(encoding="utf-8"))
    provenance = json.loads(
        (evidence / "00_provenance.json").read_text(encoding="utf-8")
    )
    source_model_id = provenance["source_model_id"]
    assert provenance["source_kind"] == "private_api_mock"
    assert provenance["emitted_case_id"] == case_id
    assert set(source["result"]) == {source_model_id}

    is_fusioninsight = case_id.startswith("fusioninsight_")
    parent_model_id = "fusioninsight" if is_fusioninsight else "storage"
    plugin_name = "fusioninsight_info" if is_fusioninsight else "oceanstor_info"
    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)
    normalized = CollectionService(
        {"plugin_name": plugin_name, "model_id": parent_model_id, "host": None}
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
            "monitor_type": parent_model_id,
            "plugin_name": plugin_name,
            "model_id": parent_model_id,
            "tags": {
                "agent_id": "agent-contract",
                "instance_id": contract_instance_id_for_case(case_id),
                "instance_type": parent_model_id,
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
    _assert_timestamp_propagation(
        actual_prometheus_semantics, actual_line_protocol_semantics
    )
