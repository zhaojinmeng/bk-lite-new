"""Task 9：七类代表对象真实 NATS→Telegraf→VM→CMDB→FalkorDB smoke。"""

from __future__ import annotations

from collections import Counter
import json
import os
import time
from unittest import mock
from urllib.parse import quote, urlparse
from urllib.request import urlopen

import pytest

from apps.cmdb.collection.collect_plugin.base import CollectBase
from apps.cmdb.collection.metrics_cannula import MetricsCannula
from apps.cmdb.collection.plugins import get_collection_plugin
from apps.cmdb.graph.falkordb import FalkorDBConnectionPool

from .runner import CollectionChainSmokeRunner, SmokeSettings
from .workload import (
    SMOKE_CASES,
    RealGraphOwnership,
    load_case_expected_associations,
    load_case_line_protocol,
    model_attrs,
    poll_until,
    publish_line_protocol,
    render_owned_line_protocol,
    run_plugin_until_metrics,
    smoke_task,
    verify_source_contracts,
    vm_metric_name,
)


pytestmark = [pytest.mark.real_smoke, pytest.mark.django_db(transaction=True)]


def _vm_has_owned_metric(
    vm_url: str,
    *,
    measurement: str,
    run_id: str,
    task_id: int,
    timeout: float,
) -> bool:
    query = quote(
        f'{measurement}{{instance_id="cmdb_{task_id}",'
        f'smoke_run_id="{run_id}"}}'
    )
    with urlopen(
        f"{vm_url}/api/v1/query?nocache=1&query={query}",
        timeout=timeout,
    ) as response:
        payload = json.loads(response.read(1_048_577))
    return payload.get("status") == "success" and bool(
        payload.get("data", {}).get("result")
    )


def _assert_no_nested_failures(result: dict) -> None:
    for model_result in result.values():
        if not isinstance(model_result, dict):
            continue
        for operation in ("add", "update", "delete"):
            operation_result = model_result.get(operation, {})
            assert operation_result.get("failed", []) == [], result
            for success in operation_result.get("success", []):
                assert (
                    success.get("assos_result", {}).get("failed", []) == []
                ), success


