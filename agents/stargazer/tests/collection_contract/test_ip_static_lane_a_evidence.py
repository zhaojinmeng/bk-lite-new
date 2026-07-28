import json
from copy import deepcopy

import semantics
from conftest import REPOSITORY_ROOT, contract_instance_id_for_case
from plugins import base_utils
from plugins.base_utils import convert_to_prometheus_format
from service.collection_service import CollectionService
from tasks.utils.nats_helper import convert_prometheus_to_influx


def test_IP真实Docker_TCP探活来源匹配静态LaneA_Golden(monkeypatch):
    evidence = (
        REPOSITORY_ROOT
        / "server"
        / "apps"
        / "cmdb"
        / "tests"
        / "e2e"
        / "fixtures"
        / "ip"
    )
    source = json.loads((evidence / "01_source_raw.json").read_text(encoding="utf-8"))
    capture = json.loads(
        (
            REPOSITORY_ROOT
            / "agents"
            / "stargazer"
            / "tests"
            / "fixtures"
            / "collect"
            / "ip.json"
        ).read_text(encoding="utf-8")
    )
    assert capture["raw_stdout"] == source
    assert capture["container_meta"]["port_mapping"] == "18081->8080/tcp"

    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)
    actual_prometheus = convert_to_prometheus_format(
        CollectionService(
            {"plugin_name": "ip_info", "model_id": "ip", "host": None}
        )._process_result(deepcopy(source))
    )
    assert semantics.parse_prometheus(
        actual_prometheus
    ) == semantics.parse_prometheus(
        (evidence / "02_prometheus.txt").read_text(encoding="utf-8")
    )

    actual_line_protocol = convert_prometheus_to_influx(
        actual_prometheus,
        {
            "monitor_type": "ip",
            "plugin_name": "ip_info",
            "model_id": "ip",
            "tags": {
                "agent_id": "agent-contract",
                "instance_id": contract_instance_id_for_case("ip"),
                "instance_type": "ip",
                "collect_type": "discovery",
                "config_type": "production-contract",
            },
        },
    )
    assert semantics.parse_line_protocol(
        actual_line_protocol
    ) == semantics.parse_line_protocol(
        (evidence / "03_line_protocol.txt").read_text(encoding="utf-8")
    )
