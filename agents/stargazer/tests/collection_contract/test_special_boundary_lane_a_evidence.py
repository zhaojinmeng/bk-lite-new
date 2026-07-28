import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
import semantics
from conftest import REPOSITORY_ROOT
from plugins import base_utils, script_executor
from plugins.base_utils import convert_to_prometheus_format
from service.collection_service import CollectionService
from tasks.utils.nats_helper import convert_prometheus_to_influx


EVIDENCE_ROOT = (
    REPOSITORY_ROOT / "server" / "apps" / "cmdb" / "tests" / "e2e" / "fixtures"
)
SCHEMA_ROOT = (
    REPOSITORY_ROOT / "server" / "apps" / "cmdb" / "tests" / "e2e" / "schemas"
)
PHYSICAL_CASES = (
    "disk",
    "gpu",
    "memory",
    "nic",
    "host_physcial_server",
    "physcial_server",
)
SPECIAL_CASES = PHYSICAL_CASES + ("iis", "vmware_vc")
FIXED_TIMESTAMP_MS = 1_700_000_000_123

PHYSICAL_NORMAL_OUTPUT = """
=== system_info ===
inst_name=physical.example.invalid
serial_number=SN-CONTRACT-001
brand=ContractVendor
=== disk_info ===
disk_name=sda
disk_size=100
disk_type=SSD
=== mem_info ===
mem_locator=DIMM-A1
mem_size=16
=== NIC info ===
nic_pci_addr=0000:01:00.0
nic_name=eth0
mac=02:00:00:00:00:01
=== GPU info ===
gpu_name=GPU-0
gpu_memory=24
""".strip()


def _physical_collector():
    from plugins.inputs.physcial_server.physcial_server_info import PhyscialServerInfo

    return PhyscialServerInfo(
        {
            "node_id": "node-contract",
            "host": "192.0.2.80",
            "script_path": (
                "plugins/inputs/physcial_server/"
                "physcial_server_default_discover.sh"
            ),
            "model_id": "physcial_server",
        }
    )


async def _run_physical(monkeypatch, response):
    async def nats_boundary(subject, payload, timeout):
        assert subject == "ssh.execute.node-contract"
        request = json.loads(payload)
        assert request["args"][0]["connection_test"] is True
        assert "dmidecode" in request["args"][0]["command"]
        assert timeout > 0
        return response

    monkeypatch.setattr(script_executor, "nats_request", nats_boundary)
    return await _physical_collector().list_all_resources()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "response", "expected_success", "expected_counts"),
    (
        (
            "normal_non_empty",
            {"success": True, "result": PHYSICAL_NORMAL_OUTPUT},
            True,
            {"physcial_server": 1, "disk": 1, "memory": 1, "nic": 1, "gpu": 1},
        ),
        (
            "empty",
            {"success": True, "result": ""},
            True,
            {"physcial_server": 1},
        ),
        (
            "missing_optional_field",
            {
                "success": True,
                "result": PHYSICAL_NORMAL_OUTPUT.replace(
                    "\nbrand=ContractVendor", ""
                ),
            },
            True,
            {"physcial_server": 1, "disk": 1, "memory": 1, "nic": 1, "gpu": 1},
        ),
        (
            "multi_record",
            {
                "success": True,
                "result": PHYSICAL_NORMAL_OUTPUT.replace(
                    "disk_type=SSD",
                    "disk_type=SSD\n"
                    "disk_name=sdb\n"
                    "disk_size=200\n"
                    "disk_type=HDD",
                ),
            },
            True,
            {"physcial_server": 1, "disk": 2, "memory": 1, "nic": 1, "gpu": 1},
        ),
        (
            "authentication_or_protocol_error",
            {"success": False, "error": "SSH authentication rejected", "result": ""},
            False,
            None,
        ),
    ),
)
async def test_物理服务器只替换SSH命令边界并运行真实父采集器(
    scenario, response, expected_success, expected_counts, monkeypatch
):
    result = await _run_physical(monkeypatch, response)

    assert result["success"] is expected_success, scenario
    if expected_counts is None:
        assert "SSH authentication rejected" in result["result"]["cmdb_collect_error"]
    else:
        assert {
            model_id: len(records)
            for model_id, records in result["result"].items()
        } == expected_counts
        if scenario == "normal_non_empty":
            for case_id in PHYSICAL_CASES:
                provenance = json.loads(
                    (EVIDENCE_ROOT / case_id / "00_provenance.json").read_text(
                        encoding="utf-8"
                    )
                )
                expected = json.loads(
                    (EVIDENCE_ROOT / case_id / "01_source_raw.json").read_text(
                        encoding="utf-8"
                    )
                )
                source_model_id = provenance["source_model_id"]
                assert expected["result"][source_model_id] == result["result"][
                    source_model_id
                ]


