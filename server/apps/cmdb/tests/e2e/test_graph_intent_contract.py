"""Task 7：真实 MetricsCannula/Management 到 FalkorDB transport 的意图合同。"""

from __future__ import annotations

import copy
import json
from collections import Counter, defaultdict
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import openpyxl
import pytest

from apps.cmdb.collection.collect_plugin.base import CollectBase
from apps.cmdb.collection.common import Management
from apps.cmdb.collection.metrics_cannula import MetricsCannula
from apps.cmdb.collection.plugins import get_collection_plugin
from apps.cmdb.constants.constants import DataCleanupStrategy
from apps.cmdb.tests.e2e.contract_loader import audit_lane_a_evidence
from apps.cmdb.tests.e2e.contract_manifest import load_manifest
from apps.cmdb.tests.e2e.graph_intent_spy import GraphIntentSpy
from apps.cmdb.tests.e2e.lane_b_loader import (
    load_lane_b_evidence,
    lane_b_entries,
    lane_b_incomplete,
)


MODEL_CONFIG = Path(__file__).parents[2] / "support-files" / "model_config.xlsx"
LANE_B_READY_ENTRIES = tuple(entry for entry in lane_b_entries() if not load_lane_b_evidence(entry.case_id).missing_files)


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _graph_groups():
    groups = defaultdict(list)
    for entry in LANE_B_READY_ENTRIES:
        vm_response = load_lane_b_evidence(entry.case_id).read_json("04_vm_response.json")
        groups[_canonical(vm_response)].append(entry)
    return tuple(tuple(entries) for entries in groups.values())


GRAPH_GROUPS = _graph_groups()
NETWORK_GROUP = next(group for group in GRAPH_GROUPS if group[0].case_id == "network")
NON_NETWORK_GRAPH_GROUPS = tuple(group for group in GRAPH_GROUPS if group is not NETWORK_GROUP)


@lru_cache(maxsize=None)
def _model_attrs(model_id: str) -> list[dict]:
    workbook = openpyxl.load_workbook(MODEL_CONFIG, read_only=True, data_only=True)
    try:
        sheet = workbook[f"attr-{model_id}"]
        rows = sheet.iter_rows(values_only=True)
        next(rows)
        headers = tuple(next(rows))
        attrs = []
        for row in rows:
            if not any(value not in (None, "") for value in row):
                continue
            item = {header: row[index] if index < len(row) else None for index, header in enumerate(headers) if header}
            if not item.get("attr_id"):
                continue
            option = item.get("option")
            if isinstance(option, str) and option.strip():
                try:
                    item["option"] = json.loads(option)
                except json.JSONDecodeError:
                    pass
            attrs.append(item)
        return attrs
    finally:
        workbook.close()


def _fake_task(entry, vm_response):
    first_metric = vm_response["data"]["result"][0]["metric"]
    source_inst_name = first_metric.get("inst_name")
    instances = [
        {
            "_id": 1,
            "model_id": entry.supported_model_id,
            "inst_name": source_inst_name or f"{entry.supported_model_id}-contract",
            "ip_addr": "192.0.2.10",
            "cloud": "contract-cloud",
            "cloud_name": "contract-cloud",
            "organization": ["contract-org"],
        }
    ]
    if entry.case_id == "ip":
        instances = {
            "subnet_ids": ["42"],
            "scan_method": "icmp",
        }
    return SimpleNamespace(
        id=33011,
        model_id=entry.supported_model_id,
        instances=instances,
        params={},
        is_network_topo=False,
        topology_contract={},
        data_cleanup_strategy=DataCleanupStrategy.NO_CLEANUP,
        is_host=False,
        input_method=None,
        team=None,
        is_k8s=False,
    )


def _deduplicate(rows: list[dict]) -> list[dict]:
    by_name = {}
    for row in rows:
        by_name[row["inst_name"]] = row
    return list(by_name.values())


