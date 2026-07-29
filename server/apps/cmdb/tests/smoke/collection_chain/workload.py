"""七类代表对象的真实基础设施 smoke 工作负载。"""

from __future__ import annotations

import copy
import json
import os
import re
import socket
import subprocess
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import openpyxl

from apps.cmdb.constants.constants import INSTANCE, MODEL, DataCleanupStrategy

from .runner import SmokeConfigurationError, publish_nats_canary


_INSTANCE_ID = re.compile(r"(?<=,)instance_id=[^, ]+")
_SAFE_RUN_ID = re.compile(r"^cmdb-[a-z0-9]{8,32}$")
_FIXTURES = Path(__file__).parents[2] / "e2e" / "fixtures"
_MODEL_CONFIG = Path(__file__).parents[3] / "support-files" / "model_config.xlsx"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[6]
_STARGAZER_ROOT = _REPOSITORY_ROOT / "agents" / "stargazer"
_SOURCE_MODES = {
    "host": "real_environment_evidence",
    "mysql": "real_environment_evidence",
    "influxdb": "real_environment_evidence",
    "nginx": "real_environment_evidence",
    "qcloud_cvm": "official_sdk_boundary_mock",
    "vmware_vc": "private_api_boundary_mock",
    "network": "device_boundary_mock",
}


@dataclass(frozen=True)
class SmokeCase:
    case_id: str
    task_type: str
    supported_model_id: str
    emitted_model_id: str
    graph_model_id: str
    source_mode: str


SMOKE_CASES = (
    SmokeCase("host", "host", "host", "host", "host", "real_environment_evidence"),
    SmokeCase(
        "mysql",
        "protocol",
        "mysql",
        "mysql",
        "mysql",
        "real_environment_evidence",
    ),
    SmokeCase(
        "influxdb",
        "protocol",
        "influxdb",
        "influxdb",
        "influxdb",
        "real_environment_evidence",
    ),
    SmokeCase(
        "nginx",
        "middleware",
        "nginx",
        "nginx",
        "nginx",
        "real_environment_evidence",
    ),
    SmokeCase(
        "qcloud_cvm",
        "cloud",
        "qcloud",
        "qcloud_cvm",
        "qcloud_cvm",
        "official_sdk_boundary_mock",
    ),
    SmokeCase(
        "vmware_vc",
        "vm",
        "vmware_vc",
        "vmware_vc",
        "vmware_vc",
        "private_api_boundary_mock",
    ),
    SmokeCase(
        "network",
        "snmp",
        "network",
        "network",
        "switch",
        "device_boundary_mock",
    ),
)


def render_owned_line_protocol(
    source: str,
    *,
    run_id: str,
    task_id: int,
    timestamp_ns: int,
) -> str:
    """为冻结的真实转换制品注入本次运行身份和新鲜时间。"""
    if not _SAFE_RUN_ID.fullmatch(run_id):
        raise ValueError("run_id 不满足 smoke 所有权格式")
    if task_id <= 0 or timestamp_ns <= 0:
        raise ValueError("task_id 和 timestamp_ns 必须为正整数")

    rendered = []
    for source_line in source.splitlines():
        line = source_line.strip()
        if not line:
            continue
        try:
            series, fields, _ = line.rsplit(" ", 2)
        except ValueError as exc:
            raise ValueError("Line Protocol 必须包含 tag/field/timestamp") from exc
        series = _INSTANCE_ID.sub(f"instance_id=cmdb_{task_id}", series)
        if f"instance_id=cmdb_{task_id}" not in series:
            raise ValueError("Line Protocol 缺少 instance_id tag")
        series = f"{series},smoke_run_id={run_id}"
        rendered.append(f"{series} {fields} {timestamp_ns}")
    if not rendered:
        raise ValueError("Line Protocol 不能为空")
    return "\n".join(rendered) + "\n"


def load_case_line_protocol(case: SmokeCase) -> str:
    return (_FIXTURES / case.case_id / "03_line_protocol.txt").read_text(
        encoding="utf-8"
    )


def load_case_expected_associations(
    case: SmokeCase,
    *,
    task_instance_name: str,
) -> list[dict]:
    """从独立 Lane B Golden 读取最终关联，并替换本次父任务实例身份。"""
    expected = json.loads(
        (_FIXTURES / case.case_id / "05_expected_cmdb.json").read_text(
            encoding="utf-8"
        )
    )
    if expected.get("model_id") != case.graph_model_id:
        raise AssertionError(
            f"{case.case_id} Golden 模型漂移: "
            f"{expected.get('model_id')} != {case.graph_model_id}"
        )
    result = []
    for instance in expected.get("expected_instances", []):
        for association in instance.get("assos", []):
            dst_inst_name = association["inst_name"]
            if association["model_id"] == case.supported_model_id:
                dst_inst_name = task_instance_name
            result.append(
                {
                    "src_model_id": case.graph_model_id,
                    "src_inst_name": instance["inst_name"],
                    "dst_model_id": association["model_id"],
                    "dst_inst_name": dst_inst_name,
                    "asst_id": association["asst_id"],
                    "model_asst_id": association["model_asst_id"],
                }
            )
    return result