def _iis_collector():
    return script_executor.SSHPlugin(
        {
            "node_id": "node-contract",
            "host": "192.0.2.81",
            "script_path": "plugins/inputs/iis/iis_default_discover.ps1",
            "model_id": "iis",
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "response", "expected_success", "expected_count"),
    (
        (
            "normal_non_empty",
            {
                "success": True,
                "result": json.dumps(
                    [
                        {
                            "inst_name": "iis.example.invalid",
                            "site_id": "1",
                            "state": "Started",
                            "physical_path": "C:\\inetpub\\wwwroot",
                        }
                    ]
                ),
            },
            True,
            1,
        ),
        ("empty", {"success": True, "result": "[]"}, True, 0),
        (
            "missing_optional_field",
            {
                "success": True,
                "result": json.dumps(
                    [{"inst_name": "iis.example.invalid", "site_id": "1"}]
                ),
            },
            True,
            1,
        ),
        (
            "multi_record",
            {
                "success": True,
                "result": json.dumps(
                    [
                        {"inst_name": "iis.example.invalid", "site_id": "1"},
                        {"inst_name": "Contract Site", "site_id": "2"},
                    ]
                ),
            },
            True,
            2,
        ),
        (
            "authentication_or_protocol_error",
            {"success": False, "error": "WinRM authentication rejected", "result": ""},
            False,
            None,
        ),
    ),
)
async def test_IIS只替换PowerShell执行边界并运行真实SSHPlugin(
    scenario, response, expected_success, expected_count, monkeypatch
):
    async def nats_boundary(subject, payload, timeout):
        request = json.loads(payload)
        assert subject == "ssh.execute.node-contract"
        assert request["args"][0]["connection_test"] is True
        assert "Get-IISProperties" in request["args"][0]["command"]
        return response

    monkeypatch.setattr(script_executor, "nats_request", nats_boundary)
    result = await _iis_collector().list_all_resources()

    assert result["success"] is expected_success, scenario
    if expected_count is None:
        assert "WinRM authentication rejected" in result["result"]["cmdb_collect_error"]
    else:
        assert len(result["result"].get("iis", [])) == expected_count
        if scenario == "normal_non_empty":
            expected = json.loads(
                (EVIDENCE_ROOT / "iis" / "01_source_raw.json").read_text(
                    encoding="utf-8"
                )
            )
            assert result == expected


class _VmwareViewManager:
    def __init__(self, virtual_machines=()):
        self.virtual_machines = list(virtual_machines)

    def CreateContainerView(self, root, object_types, recursive):
        object_name = object_types[0].__name__
        view = self.virtual_machines if object_name == "vim.VirtualMachine" else []
        return SimpleNamespace(view=view)


def _fake_vm(index, *, missing_optional=False):
    host = None
    summary = SimpleNamespace(
        runtime=SimpleNamespace(powerState="poweredOn", host=host),
        config=SimpleNamespace(
            numCpu=2,
            guestFullName="" if missing_optional else "Contract Linux",
            memorySizeMB=4096,
            annotation="",
        ),
        quickStats=SimpleNamespace(uptimeSeconds=120),
    )
    return SimpleNamespace(
        name=f"vm-contract-{index}",
        _moId=f"vm-{index}",
        config=SimpleNamespace(template=False, createDate=None),
        summary=summary,
        runtime=SimpleNamespace(connectionState="connected", bootTime=None),
        guest=SimpleNamespace(
            net=[],
            toolsVersion="",
            toolsStatus="",
            toolsRunningStatus="",
        ),
        datastore=[],
        availableField=[],
        value=[],
    )


def _vmware_content(*, version="8.0", virtual_machines=()):
    return SimpleNamespace(
        about=SimpleNamespace(name="vcenter.example.invalid", version=version),
        rootFolder=object(),
        viewManager=_VmwareViewManager(virtual_machines),
    )


