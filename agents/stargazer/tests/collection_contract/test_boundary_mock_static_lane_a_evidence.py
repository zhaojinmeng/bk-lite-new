import json
from copy import deepcopy
from pathlib import Path

import pytest
import semantics
from conftest import PRODUCTION_ADAPTER_BINDINGS, REPOSITORY_ROOT
from plugins import base_utils
from plugins.base_utils import convert_to_prometheus_format
from service.collection_service import CollectionService
from tasks.utils.nats_helper import convert_prometheus_to_influx

SCENARIO_PATH = Path(__file__).with_name("boundary_mock_scenarios.json")
EVIDENCE_ROOT = (
    REPOSITORY_ROOT / "server" / "apps" / "cmdb" / "tests" / "e2e" / "fixtures"
)
SCENARIOS = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
CASES = tuple(SCENARIOS["cases"])


def _binding(case_id):
    return next(
        binding
        for binding in PRODUCTION_ADAPTER_BINDINGS
        if case_id in binding.emitted_model_ids
        or (
            case_id == "keepalived"
            and binding.supported_model_id == "keepalive"
        )
        or (
            case_id == "network"
            and binding.supported_model_id == "network"
        )
    )


def _service(binding):
    return CollectionService(
        {
            "plugin_name": f"{binding.adapter_dir}_info",
            "model_id": binding.supported_model_id,
            "host": None
            if binding.task_type == "snmp"
            else f"{binding.emitted_model_ids[0]}.example.invalid",
        }
    )


def test_Docker降级对象逐项声明实际失败和五态边界():
    assert set(SCENARIOS["scenario_names"]) == {
        "normal_non_empty",
        "empty",
        "missing_optional",
        "multi_record",
        "protocol_error",
    }
    assert set(CASES) == {
        "hbase",
        "keepalived",
        "openresty",
        "rocketmq",
        "spark",
        "mssql",
        "network",
        "oracle",
    }
    for case_id, declaration in SCENARIOS["cases"].items():
        assert declaration["docker_result"]
        assert declaration["mock_boundary"]
        assert declaration["parent_binding"] == _binding(case_id).case_id


@pytest.mark.parametrize("case_id", CASES)
def test_Docker降级对象真实formatter匹配独立静态Golden(case_id, monkeypatch):
    binding = _binding(case_id)
    evidence = EVIDENCE_ROOT / case_id
    provenance = json.loads(
        (evidence / "00_provenance.json").read_text(encoding="utf-8")
    )
    source = json.loads((evidence / "01_source_raw.json").read_text(encoding="utf-8"))
    assert provenance["source_kind"] == "boundary_mock"
    assert provenance["docker_attempt"]["result"]
    assert provenance["scenario_contract"] == SCENARIOS["scenario_names"]

    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)
    prometheus = convert_to_prometheus_format(
        _service(binding)._process_result(deepcopy(source))
    )
    assert semantics.parse_prometheus(prometheus) == semantics.parse_prometheus(
        (evidence / "02_prometheus.txt").read_text(encoding="utf-8")
    )
    line_protocol = convert_prometheus_to_influx(prometheus, binding.publish_params)
    assert semantics.parse_line_protocol(
        line_protocol
    ) == semantics.parse_line_protocol(
        (evidence / "03_line_protocol.txt").read_text(encoding="utf-8")
    )


@pytest.mark.parametrize("case_id", CASES)
@pytest.mark.parametrize(
    "scenario",
    ("empty", "missing_optional", "multi_record", "protocol_error"),
)
def test_Docker降级边界五态继续运行真实清洗与Prometheus转换(
    case_id, scenario, monkeypatch
):
    binding = _binding(case_id)
    evidence = EVIDENCE_ROOT / case_id
    source = json.loads((evidence / "01_source_raw.json").read_text(encoding="utf-8"))
    source_model_id = next(iter(source["result"]))
    record = source["result"][source_model_id][0]
    if scenario == "empty":
        replay = {"success": True, "result": {source_model_id: []}}
    elif scenario == "missing_optional":
        identity_key = "inst_name" if "inst_name" in record else next(iter(record))
        replay = {
            "success": True,
            "result": {source_model_id: [{identity_key: record[identity_key]}]},
        }
    elif scenario == "multi_record":
        second = deepcopy(record)
        if "inst_name" in second:
            second["inst_name"] = f"{case_id}-2.example.invalid"
        elif "sysname" in second:
            second["sysname"] = f"{case_id}-2.example.invalid"
        replay = {
            "success": True,
            "result": {source_model_id: [record, second]},
        }
    else:
        replay = {
            "success": False,
            "result": {"cmdb_collect_error": "protocol boundary error"},
        }

    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)
    normalized = _service(binding)._process_result(replay)
    prometheus = convert_to_prometheus_format(normalized)
    parsed = semantics.parse_prometheus(prometheus)
    if scenario == "empty":
        samples = list(parsed.elements())
        assert len(samples) == 1
        labels = dict(samples[0].labels)
        assert labels["collect_status"] == "success"
        assert set(labels) == {"bk_obj_id", "collect_status", "model_id"}
    elif scenario == "multi_record":
        assert len(list(parsed.elements())) == 2
    else:
        assert list(parsed.elements())