def _canonical(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _assert_graph_contract(
    case,
    metrics,
    graph_rows,
    graph_associations,
    *,
    task_instance_name,
) -> None:
    assert case.graph_model_id in metrics, case.case_id
    expected_instances = metrics[case.graph_model_id]
    assert expected_instances, case.case_id
    actual_instances = [
        row for row in graph_rows if row.get("model_id") == case.graph_model_id
    ]
    assert len(actual_instances) == len(expected_instances), case.case_id

    actual_by_name = {
        row["inst_name"]: row
        for row in actual_instances
    }
    assert len(actual_by_name) == len(actual_instances), case.case_id
    table_fields = {
        item["attr_id"]
        for item in model_attrs(case.graph_model_id)
        if item.get("attr_type") == "table"
    }
    for expected in expected_instances:
        actual = actual_by_name[expected["inst_name"]]
        expected_fields = {
            key: value for key, value in expected.items() if key != "assos"
        }
        for field in table_fields & expected_fields.keys():
            if isinstance(expected_fields[field], str):
                expected_fields[field] = json.loads(expected_fields[field])
        assert {
            key: actual.get(key) for key in expected_fields
        } == expected_fields, case.case_id

    expected_edges = load_case_expected_associations(
        case,
        task_instance_name=task_instance_name,
    )
    actual_edges = [
        edge
        for edge in graph_associations
        if edge["src_model_id"] == case.graph_model_id
    ]
    assert Counter(map(_canonical, actual_edges)) == Counter(
        map(_canonical, expected_edges)
    ), case.case_id


def test_七类代表对象穿过真实基础设施且精确清理(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SmokeSettings.from_env()
    ownership_holder: dict[str, RealGraphOwnership] = {}

    def remove_resource(kind: str, identifier: str) -> None:
        ownership_holder["ownership"].remove_owned(kind, identifier)

    runner = CollectionChainSmokeRunner(
        settings,
        resource_remover=remove_resource,
    )

    def workload(context):
        nats_url = runner.published_endpoint("nats", 4222, scheme="nats")
        vm_url = runner.published_endpoint(
            "victoriametrics",
            8428,
            scheme="http",
        )
        verify_source_contracts(
            SMOKE_CASES,
            artifact_dir=context.settings.artifact_dir,
            timeout=min(300, settings.workload_timeout / 2),
        )
        falkor_url = urlparse(
            runner.published_endpoint("falkordb", 6379, scheme="redis")
        )
        monkeypatch.setenv("FALKORDB_HOST", falkor_url.hostname or "")
        monkeypatch.setenv("FALKORDB_PORT", str(falkor_url.port))
        monkeypatch.setenv(
            "FALKORDB_DATABASE",
            f"cmdb_smoke_{context.settings.run_id[5:]}",
        )
        monkeypatch.setattr(
            "apps.cmdb.collection.query_vm.VICTORIAMETRICS_HOST",
            vm_url,
        )
        FalkorDBConnectionPool().invalidate()

        ownership = RealGraphOwnership(context.settings.run_id, context.ledger)
        ownership_holder["ownership"] = ownership
        for model_id in (
            "host",
            "mysql",
            "influxdb",
            "nginx",
            "qcloud",
            "qcloud_cvm",
            "vmware_vc",
            "switch",
        ):
            ownership.seed_model(model_id)

        observed = {}
        with (
            mock.patch(
                "apps.cmdb.collection.common.write_collect_instance_change_records"
            ),
            mock.patch(
                "apps.cmdb.collection.common.get_collect_enterprise_extension"
            ) as extension,
            mock.patch(
                "apps.cmdb.services.auto_relation_reconcile."
                "schedule_instance_auto_relation_reconcile"
            ),
            mock.patch(
                "apps.cmdb.services.auto_relation_reconcile."
                "schedule_incoming_rule_full_sync_by_model_ids"
            ),
        ):
            extension.return_value.on_collect_instances_applied.return_value = None
            for index, case in enumerate(SMOKE_CASES, start=1):
                task_id = 790_000 + index
                task = smoke_task(
                    case,
                    task_id=task_id,
                    run_id=context.settings.run_id,
                )
                task_instance = task.instances[0]
                if case.case_id == "qcloud_cvm":
                    ownership.seed_parent_instance(
                        "qcloud",
                        task_instance["inst_name"],
                        task_id=task_id,
                    )

                timestamp_ns = time.time_ns()
                source = load_case_line_protocol(case)
                rendered = render_owned_line_protocol(
                    source,
                    run_id=context.settings.run_id,
                    task_id=task_id,
                    timestamp_ns=timestamp_ns,
                )
                publish_line_protocol(
                    nats_url,
                    run_id=context.settings.run_id,
                    payload=rendered,
                    timeout=settings.command_timeout,
                )
                measurement = vm_metric_name(rendered)
                poll_until(
                    lambda: _vm_has_owned_metric(
                        vm_url,
                        measurement=measurement,
                        run_id=context.settings.run_id,
                        task_id=task_id,
                        timeout=settings.command_timeout,
                    ),
                    timeout=settings.canary_timeout,
                    interval=settings.poll_interval,
                    message=f"{case.case_id} 未到达 VictoriaMetrics",
                )

                plugin_cls = get_collection_plugin(
                    case.task_type,
                    case.supported_model_id,
                )
                with mock.patch.object(
                    CollectBase,
                    "get_collect_inst",
                    lambda _: task,
                ):
                    plugin = plugin_cls(
                        inst_name=task_instance["inst_name"],
                        inst_id=task_instance["_id"],
                        task_id=task_id,
                    )
                    metrics = poll_until(
                        lambda: run_plugin_until_metrics(plugin),
                        timeout=settings.canary_timeout,
                        interval=settings.poll_interval,
                        message=f"{case.case_id} CMDB 插件未读取到 VM 数据",
                    )

                # 真实 CMDB 图中注册了插件声明的全部产出模型；隔离图也必须
                # 按完整 result 键补齐，不能靠过滤空模型绕过 Management。
                for model_id in metrics:
                    ownership.seed_model(model_id)
                result = MetricsCannula(
                    inst_id=task_instance["_id"],
                    organization=task_instance["organization"],
                    inst_name=task_instance["inst_name"],
                    task_id=task_id,
                    collect_plugin=plugin_cls,
                    default_metrics=metrics,
                    filter_collect_task=True,
                    plugin_kwargs={},
                ).collect_controller()
                _assert_no_nested_failures(result)
                graph_rows = ownership.tag_and_record_task_resources(task_id)
                graph_associations = ownership.task_associations(task_id)
                _assert_graph_contract(
                    case,
                    metrics,
                    graph_rows,
                    graph_associations,
                    task_instance_name=task_instance["inst_name"],
                )
                observed[case.case_id] = {
                    "models": sorted(metrics),
                    "graph_models": sorted(
                        {row["model_id"] for row in graph_rows}
                    ),
                }

        assert tuple(observed) == tuple(case.case_id for case in SMOKE_CASES)
        assert observed["network"]["graph_models"] == ["switch"]
        return observed

    try:
        observed = runner.run(workload)
    finally:
        FalkorDBConnectionPool().close()
    assert len(observed) == 7
    assert runner.ledger.cleanup_errors == []
    assert runner.ledger.skipped == []
    assert os.getenv("FALKORDB_DATABASE", "").startswith("cmdb_smoke_")
