from __future__ import annotations

import subprocess

import pytest

from .workload import (
    SMOKE_CASES,
    load_case_expected_associations,
    model_attrs,
    render_owned_line_protocol,
    run_plugin_until_metrics,
    verify_source_contracts,
    vm_metric_name,
)


def test_七类代表对象与来源边界锁死() -> None:
    assert tuple(case.case_id for case in SMOKE_CASES) == (
        "host",
        "mysql",
        "influxdb",
        "nginx",
        "qcloud_cvm",
        "vmware_vc",
        "network",
    )
    assert {
        case.case_id: case.source_mode
        for case in SMOKE_CASES
    } == {
        "host": "real_environment_evidence",
        "mysql": "real_environment_evidence",
        "influxdb": "real_environment_evidence",
        "nginx": "real_environment_evidence",
        "qcloud_cvm": "official_sdk_boundary_mock",
        "vmware_vc": "private_api_boundary_mock",
        "network": "device_boundary_mock",
    }
    assert {
        case.case_id: (case.emitted_model_id, case.graph_model_id)
        for case in SMOKE_CASES
    } == {
        "host": ("host", "host"),
        "mysql": ("mysql", "mysql"),
        "influxdb": ("influxdb", "influxdb"),
        "nginx": ("nginx", "nginx"),
        "qcloud_cvm": ("qcloud_cvm", "qcloud_cvm"),
        "vmware_vc": ("vmware_vc", "vmware_vc"),
        "network": ("network", "switch"),
    }


def test_LineProtocol注入本次所有权并刷新任务身份与时间() -> None:
    rendered = render_owned_line_protocol(
        "mysql_info,instance_id=cmdb_1,model_id=mysql gauge=1i 1\n",
        run_id="cmdb-a1b2c3d4",
        task_id=987654,
        timestamp_ns=1_700_000_000_123_000_000,
    )

    assert rendered == (
        "mysql_info,instance_id=cmdb_987654,model_id=mysql,"
        "smoke_run_id=cmdb-a1b2c3d4 gauge=1i 1700000000123000000\n"
    )


def test_smoke从生产workbook读取真实模型字段() -> None:
    attrs = model_attrs("host")

    assert "inst_name" in {item["attr_id"] for item in attrs}
    inst_name = next(item for item in attrs if item["attr_id"] == "inst_name")
    assert isinstance(inst_name["option"], dict)


def test_VM指标名保留Influx字段后缀() -> None:
    assert (
        vm_metric_name(
            "host_info,instance_id=cmdb_1 gauge=1i 1700000000123000000\n"
        )
        == "host_info_gauge"
    )


def test_CMDB插件轮询必须等待至少一个模型真正返回数据() -> None:
    class EventuallyReadyPlugin:
        def __init__(self) -> None:
            self.calls = 0
            self.result = {"host": []}

        def run(self):
            self.calls += 1
            if self.calls == 2:
                self.result = {
                    "host": [{"inst_name": "192.0.2.60"}],
                    "__task_format_data__": {"ignored": True},
                }
            return self.result

    plugin = EventuallyReadyPlugin()

    assert run_plugin_until_metrics(plugin) is None
    assert run_plugin_until_metrics(plugin) == {
        "host": [{"inst_name": "192.0.2.60"}]
    }


def test_来源门禁执行Task5完整合同并保存有界证据(tmp_path, monkeypatch) -> None:
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "contracts passed", "")

    monkeypatch.setattr("apps.cmdb.tests.smoke.collection_chain.workload.subprocess.run", run)

    verify_source_contracts(SMOKE_CASES, artifact_dir=tmp_path, timeout=30)

    command, kwargs = calls[0]
    assert command[-1] == "tests/collection_contract"
    assert kwargs["timeout"] == 30
    assert kwargs["env"]["TZ"] == "Asia/Shanghai"
    assert (tmp_path / "source-contract.log").read_text() == "contracts passed"


def test_来源门禁失败不能继续使用冻结LP(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "apps.cmdb.tests.smoke.collection_chain.workload.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 1, "", "source failed"
        ),
    )

    with pytest.raises(AssertionError, match="来源合同未通过"):
        verify_source_contracts(SMOKE_CASES, artifact_dir=tmp_path, timeout=30)


def test_最终关联来自独立LaneB_Golden并替换本次父任务身份() -> None:
    qcloud = next(case for case in SMOKE_CASES if case.case_id == "qcloud_cvm")

    assert load_case_expected_associations(
        qcloud,
        task_instance_name="qcloud-cmdb-a1b2c3d4",
    ) == [
        {
            "src_model_id": "qcloud_cvm",
            "src_inst_name": "cvm-contract_ins-001",
            "dst_model_id": "qcloud",
            "dst_inst_name": "qcloud-cmdb-a1b2c3d4",
            "asst_id": "belong",
            "model_asst_id": "qcloud_cvm_belong_qcloud",
        }
    ]