@pytest.mark.parametrize(
    ("scenario", "content", "error", "expected_success", "expected_vm_count"),
    (
        ("normal_non_empty", _vmware_content(), None, True, 0),
        # 成功认证的 vCenter 必然产生自身 identity；“空”只适用于下属资源集合。
        ("empty_not_applicable_to_parent_identity", _vmware_content(), None, True, 0),
        (
            "missing_optional_field",
            _vmware_content(version="", virtual_machines=(_fake_vm(1, missing_optional=True),)),
            None,
            True,
            1,
        ),
        (
            "multi_record",
            _vmware_content(virtual_machines=(_fake_vm(1), _fake_vm(2))),
            None,
            True,
            2,
        ),
        (
            "authentication_or_protocol_error",
            None,
            RuntimeError("vCenter authentication rejected"),
            False,
            None,
        ),
    ),
)
def test_VMware只替换pyVmomi连接对象边界并运行真实VmwareManage(
    scenario, content, error, expected_success, expected_vm_count, monkeypatch
):
    from plugins.inputs.vmware_vc import vmware_info

    disconnects = []

    def smart_connect(**kwargs):
        assert kwargs["host"] == "vcenter.example.invalid"
        if error:
            raise error
        return SimpleNamespace(RetrieveContent=lambda: content)

    monkeypatch.setattr(vmware_info, "SmartConnect", smart_connect)
    monkeypatch.setattr(vmware_info, "Disconnect", disconnects.append)
    manager = vmware_info.VmwareManage(
        {
            "host": "vcenter.example.invalid",
            "port": 443,
            "username": "contract-user",
            "password": "contract-password",
        }
    )

    result = manager.list_all_resources()

    assert result["success"] is expected_success, scenario
    if expected_vm_count is None:
        assert "vCenter authentication rejected" in result["result"]["cmdb_collect_error"]
    else:
        assert len(result["result"]["vmware_vc"]) == 1
        assert len(result["result"]["vmware_vm"]) == expected_vm_count
        if scenario == "normal_non_empty":
            expected = json.loads(
                (EVIDENCE_ROOT / "vmware_vc" / "01_source_raw.json").read_text(
                    encoding="utf-8"
                )
            )
            assert result["result"]["vmware_vc"] == expected["result"]["vmware_vc"]
    if expected_success:
        assert len(disconnects) == 1


@pytest.mark.parametrize("case_id", SPECIAL_CASES)
def test_特殊环境边界Mock逐case运行生产转换并精确匹配静态Golden(
    case_id, monkeypatch
):
    evidence = EVIDENCE_ROOT / case_id
    provenance = json.loads(
        (evidence / "00_provenance.json").read_text(encoding="utf-8")
    )
    source = json.loads((evidence / "01_source_raw.json").read_text(encoding="utf-8"))
    schema = json.loads(
        (SCHEMA_ROOT / case_id / "source.schema.json").read_text(encoding="utf-8")
    )
    assert provenance["source_kind"] == "boundary_mock"
    assert provenance["emitted_case_id"] == case_id
    assert provenance["review_basis"] == {
        "normal_non_empty": True,
        "empty": True,
        "missing_optional_field": True,
        "multi_record_or_pagination": True,
        "authentication_or_protocol_error": True,
    } or provenance["review_basis"] == {
        "normal_non_empty": True,
        "empty": "not_applicable: authenticated vCenter always emits its own identity",
        "missing_optional_field": True,
        "multi_record_or_pagination": True,
        "authentication_or_protocol_error": True,
    }
    assert schema["properties"]["result"]["required"] == [
        provenance["source_model_id"]
    ]
    assert set(source["result"]) == {provenance["source_model_id"]}

    if case_id in PHYSICAL_CASES:
        model_id = "physcial_server"
        plugin_name = "physcial_server_info"
        host = "192.0.2.80"
    elif case_id == "iis":
        model_id = "iis"
        plugin_name = "iis_info"
        host = "192.0.2.81"
    else:
        model_id = "vmware_vc"
        plugin_name = "vmware_vc_info"
        host = None

    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)
    normalized = CollectionService(
        {"plugin_name": plugin_name, "model_id": model_id, "host": host}
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
            "monitor_type": model_id,
            "plugin_name": plugin_name,
            "model_id": model_id,
            "tags": {
                "agent_id": "agent-contract",
                "instance_id": f"cmdb-{model_id}",
                "instance_type": model_id,
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
    if case_id == "iis":
        # InfluxDB 的 tag 编码保留 Windows 路径，但本地语义解析器把反斜杠按
        # 通用转义符消费；本 case 只有一条已精确比较的记录，直接绑定其时间戳。
        sample = next(iter(actual_prometheus_semantics.elements()))
        record = next(iter(actual_line_protocol_semantics.elements()))
        assert record.measurement == sample.metric_name
        assert record.timestamp_ns == sample.timestamp_ms * 1_000_000
    else:
        semantics.assert_timestamp_propagation(
            actual_prometheus_semantics, actual_line_protocol_semantics
        )
    assert str(FIXED_TIMESTAMP_MS) in expected_prometheus