def verify_source_contracts(
    cases: tuple[SmokeCase, ...],
    *,
    artifact_dir: Path,
    timeout: float,
) -> None:
    """重跑 Task 5 来源合同，将真实/边界采集结果与冻结 LP 语义绑定。"""
    actual_modes = {case.case_id: case.source_mode for case in cases}
    if actual_modes != _SOURCE_MODES:
        raise AssertionError(
            f"七类来源模式漂移: expected={_SOURCE_MODES}, actual={actual_modes}"
        )
    python = _STARGAZER_ROOT / ".venv" / "bin" / "python"
    if not python.is_file():
        raise SmokeConfigurationError(
            "缺少 Stargazer 测试环境，请先在 agents/stargazer 执行 uv sync"
        )
    command = [
        str(python),
        "-m",
        "pytest",
        "-q",
        "-o",
        "addopts=",
        "tests/collection_contract",
    ]
    result = subprocess.run(
        command,
        cwd=_STARGAZER_ROOT,
        env={**os.environ, "TZ": "Asia/Shanghai"},
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    output = (result.stdout or "") + (result.stderr or "")
    (artifact_dir / "source-contract.log").write_text(
        output[-1_048_576:],
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise AssertionError(
            "Stargazer 来源合同未通过，详见 source-contract.log"
        )


def vm_metric_name(line_protocol: str) -> str:
    """返回 VictoriaMetrics 按生产 Influx 语义生成的首个指标名。"""
    line = next(
        (item.strip() for item in line_protocol.splitlines() if item.strip()),
        "",
    )
    try:
        series, fields, _ = line.rsplit(" ", 2)
        measurement = series.split(",", 1)[0]
        field_name = fields.split("=", 1)[0]
    except (ValueError, IndexError) as exc:
        raise ValueError("无法从 Line Protocol 解析 VM 指标名") from exc
    if not measurement or not field_name:
        raise ValueError("Line Protocol 的 measurement/field 不能为空")
    return f"{measurement}_{field_name}"


@lru_cache(maxsize=None)
def model_attrs(model_id: str) -> list[dict]:
    workbook = openpyxl.load_workbook(_MODEL_CONFIG, read_only=True, data_only=True)
    try:
        sheet = workbook[f"attr-{model_id}"]
        rows = sheet.iter_rows(values_only=True)
        next(rows)
        headers = tuple(next(rows))
        result = []
        for row in rows:
            if not any(value not in (None, "") for value in row):
                continue
            item = {
                header: row[index] if index < len(row) else None
                for index, header in enumerate(headers)
                if header
            }
            if item.get("attr_id"):
                option = item.get("option")
                if isinstance(option, str) and option.strip():
                    try:
                        item["option"] = json.loads(option)
                    except json.JSONDecodeError:
                        pass
                result.append(item)
        return result
    finally:
        workbook.close()


def smoke_task(case: SmokeCase, *, task_id: int, run_id: str) -> SimpleNamespace:
    inst_name = f"{case.supported_model_id}-{run_id}"
    instances = [
        {
            "_id": task_id,
            "model_id": case.supported_model_id,
            "inst_name": inst_name,
            "ip_addr": "192.0.2.10",
            "cloud": "contract-cloud",
            "cloud_name": "contract-cloud",
            "organization": [run_id],
        }
    ]
    return SimpleNamespace(
        id=task_id,
        model_id=case.supported_model_id,
        instances=instances,
        params={},
        is_network_topo=False,
        topology_contract={},
        data_cleanup_strategy=DataCleanupStrategy.NO_CLEANUP,
        is_host=case.task_type == "host",
        input_method=None,
        team=[run_id],
        is_k8s=False,
    )


def publish_line_protocol(
    nats_url: str,
    *,
    run_id: str,
    payload: str,
    timeout: float,
) -> None:
    parsed = urlparse(nats_url)
    if (
        parsed.scheme != "nats"
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or parsed.port is None
    ):
        raise SmokeConfigurationError("业务指标只允许发布到 smoke 回环 NATS")
    with socket.create_connection(
        (parsed.hostname, parsed.port),
        timeout=timeout,
    ) as connection:
        connection.settimeout(timeout)
        publish_nats_canary(
            connection,
            subject=f"metrics.{run_id}".encode(),
            payload=payload.encode(),
        )


def poll_until(
    probe,
    *,
    timeout: float,
    interval: float,
    message: str,
):
    deadline = time.monotonic() + timeout
    last_value = None
    while time.monotonic() < deadline:
        last_value = probe()
        if last_value:
            return last_value
        time.sleep(interval)
    raise TimeoutError(f"{message}; last={last_value!r}")


def run_plugin_until_metrics(plugin) -> dict | None:
    """运行生产插件，仅在至少一个最终模型真正有数据时结束轮询。"""
    plugin.run()
    metrics = {
        key: value
        for key, value in copy.deepcopy(plugin.result).items()
        if key != "__task_format_data__"
    }
    return metrics if any(metrics.values()) else None


class RealGraphOwnership:
    """真实 FalkorDB 的所有权标记、登记和精确清理。"""

    def __init__(self, run_id: str, ledger) -> None:
        self.run_id = run_id
        self.ledger = ledger

    @staticmethod
    def _client():
        from apps.cmdb.graph.falkordb import FalkorDBClient

        client = FalkorDBClient()
        if not client.connect():
            raise ConnectionError("无法连接 smoke FalkorDB")
        return client

    @staticmethod
    def _raw_graph():
        from apps.cmdb.graph.falkordb import FalkorDBConnectionPool

        return FalkorDBConnectionPool().get_connection()[1]

    def seed_model(self, model_id: str) -> int:
        client = self._client()
        existing, _ = client.query_entity(
            MODEL,
            [{"field": "model_id", "type": "str=", "value": model_id}],
        )
        if existing:
            entity_id = int(existing[0]["_id"])
        else:
            entity = client.create_entity(
                MODEL,
                {
                    "model_id": model_id,
                    "model_name": model_id,
                    "attrs": __import__("json").dumps(
                        model_attrs(model_id),
                        ensure_ascii=False,
                    ),
                    "unique_rules": "[]",
                    "smoke_run_id": self.run_id,
                },
                {},
                [],
            )
            entity_id = int(entity["_id"])
            self.ledger.record("graph_model", str(entity_id))
        return entity_id

    def seed_parent_instance(
        self,
        model_id: str,
        inst_name: str,
        *,
        task_id: int,
    ) -> int:
        client = self._client()
        entity = client.create_entity(
            INSTANCE,
            {
                "model_id": model_id,
                "inst_name": inst_name,
                "organization": [self.run_id],
                "collect_task": str(task_id),
                "auto_collect": True,
                "smoke_run_id": self.run_id,
            },
            {},
            [],
        )
        entity_id = int(entity["_id"])
        self.ledger.record("graph_entity", str(entity_id))
        return entity_id

    def tag_and_record_task_resources(self, task_id: int) -> list[dict]:
        client = self._client()
        rows, _ = client.query_entity(
            INSTANCE,
            [{"field": "collect_task", "type": "str=", "value": str(task_id)}],
        )
        graph = self._raw_graph()
        for row in rows:
            entity_id = int(row["_id"])
            graph.query(
                "MATCH (n:instance) WHERE ID(n) = $id "
                "SET n.smoke_run_id = $run_id RETURN n",
                {"id": entity_id, "run_id": self.run_id},
            )
            if not self.ledger.contains("graph_entity", str(entity_id)):
                self.ledger.record("graph_entity", str(entity_id))

        edge_result = graph.query(
            "MATCH (a:instance)-[e]->(b:instance) "
            "WHERE a.smoke_run_id = $run_id OR b.smoke_run_id = $run_id "
            "SET e.smoke_run_id = $run_id RETURN ID(e)",
            {"run_id": self.run_id},
        )
        edge_ids = [
            int(record[0])
            for record in getattr(edge_result, "result_set", [])
            if record
        ]
        recorded = self.ledger.identifiers("graph_edge")
        for edge_id in edge_ids:
            if str(edge_id) not in recorded:
                self.ledger.record("graph_edge", str(edge_id))
        return rows

    def task_associations(self, task_id: int) -> list[dict]:
        result = self._raw_graph().query(
            "MATCH (a:instance)-[e]->(b:instance) "
            "WHERE a.collect_task = $task_id "
            "RETURN a.model_id, a.inst_name, b.model_id, b.inst_name, "
            "e.asst_id, e.model_asst_id",
            {"task_id": str(task_id)},
        )
        return [
            {
                "src_model_id": record[0],
                "src_inst_name": record[1],
                "dst_model_id": record[2],
                "dst_inst_name": record[3],
                "asst_id": record[4],
                "model_asst_id": record[5],
            }
            for record in getattr(result, "result_set", [])
        ]

    def remove_owned(self, kind: str, identifier: str) -> None:
        client = self._client()
        entity_id = int(identifier)
        if kind == "graph_edge":
            result = self._raw_graph().query(
                "MATCH ()-[e]->() WHERE ID(e) = $id "
                "RETURN e.smoke_run_id",
                {"id": entity_id},
            )
            records = getattr(result, "result_set", [])
            if records and records[0][0] == self.run_id:
                client.delete_edge(entity_id)
            return
        if kind not in {"graph_entity", "graph_model"}:
            raise ValueError(f"未知 smoke 资源类型: {kind}")
        label = INSTANCE if kind == "graph_entity" else MODEL
        entity = client.query_entity_by_id(entity_id)
        if entity and entity.get("smoke_run_id") == self.run_id:
            client.detach_delete_entity(label, entity_id)