def _expected_contract(group, task_id):
    expected_instance_map = {}
    expected_edges = []
    write_modes = set()
    for entry in group:
        expected = load_lane_b_evidence(entry.case_id).read_json("05_expected_cmdb.json")
        write_modes.add(expected.get("write_mode", "graph_entity"))
        model_id = expected["model_id"]
        for row in _deduplicate(expected.get("expected_instances", [])):
            graph_instance = {
                **{key: value for key, value in row.items() if key != "assos"},
                "model_id": model_id,
                "organization": ["contract-org"],
                "collect_task": str(task_id),
                "auto_collect": True,
            }
            key = (model_id, row["inst_name"])
            previous = expected_instance_map.setdefault(key, graph_instance)
            assert previous == graph_instance, f"{key}: 多个静态 Golden 对同一图库实例定义不一致"
            expected_edges.extend(
                {
                    "src_model_id": model_id,
                    "src_inst_name": row["inst_name"],
                    "dst_model_id": association["model_id"],
                    "dst_inst_name": association["inst_name"],
                    "asst_id": association["asst_id"],
                    "model_asst_id": association["model_asst_id"],
                }
                for association in row.get("assos", [])
            )
    return write_modes, list(expected_instance_map.values()), expected_edges


def _prepare_group(group, monkeypatch):
    entry = group[0]
    evidence = load_lane_b_evidence(entry.case_id)
    vm_response = evidence.read_json("04_vm_response.json")
    task = _fake_task(entry, vm_response)
    instance_id = str(vm_response["data"]["result"][0]["metric"].get("instance_id", ""))
    task_id = instance_id.split("_", 1)[1]

    monkeypatch.setattr(CollectBase, "get_collect_inst", lambda self: task)
    plugin_cls = get_collection_plugin(entry.task_type, entry.supported_model_id)
    task_instance = (
        task.instances[0]
        if isinstance(task.instances, list)
        else {"_id": 1, "inst_name": f"{entry.supported_model_id}-contract", "organization": ["contract-org"],}
    )
    plugin = plugin_cls(inst_name=task_instance["inst_name"], inst_id=task_instance["_id"], task_id=task_id,)
    evidence_timestamp = max(row["value"][0] for row in vm_response["data"]["result"])

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(evidence_timestamp + 3600, tz=tz)

    monkeypatch.setattr("apps.cmdb.collection.collect_util.datetime", FrozenDateTime)
    with mock.patch(
        "apps.cmdb.collection.query_vm.Collection.query", return_value=vm_response,
    ):
        plugin.run()
    metrics = {key: value for key, value in json.loads(json.dumps(plugin.result)).items() if key != "__task_format_data__"}
    return SimpleNamespace(
        entry=entry,
        task=task,
        task_instance=task_instance,
        task_id=task_id,
        plugin_cls=plugin_cls,
        metrics=metrics,
        expected=_expected_contract(group, task_id),
    )


def _collect_once(prepared, spy, *, metrics=None, cleanup=None):
    enterprise_extension = mock.Mock()
    with mock.patch("apps.cmdb.collection.common.write_collect_instance_change_records") as change_records:
        with mock.patch(
            "apps.cmdb.collection.common.get_collect_enterprise_extension", return_value=enterprise_extension,
        ):
            with mock.patch("apps.cmdb.services.auto_relation_reconcile." "schedule_instance_auto_relation_reconcile") as reconcile_instances:
                with mock.patch("apps.cmdb.services.auto_relation_reconcile." "schedule_incoming_rule_full_sync_by_model_ids") as reconcile_models:
                    result = MetricsCannula(
                        inst_id=prepared.task_instance["_id"],
                        organization=prepared.task_instance["organization"],
                        inst_name=prepared.task_instance["inst_name"],
                        task_id=int(prepared.task_id),
                        collect_plugin=prepared.plugin_cls,
                        default_metrics=copy.deepcopy(prepared.metrics if metrics is None else metrics),
                        filter_collect_task=True,
                        data_cleanup_strategy=cleanup or DataCleanupStrategy.NO_CLEANUP,
                        plugin_kwargs={},
                    ).collect_controller()
    return (
        result,
        change_records,
        reconcile_instances,
        reconcile_models,
        enterprise_extension.on_collect_instances_applied,
    )


def _assert_no_nested_failures(result):
    for model_id, model_result in result.items():
        if model_id in {"__raw_data__", "all"}:
            continue
        for operation in ("add", "update", "delete"):
            operation_result = model_result[operation]
            assert operation_result["failed"] == [], model_result
            for success in operation_result["success"]:
                assos_result = success.get("assos_result") or {}
                assert assos_result.get("failed", []) == [], success


def test_图库意图分组锁死79个生产三元组且不含K8s():
    manifest = load_manifest()
    expected_contracts = {entry.contract_id for entry in manifest.validation_entries}
    ready_contracts = {item.contract_id for item in audit_lane_a_evidence(manifest).validation if item.status == "ready"}
    grouped_contracts = {entry.contract_id for group in GRAPH_GROUPS for entry in group}

    assert len(expected_contracts) == 79
    assert len(grouped_contracts) == 79
    assert grouped_contracts == expected_contracts == ready_contracts
    assert lane_b_incomplete() == {}
    assert {item.case_id for item in manifest.production_exemptions} == {"k8s_cluster"}
    assert "k8s_cluster" not in {entry.case_id for group in GRAPH_GROUPS for entry in group}


@pytest.mark.parametrize(
    "group", NON_NETWORK_GRAPH_GROUPS, ids=lambda group: "+".join(entry.case_id for entry in group),
)
def test_静态Golden经真实业务与Falkor驱动产生精确且幂等的图库意图(group, monkeypatch):
    _assert_graph_group(group, monkeypatch)


@pytest.mark.django_db
def test_Network经真实ORM和Falkor驱动产生精确且幂等的图库意图(monkeypatch):
    _assert_graph_group(NETWORK_GROUP, monkeypatch)


def _assert_graph_group(group, monkeypatch):
    if group[0].case_id == "ip":
        calls = []

        def apply_discovery_result(subnet_id, alive):
            calls.append((subnet_id, alive))
            return {
                "created": 0,
                "updated": 0,
                "offline": 0,
                "failed": 0,
                "format_data": {"add": [], "update": [], "delete": [], "association": [], "all": 0,},
            }

        monkeypatch.setattr(
            "apps.cmdb.services.ipam_discovery.apply_discovery_result", apply_discovery_result,
        )
        prepared = _prepare_group(group, monkeypatch)
        evidence = load_lane_b_evidence("ip")
        expected_rows = evidence.read_json("05_expected_cmdb.json")["expected_ipam_rows"]
        vm_rows = [item["metric"] for item in evidence.read_json("04_vm_response.json")["data"]["result"]]
        assert vm_rows == expected_rows
        assert calls == [("42", [{"ip": "127.0.0.1", "mac": ""}])]
        assert prepared.metrics == {"ip": []}
        return

    prepared = _prepare_group(group, monkeypatch)
    write_modes, expected_instances, expected_edges = prepared.expected
    assert write_modes <= {
        "graph_entity",
        "embedded_host_field",
        "model_alias_entity",
        "dynamic_model_entity",
    }
    with GraphIntentSpy() as spy:
        graph_model_ids = (
            {item["model_id"] for item in expected_instances} | set(prepared.metrics) | {edge["dst_model_id"] for edge in expected_edges}
        )
        for model_id in graph_model_ids:
            spy.seed_model(model_id, _model_attrs(model_id))
        expected_instance_keys = {(item["model_id"], item["inst_name"]) for item in expected_instances}
        for edge in expected_edges:
            target_key = (edge["dst_model_id"], edge["dst_inst_name"])
            if target_key not in expected_instance_keys:
                spy.seed_instance(*target_key)

        first, *_ = _collect_once(prepared, spy)
        assert Counter(map(_canonical, spy.created_instances())) == Counter(map(_canonical, expected_instances))
        assert Counter(map(_canonical, spy.created_edges())) == Counter(map(_canonical, expected_edges))
        _assert_no_nested_failures(first)

        node_count = len(spy.transport.nodes)
        edge_count = len(spy.transport.edges)
        create_count = len(spy.transport.creates)
        edge_create_count = len(spy.transport.edge_creates)
        (second, change_records, reconcile_instances, reconcile_models, enterprise_hook,) = _collect_once(prepared, spy)

        assert len(spy.transport.nodes) == node_count
        assert len(spy.transport.edges) == edge_count
        assert len(spy.transport.creates) == create_count
        assert len(spy.transport.edge_creates) == edge_create_count
        _assert_no_nested_failures(second)
        if not expected_edges:
            change_records.assert_not_called()
            reconcile_instances.assert_not_called()
            reconcile_models.assert_not_called()
            enterprise_hook.assert_not_called()


def test_Management真实驱动覆盖唯一键更新和立即删除意图():
    attrs = _model_attrs("etcd")
    base = {
        "organization": ["contract-org"],
        "collect_task": "33011",
        "auto_collect": True,
    }
    with GraphIntentSpy() as spy:
        spy.seed_model("etcd", attrs)
        keep = spy.seed_instance("etcd", "etcd-keep", **base, ip_addr="192.0.2.10", port="2379", version="old",)
        stale = spy.seed_instance("etcd", "etcd-stale", **base, ip_addr="192.0.2.11", port="2379", version="stale",)
        management = Management(
            organization=["contract-org"],
            inst_name="host-contract",
            model_id="etcd",
            old_data=[keep, stale],
            new_data=[{"inst_name": "etcd-keep", "ip_addr": "192.0.2.10", "port": "2379", "version": "new",}],
            unique_keys=["inst_name"],
            collect_time="2026-07-28T00:00:00+00:00",
            task_id="33011",
            collect_plugin=SimpleNamespace(_MODEL_ID="etcd"),
            data_cleanup_strategy=DataCleanupStrategy.IMMEDIATELY,
        )
        with mock.patch("apps.cmdb.collection.common." "write_collect_instance_change_records"):
            with mock.patch("apps.cmdb.services.auto_relation_reconcile." "schedule_instance_auto_relation_reconcile"):
                with mock.patch("apps.cmdb.services.auto_relation_reconcile." "schedule_incoming_rule_full_sync_by_model_ids"):
                    result = management.controller()

    assert [item["inst_info"]["inst_name"] for item in result["update"]["success"]] == ["etcd-keep"]
    assert [item["inst_name"] for item in result["delete"]["success"]] == ["etcd-stale"]
    assert spy.transport.updates == [keep["_id"]]
    assert spy.transport.deletes == [stale["_id"]]
    _assert_no_nested_failures({"etcd": result})


def test_Management新增时由真实驱动拒绝重复唯一键():
    attrs = _model_attrs("etcd")
    with GraphIntentSpy() as spy:
        spy.seed_model("etcd", attrs)
        spy.seed_instance(
            "etcd", "etcd-duplicate", organization=["contract-org"], collect_task="other-task", auto_collect=True, ip_addr="192.0.2.11", port="2379",
        )
        management = Management(
            organization=["contract-org"],
            inst_name="host-contract",
            model_id="etcd",
            old_data=[],
            new_data=[{"inst_name": "etcd-duplicate", "ip_addr": "192.0.2.12", "port": "2379",}],
            unique_keys=["inst_name"],
            collect_time="2026-07-28T00:00:00+00:00",
            task_id="33011",
            collect_plugin=SimpleNamespace(_MODEL_ID="etcd"),
            data_cleanup_strategy=DataCleanupStrategy.NO_CLEANUP,
        )
        with mock.patch("apps.cmdb.collection.common." "write_collect_instance_change_records"):
            with mock.patch("apps.cmdb.services.auto_relation_reconcile." "schedule_instance_auto_relation_reconcile"):
                result = management.controller()

    assert result["add"]["success"] == []
    assert len(result["add"]["failed"]) == 1
    assert "exist" in str(result["add"]["failed"][0]["error"])
    assert spy.transport.creates == []
