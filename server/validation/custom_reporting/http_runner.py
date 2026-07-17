import argparse
import ipaddress
import json
import os
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urlencode, urljoin, urlsplit

import requests

from validation.custom_reporting.ledger import ValidationLedger

CONNECT_TIMEOUT = 3.0
READ_TIMEOUT = 10.0
MAX_RESPONSE_BYTES = 1024 * 1024
TASK_SCAN_PAGE_SIZE = 200
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
SENSITIVE_KEY_PARTS = ("auth", "cookie", "password", "secret", "token")
TOKEN_REJECTION_SUBJECTS = ("token", "credential", "令牌", "凭据")
TOKEN_REJECTION_STATES = (
    "invalid",
    "revoked",
    "expired",
    "disabled",
    "无效",
    "作废",
    "吊销",
    "过期",
    "禁用",
)


class SafetyError(RuntimeError):
    pass


class HttpProtocolError(RuntimeError):
    pass


class CleanupIncompleteError(RuntimeError):
    pass


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class Transport(Protocol):
    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None,
        connect_timeout: float,
        read_timeout: float,
        max_response_bytes: int,
    ) -> HttpResponse:
        ...


class LedgerStateBackend(Protocol):
    def snapshot(
        self,
        *,
        ledger: ValidationLedger,
        org_id: int | None,
        expect_present: bool,
    ) -> dict[str, Any]:
        ...

    def cleanup(
        self,
        *,
        ledger: ValidationLedger,
        org_id: int | None,
    ) -> dict[str, Any]:
        ...


class DjangoFalkorLedgerStateBackend:
    EXPECTED_INSTANCE_SUFFIXES = frozenset({"immediate_source", "immediate_target", "pending_source", "backfill_target", "after_rotate"})

    @staticmethod
    def _owned_ids(ledger: ValidationLedger, kind: str) -> set[int]:
        return {_parse_owned_int(ledger.run_id, item.identifier) for item in ledger.resources if item.kind == kind}

    @staticmethod
    def _model_id(ledger: ValidationLedger) -> str:
        values = {item.identifier for item in ledger.resources if item.kind == "model"}
        if len(values) != 1 or not all(isinstance(item, str) for item in values):
            raise SafetyError("账本 model 身份不唯一")
        model_id = values.pop()
        if model_id != f"{ledger.run_id}_model".lower():
            raise SafetyError("账本 model 不属于当前 run_id")
        return model_id

    @staticmethod
    def _association_id(
        ledger: ValidationLedger,
        model_id: str,
        *,
        required: bool = True,
    ) -> str | None:
        prefix = f"{ledger.run_id}_association_intent_"
        values = {
            str(item.identifier)[len(prefix) :] if str(item.identifier).startswith(prefix) else str(item.identifier)
            for item in ledger.resources
            if item.kind == "association"
        }
        if not values and not required:
            return None
        if len(values) != 1:
            raise SafetyError("账本 association 身份不唯一")
        value = values.pop()
        _asst_id, expected = _association_contract(ledger.run_id, model_id)
        if value != expected:
            raise SafetyError("账本 association 不属于当前 run_id/model")
        return value

    @staticmethod
    def _incident_edges(graph: Any, node_ids: set[int]) -> list[dict[str, Any]]:
        if not node_ids:
            return []
        if any(type(node_id) is not int or node_id < 0 for node_id in node_ids):
            raise SafetyError("incident edge node id 非法")
        result = graph._execute_query(
            "MATCH p=(a)-[n]-(b) WHERE ID(a) IN $node_ids RETURN p, ID(startNode(n)), ID(endNode(n))",
            params={"node_ids": sorted(node_ids)},
        )
        records = getattr(result, "result_set", result)
        found: dict[int, dict[str, Any]] = {}
        for record in records or []:
            if not isinstance(record, (list, tuple)) or len(record) != 3:
                raise SafetyError("incident edge 查询结构非法")
            path, src_id, dst_id = record
            edges = list(getattr(path, "_edges", []) or [])
            nodes = list(getattr(path, "_nodes", []) or [])
            if len(edges) != 1 or len(nodes) != 2:
                raise SafetyError("incident edge 查询结构非法")
            edge = edges[0]
            edge_id = getattr(edge, "id", None)
            if any(type(value) is not int or value < 0 for value in (edge_id, src_id, dst_id)):
                raise SafetyError("incident edge id 非法")
            properties = dict(getattr(edge, "properties", {}) or {})
            found[edge_id] = {
                "_id": edge_id,
                "_label": str(getattr(edge, "relation", "")),
                "src_id": src_id,
                "dst_id": dst_id,
                **{
                    key: properties.get(key)
                    for key in (
                        "model_asst_id",
                        "src_model_id",
                        "dst_model_id",
                        "src_inst_id",
                        "dst_inst_id",
                        "classification_model_asst_id",
                    )
                },
            }
        return [found[key] for key in sorted(found)]

    @staticmethod
    def _delete_entities_without_detach(graph: Any, label: str, node_ids: set[int]) -> None:
        if label not in {"instance", "model"}:
            raise SafetyError("cleanup entity label 非法")
        if not node_ids:
            return
        if any(type(node_id) is not int or node_id < 0 for node_id in node_ids):
            raise SafetyError("cleanup entity node id 非法")
        graph._execute_query(
            f"MATCH (n:{label}) WHERE ID(n) IN $node_ids DELETE n",
            params={"node_ids": sorted(node_ids)},
        )

    @staticmethod
    def _validate_instance_relation_contract(
        *,
        ledger: ValidationLedger,
        evidence: Mapping[str, Any],
        require_complete: bool,
    ) -> None:
        instances = evidence.get("instances") or []
        names_to_ids = {item.get("inst_name"): item.get("_id") for item in instances}
        if len(names_to_ids) != len(instances):
            raise SafetyError("instance 名称或 id 不唯一")
        expected_names = {suffix: f"{ledger.run_id}_{suffix}" for suffix in DjangoFalkorLedgerStateBackend.EXPECTED_INSTANCE_SUFFIXES}
        if any(name not in expected_names.values() for name in names_to_ids):
            raise SafetyError("instance 名称不属于关系验证合同")
        expected_pairs = {
            (names_to_ids[expected_names["immediate_source"]], names_to_ids[expected_names["immediate_target"]])
            if expected_names["immediate_source"] in names_to_ids and expected_names["immediate_target"] in names_to_ids
            else None,
            (names_to_ids[expected_names["pending_source"]], names_to_ids[expected_names["backfill_target"]])
            if expected_names["pending_source"] in names_to_ids and expected_names["backfill_target"] in names_to_ids
            else None,
        }
        expected_pairs.discard(None)
        edges = evidence.get("edges") or []
        association_id = DjangoFalkorLedgerStateBackend._association_id(
            ledger,
            DjangoFalkorLedgerStateBackend._model_id(ledger),
            required=bool(edges),
        )
        actual_pairs = {(item.get("src_inst_id"), item.get("dst_inst_id")) for item in edges}
        if (
            len(actual_pairs) != len(edges)
            or not actual_pairs.issubset(expected_pairs)
            or any(item.get("model_asst_id") != association_id for item in edges)
            or (require_complete and actual_pairs != expected_pairs)
        ):
            raise SafetyError("instance edge 关联或精确关系配对不符合合同")
        incident_by_id = {item.get("_id"): item for item in (evidence.get("incident_instance_edges") or [])}
        if any(
            item.get("_id") not in incident_by_id
            or incident_by_id[item.get("_id")].get("src_id") != item.get("src_inst_id")
            or incident_by_id[item.get("_id")].get("dst_id") != item.get("dst_inst_id")
            for item in edges
        ):
            raise SafetyError("instance edge 属性端点与真实图端点不一致")

    @staticmethod
    def validate_present(
        *,
        ledger: ValidationLedger,
        org_id: int,
        snapshot: Mapping[str, Any],
    ) -> None:
        if type(org_id) is not int or org_id <= 0 or snapshot.get("run_id") != ledger.run_id:
            raise SafetyError("verify run_id/organization 非法")
        counts = snapshot.get("counts")
        evidence = snapshot.get("evidence")
        if not isinstance(counts, Mapping) or not isinstance(evidence, Mapping):
            raise SafetyError("verify snapshot 结构非法")
        tasks = evidence.get("tasks")
        if not isinstance(tasks, list) or len(tasks) != 1:
            raise SafetyError("verify task 身份异常")
        task = tasks[0]
        mode = "standard" if task.get("name") == f"{ledger.run_id}_standard_task" else "quick"
        expected_counts = {
            "task": 1,
            "credential": 1,
            "batch": 4,
            "model": 1,
            "model_association": 1,
            "instance": 5,
            "edge": 2,
            "pending": 0,
            "review": 0,
            "field_registration": 1 if mode == "quick" else 0,
        }
        if any(counts.get(key) != value for key, value in expected_counts.items()):
            raise SafetyError("verify ORM/FalkorDB 资源计数不符合执行合同")
        model_id = DjangoFalkorLedgerStateBackend._model_id(ledger)
        if evidence.get("model_id") != model_id:
            raise SafetyError("verify model_id 不属于当前 run_id")
        task_id = task.get("id")
        if (
            task_id not in DjangoFalkorLedgerStateBackend._owned_ids(ledger, "task")
            or task.get("name") not in {f"{ledger.run_id}_quick_task", f"{ledger.run_id}_standard_task"}
            or task.get("team") != [org_id]
            or task.get("model_id") != model_id
        ):
            raise SafetyError("verify task organization/model/run_id 不匹配")
        credentials = evidence.get("credentials")
        if not isinstance(credentials, list) or len(credentials) != 1:
            raise SafetyError("verify credential 身份异常")
        credential = credentials[0]
        if (
            credential.get("id") not in DjangoFalkorLedgerStateBackend._owned_ids(ledger, "credential")
            or credential.get("task_id") != task_id
            or credential.get("is_enabled") is not False
            or credential.get("token_revoked") is not True
        ):
            raise SafetyError("verify credential 未正确归属或吊销")
        if set(evidence.get("batch_ids") or []) != DjangoFalkorLedgerStateBackend._owned_ids(ledger, "batch"):
            raise SafetyError("verify batch 与账本不一致")
        model = evidence.get("model")
        if (
            not isinstance(model, Mapping)
            or model.get("model_id") != model_id
            or model.get("group") != [org_id]
            or model.get("is_custom_reporting") is not True
        ):
            raise SafetyError("verify model organization/run_id 不匹配")
        association_id = DjangoFalkorLedgerStateBackend._association_id(ledger, model_id)
        associations = evidence.get("model_associations")
        if (
            not isinstance(associations, list)
            or [item.get("model_asst_id") for item in associations] != [association_id]
            or associations[0].get("src_model_id") != model_id
            or associations[0].get("dst_model_id") != model_id
        ):
            raise SafetyError("verify model association 不匹配")
        instances = evidence.get("instances")
        expected_names = {f"{ledger.run_id}_{suffix}" for suffix in DjangoFalkorLedgerStateBackend.EXPECTED_INSTANCE_SUFFIXES}
        if not isinstance(instances, list) or {item.get("inst_name") for item in instances} != expected_names:
            raise SafetyError("verify instance identity 不匹配")
        for instance in instances:
            if instance.get("model_id") != model_id or instance.get("crv_run_id") != ledger.run_id or instance.get("organization") != [org_id]:
                raise SafetyError("verify instance organization/model/run_id 不匹配")
            if instance.get("collect_task") != f"cr_{task_id}":
                raise SafetyError("verify instance collect_task 不属于 live task")
        instance_ids = {item.get("_id") for item in instances}
        edges = evidence.get("edges")
        if not isinstance(edges, list) or any(
            item.get("src_inst_id") not in instance_ids or item.get("dst_inst_id") not in instance_ids for item in edges
        ):
            raise SafetyError("verify edge 端点不属于当前 run")
        DjangoFalkorLedgerStateBackend._validate_instance_relation_contract(
            ledger=ledger,
            evidence=evidence,
            require_complete=True,
        )
        expected_fields = ["crv_run_id"] if mode == "quick" else []
        if evidence.get("field_attr_ids") != expected_fields:
            raise SafetyError("verify 字段登记不符合 crv_run_id 合同")

    @staticmethod
    def validate_cleanup_ownership(
        *,
        ledger: ValidationLedger,
        org_id: int,
        snapshot: Mapping[str, Any],
    ) -> None:
        evidence = snapshot.get("evidence")
        if type(org_id) is not int or org_id <= 0 or not isinstance(evidence, Mapping):
            raise SafetyError("cleanup ownership snapshot 非法")
        model_id = DjangoFalkorLedgerStateBackend._model_id(ledger)
        model = evidence.get("model") or {}
        if model and (model.get("model_id") != model_id or model.get("group") != [org_id] or model.get("is_custom_reporting") is not True):
            raise SafetyError("cleanup model 归属证明失败")
        associations = evidence.get("model_associations") or []
        association_id = DjangoFalkorLedgerStateBackend._association_id(ledger, model_id, required=False)
        if any(
            association_id is None
            or item.get("model_asst_id") != association_id
            or item.get("src_model_id") != model_id
            or item.get("dst_model_id") != model_id
            for item in associations
        ):
            raise SafetyError("cleanup association 归属证明失败")
        instances = evidence.get("instances") or []
        ledger_task_ids = DjangoFalkorLedgerStateBackend._owned_ids(ledger, "task")
        collect_tasks = {item.get("collect_task") for item in instances}
        if len(collect_tasks) > 1:
            raise SafetyError("cleanup instance collect_task 不唯一")
        for item in instances:
            collect_task = item.get("collect_task")
            if (
                item.get("model_id") != model_id
                or item.get("crv_run_id") != ledger.run_id
                or item.get("organization") != [org_id]
                or not isinstance(collect_task, str)
                or not collect_task.startswith("cr_")
                or not collect_task[3:].isascii()
                or not collect_task[3:].isdecimal()
                or int(collect_task[3:]) not in ledger_task_ids
            ):
                raise SafetyError("cleanup instance collect_task/model/org/run_id 归属证明失败")
        instance_ids = {item.get("_id") for item in instances}
        if any(item.get("src_inst_id") not in instance_ids or item.get("dst_inst_id") not in instance_ids for item in (evidence.get("edges") or [])):
            raise SafetyError("cleanup edge 端点归属证明失败")
        DjangoFalkorLedgerStateBackend._validate_instance_relation_contract(
            ledger=ledger,
            evidence=evidence,
            require_complete=False,
        )

    @staticmethod
    def validate_cleanup_preflight(*, ledger: ValidationLedger, org_id: int, snapshot: Mapping[str, Any]) -> None:
        DjangoFalkorLedgerStateBackend.validate_cleanup_ownership(ledger=ledger, org_id=org_id, snapshot=snapshot)
        counts = snapshot.get("counts") or {}
        evidence = snapshot.get("evidence") or {}
        model_id = DjangoFalkorLedgerStateBackend._model_id(ledger)
        tasks = evidence.get("tasks") or []
        child_keys = ("task_scope", "credential", "batch", "pending", "review")
        if not tasks:
            if counts.get("task") != 0 or any(counts.get(key) != 0 for key in child_keys):
                raise SafetyError("cleanup task 已缺失但 ORM 子资源非零")
        else:
            if counts.get("task") != 1 or len(tasks) != 1:
                raise SafetyError("cleanup task 查询不唯一")
            task = tasks[0]
            task_id = task.get("id")
            if (
                task_id not in DjangoFalkorLedgerStateBackend._owned_ids(ledger, "task")
                or task.get("name")
                not in {
                    f"{ledger.run_id}_quick_task",
                    f"{ledger.run_id}_standard_task",
                }
                or task.get("team") != [org_id]
                or task.get("model_id") != model_id
            ):
                raise SafetyError("cleanup task id/name/team/model 归属证明失败")
            credentials = evidence.get("credentials") or []
            if len(credentials) != 1:
                raise SafetyError("cleanup credential 查询不唯一")
            credential = credentials[0]
            if credential.get("id") not in DjangoFalkorLedgerStateBackend._owned_ids(ledger, "credential") or credential.get("task_id") != task_id:
                raise SafetyError("cleanup credential 归属证明失败")
            batches = evidence.get("batches") or []
            if {item.get("id") for item in batches} != DjangoFalkorLedgerStateBackend._owned_ids(ledger, "batch") or any(
                item.get("task_id") != task_id for item in batches
            ):
                raise SafetyError("cleanup batch 归属证明失败")

        model = evidence.get("model") or {}
        model_incident = evidence.get("incident_model_edges") or []
        model_association_ids = {item.get("_id") for item in (evidence.get("model_associations") or [])}
        subordinate = [item for item in model_incident if item.get("_label") == "subordinate_model"]
        if model:
            classification = evidence.get("classification") or {}
            model_node_id = model.get("_id")
            classification_node_id = classification.get("_id")
            classification_id = model.get("classification_id")
            if (
                type(model_node_id) is not int
                or type(classification_node_id) is not int
                or not isinstance(classification_id, str)
                or not classification_id
                or classification.get("classification_id") != classification_id
            ):
                raise SafetyError("cleanup classification 归属证明失败")
            if len(subordinate) > 1 or (tasks and len(subordinate) != 1):
                raise SafetyError("cleanup incident model subordinate edge 不符合合同")
            if subordinate and (
                {subordinate[0].get("src_id"), subordinate[0].get("dst_id")} != {classification_node_id, model_node_id}
                or subordinate[0].get("classification_model_asst_id") != f"{classification_id}_subordinate_model_{model_id}"
            ):
                raise SafetyError("cleanup incident model subordinate edge 不符合合同")
            association_incident = [item for item in model_incident if item.get("_id") in model_association_ids]
            if any(
                item.get("_label") != "model_association" or item.get("src_id") != model_node_id or item.get("dst_id") != model_node_id
                for item in association_incident
            ):
                raise SafetyError("cleanup incident model association 端点不属于 owned model")
        elif subordinate:
            raise SafetyError("cleanup incident model edge 无 owned model")
        allowed_model_edge_ids = model_association_ids | {item.get("_id") for item in subordinate}
        if {item.get("_id") for item in model_incident} != allowed_model_edge_ids or any(
            item.get("_label") not in {"model_association", "subordinate_model"} for item in model_incident
        ):
            raise SafetyError("cleanup incident model edge 包含未证明外部边")

        instance_edge_ids = {item.get("_id") for item in (evidence.get("edges") or [])}
        instance_ids = {item.get("_id") for item in (evidence.get("instances") or [])}
        incident_instance = evidence.get("incident_instance_edges") or []
        if {item.get("_id") for item in incident_instance} != instance_edge_ids or any(
            item.get("_label") != "instance_association" or item.get("src_id") not in instance_ids or item.get("dst_id") not in instance_ids
            for item in incident_instance
        ):
            raise SafetyError("cleanup incident instance edge 包含未证明外部边")

    def _raw_snapshot(self, *, ledger: ValidationLedger) -> dict[str, Any]:
        from apps.cmdb.constants.constants import CLASSIFICATION, INSTANCE, MODEL
        from apps.cmdb.graph.drivers.graph_client import GraphClient
        from apps.cmdb.models.change_record import ChangeRecord
        from apps.cmdb_enterprise.custom_reporting.models import (
            CustomReportingBatch,
            CustomReportingCleanupReview,
            CustomReportingCredential,
            CustomReportingFieldRegistration,
            CustomReportingPendingRelation,
            CustomReportingTask,
            CustomReportingTaskScope,
        )

        model_id = self._model_id(ledger)
        names = [f"{ledger.run_id}_quick_task", f"{ledger.run_id}_standard_task"]
        tasks = list(CustomReportingTask.objects.filter(name__in=names).order_by("id"))
        task_ids = [item.id for item in tasks]
        credentials = list(CustomReportingCredential.objects.filter(task_id__in=task_ids).order_by("id"))
        batches = list(CustomReportingBatch.objects.filter(task_id__in=task_ids).order_by("id"))
        pending = CustomReportingPendingRelation.objects.filter(task_id__in=task_ids)
        reviews = CustomReportingCleanupReview.objects.filter(batch__task_id__in=task_ids)
        scopes = CustomReportingTaskScope.objects.filter(task_id__in=task_ids)
        fields = list(CustomReportingFieldRegistration.objects.filter(model_id=model_id).order_by("attr_id"))
        changes = ChangeRecord.objects.filter(model_id=model_id)
        with GraphClient() as graph:
            models, _ = graph.query_entity(MODEL, [{"field": "model_id", "type": "str=", "value": model_id}])
            instances, _ = graph.query_entity(INSTANCE, [{"field": "model_id", "type": "str=", "value": model_id}])
            classifications = []
            if len(models) == 1 and isinstance(models[0].get("classification_id"), str):
                classifications, _ = graph.query_entity(
                    CLASSIFICATION,
                    [{"field": "classification_id", "type": "str=", "value": models[0]["classification_id"]}],
                )
            incident_model_edges = self._incident_edges(graph, {item["_id"] for item in models})
            incident_instance_edges = self._incident_edges(graph, {item["_id"] for item in instances})
        model_associations = [item for item in incident_model_edges if item.get("_label") == "model_association"]
        edges = [item for item in incident_instance_edges if item.get("_label") == "instance_association"]
        counts = {
            "task": len(tasks),
            "task_scope": scopes.count(),
            "credential": len(credentials),
            "batch": len(batches),
            "model": len(models),
            "model_association": len(model_associations),
            "instance": len(instances),
            "edge": len(edges),
            "pending": pending.count(),
            "review": reviews.count(),
            "field_registration": len(fields),
            "change_record": changes.count(),
        }
        return {
            "run_id": ledger.run_id,
            "counts": counts,
            "evidence": {
                "model_id": model_id,
                "tasks": [{"id": item.id, "name": item.name, "team": item.team, "model_id": (item.config or {}).get("model_id")} for item in tasks],
                "credentials": [
                    {
                        "id": item.id,
                        "task_id": item.task_id,
                        "is_enabled": item.is_enabled,
                        "token_revoked": (item.credential_data or {}).get("token_revoked") is True,
                    }
                    for item in credentials
                ],
                "batch_ids": [item.id for item in batches],
                "batches": [{"id": item.id, "task_id": item.task_id} for item in batches],
                "model": (
                    {key: models[0].get(key) for key in ("_id", "model_id", "classification_id", "group", "is_custom_reporting")}
                    if len(models) == 1
                    else {}
                ),
                "classification": ({key: classifications[0].get(key) for key in ("_id", "classification_id")} if len(classifications) == 1 else {}),
                "model_associations": [
                    {key: item.get(key) for key in ("_id", "model_asst_id", "src_model_id", "dst_model_id")} for item in model_associations
                ],
                "instances": [
                    {key: item.get(key) for key in ("_id", "model_id", "inst_name", "crv_run_id", "organization", "collect_task")}
                    for item in instances
                ],
                "edges": [{key: item.get(key) for key in ("_id", "model_asst_id", "src_inst_id", "dst_inst_id")} for item in edges],
                "incident_model_edges": incident_model_edges,
                "incident_instance_edges": incident_instance_edges,
                "field_attr_ids": [item.attr_id for item in fields],
            },
        }

    def snapshot(
        self,
        *,
        ledger: ValidationLedger,
        org_id: int | None,
        expect_present: bool,
    ) -> dict[str, Any]:
        snapshot = self._raw_snapshot(ledger=ledger)
        if expect_present:
            if type(org_id) is not int:
                raise SafetyError("verify 必须提供正整数 organization")
            self.validate_present(ledger=ledger, org_id=org_id, snapshot=snapshot)
            self.validate_cleanup_preflight(ledger=ledger, org_id=org_id, snapshot=snapshot)
        elif type(org_id) is int:
            self.validate_cleanup_preflight(ledger=ledger, org_id=org_id, snapshot=snapshot)
        return snapshot

    def cleanup(
        self,
        *,
        ledger: ValidationLedger,
        org_id: int | None,
    ) -> dict[str, Any]:
        from apps.cmdb.constants.constants import INSTANCE, MODEL
        from apps.cmdb.graph.drivers.graph_client import GraphClient
        from apps.cmdb.models.change_record import ChangeRecord
        from apps.cmdb_enterprise.custom_reporting.models import CustomReportingFieldRegistration

        if type(org_id) is not int or org_id <= 0:
            raise SafetyError("cleanup 必须提供正整数 organization")
        snapshot = self._raw_snapshot(ledger=ledger)
        counts = snapshot["counts"]
        if any(counts[key] for key in ("task", "task_scope", "credential", "batch", "pending", "review")):
            raise CleanupIncompleteError("HTTP task 清理后 ORM 子资源仍存在")
        if counts["model_association"]:
            raise CleanupIncompleteError("HTTP association 清理后图关联仍存在")
        self.validate_cleanup_preflight(ledger=ledger, org_id=org_id, snapshot=snapshot)
        model_id = self._model_id(ledger)
        evidence = snapshot["evidence"]
        models = [evidence["model"]] if counts["model"] == 1 else []
        if counts["model"] > 1:
            raise SafetyError("cleanup model 查询不唯一")
        instances = evidence["instances"]
        instance_ids = {item.get("_id") for item in instances}
        edges = evidence["incident_instance_edges"]
        subordinate_edges = [item for item in evidence["incident_model_edges"] if item.get("_label") == "subordinate_model"]
        deleted = {"edge": 0, "instance": 0, "model": 0, "field_registration": 0, "change_record": 0}
        with GraphClient() as graph:
            for edge in edges:
                graph.delete_edge(edge["_id"])
                deleted["edge"] += 1
            for edge in subordinate_edges:
                graph.delete_edge(edge["_id"])
                deleted["edge"] += 1
            if instance_ids:
                self._delete_entities_without_detach(graph, INSTANCE, instance_ids)
                deleted["instance"] = len(instance_ids)
            if models:
                self._delete_entities_without_detach(graph, MODEL, {models[0]["_id"]})
                deleted["model"] = 1
        deleted["field_registration"], _ = CustomReportingFieldRegistration.objects.filter(model_id=model_id).delete()
        deleted["change_record"], _ = ChangeRecord.objects.filter(model_id=model_id).delete()
        return {"deleted": deleted}


class RequestsTransport:
    """Bounded requests adapter with environment proxies and redirects disabled."""

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.trust_env = False

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None,
        connect_timeout: float,
        read_timeout: float,
        max_response_bytes: int,
    ) -> HttpResponse:
        with self._session.request(
            method,
            url,
            headers=dict(headers),
            json=json_body,
            timeout=(connect_timeout, read_timeout),
            allow_redirects=False,
            stream=True,
        ) as response:
            body = bytearray()
            for chunk in response.iter_content(64 * 1024):
                body.extend(chunk)
                if len(body) > max_response_bytes:
                    break
            return HttpResponse(response.status_code, dict(response.headers), bytes(body))


def _redact(value: Any, known_values: Sequence[str] = ()) -> Any:
    known = tuple(item for item in known_values if item)
    if isinstance(value, Mapping):
        return {
            str(key): ("***" if any(part in str(key).lower() for part in SENSITIVE_KEY_PARTS) else _redact(item, known))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item, known) for item in value]
    if isinstance(value, str):
        redacted = value
        for secret in known:
            redacted = redacted.replace(secret, "***")
        return redacted
    return value


def _normalize_allowed_hosts(allowed_hosts: set[str]) -> frozenset[str]:
    normalized = frozenset(host.strip().lower() for host in allowed_hosts if host.strip())
    if not normalized or any("*" in host for host in normalized):
        raise SafetyError("CRV_ALLOWED_HOSTS 必须是非空精确 IP literal 列表，且禁止通配符")
    return normalized


def _validate_url(url: str, allowed_hosts: frozenset[str], *, execute: bool) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise SafetyError("URL scheme 仅允许 http/https")
    if parsed.username is not None or parsed.password is not None:
        raise SafetyError("URL 禁止 userinfo")
    hostname = (parsed.hostname or "").lower()
    if hostname not in allowed_hosts:
        raise SafetyError("URL host 不在 CRV_ALLOWED_HOSTS 精确允许列表")
    if not execute:
        return
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        raise SafetyError("execute 仅允许精确 loopback IP literal，禁止 DNS hostname") from None
    if not address.is_loopback:
        raise SafetyError("execute 仅允许 127/8 或 ::1 loopback 地址")


def _owned_identifier(run_id: str, identifier: Any) -> str:
    if type(identifier) is not int or identifier <= 0:
        raise HttpProtocolError("响应缺少合法正整数 id")
    return f"{run_id}:{identifier}"


def _parse_owned_int(run_id: str, identifier: Any) -> int:
    if not isinstance(identifier, str):
        raise SafetyError("账本资源 identifier 不是 owned identifier")
    prefix = f"{run_id}:"
    if not identifier.startswith(prefix):
        raise SafetyError("账本资源不属于当前 run_id")
    raw = identifier[len(prefix) :]
    if not raw.isascii() or not raw.isdecimal() or raw.startswith("0"):
        raise SafetyError("账本资源真实 id 格式非法")
    value = int(raw)
    if value <= 0:
        raise SafetyError("账本资源真实 id 格式非法")
    return value


class SafeHttpClient:
    def __init__(
        self,
        *,
        base_url: str,
        allowed_hosts: set[str],
        transport: Transport,
        write_enabled: bool,
        session_cookie: str | None,
        management_api_secret: str | None,
        org_id: int | None,
    ) -> None:
        self.allowed_hosts = _normalize_allowed_hosts(allowed_hosts)
        self.base_url = base_url.rstrip("/") + "/"
        _validate_url(self.base_url, self.allowed_hosts, execute=write_enabled)
        self.transport = transport
        self.write_enabled = write_enabled
        self.session_cookie = session_cookie
        self.management_api_secret = management_api_secret
        self.org_id = org_id
        self.requests_sent = 0

    def _management_headers(self) -> dict[str, str]:
        if not self.session_cookie or type(self.org_id) is not int or self.org_id <= 0:
            raise SafetyError("execute 管理请求必须提供 CRV_SESSION_COOKIE 与正整数 CRV_ORG_ID")
        if (
            not isinstance(self.management_api_secret, str)
            or not self.management_api_secret.strip()
            or "\r" in self.management_api_secret
            or "\n" in self.management_api_secret
        ):
            raise SafetyError("execute 管理请求必须提供 CRV_MANAGEMENT_API_SECRET")
        cookie = self.session_cookie.strip().rstrip(";")
        if "\r" in cookie or "\n" in cookie:
            raise SafetyError("session cookie 格式非法")
        return {
            "Accept": "application/json",
            "Api-Authorization": self.management_api_secret,
            "Content-Type": "application/json",
            "Cookie": f"{cookie}; current_team={self.org_id}",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
        ingest_token: str | None = None,
        expect_token_rejection: bool = False,
    ) -> dict[str, Any]:
        if not self.write_enabled:
            raise SafetyError("网络请求被三重执行门拒绝")
        url = urljoin(self.base_url, path)
        _validate_url(url, self.allowed_hosts, execute=True)
        if ingest_token is None:
            headers = self._management_headers()
        else:
            if not ingest_token:
                raise SafetyError("ingest 缺少 token")
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {ingest_token}",
            }
        try:
            response = self.transport.request(
                method=method,
                url=url,
                headers=headers,
                json_body=payload,
                connect_timeout=CONNECT_TIMEOUT,
                read_timeout=READ_TIMEOUT,
                max_response_bytes=MAX_RESPONSE_BYTES,
            )
        except Exception:
            raise HttpProtocolError("HTTP transport failed") from None
        self.requests_sent += 1
        if len(response.body) > MAX_RESPONSE_BYTES:
            raise HttpProtocolError("HTTP 响应体超过上限")
        if response.status in REDIRECT_STATUSES:
            raise HttpProtocolError("HTTP 重定向已拒绝，禁止重放请求")
        if expect_token_rejection and response.status in {401, 403}:
            return {"token_rejected": True}
        if not 200 <= response.status < 300:
            raise HttpProtocolError(f"HTTP {response.status}")
        try:
            decoded = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HttpProtocolError("HTTP 响应不是合法 JSON") from None
        if (
            not isinstance(decoded, dict)
            or set(decoded) != {"result", "data", "message"}
            or type(decoded["result"]) is not bool
            or not isinstance(decoded["message"], str)
            or not isinstance(decoded["data"], dict)
        ):
            raise HttpProtocolError("HTTP 响应不符合 WebUtils envelope")
        if decoded["result"] is not True and expect_token_rejection:
            message = decoded["message"].lower()
            if any(subject in message for subject in TOKEN_REJECTION_SUBJECTS) and any(state in message for state in TOKEN_REJECTION_STATES):
                return {"token_rejected": True}
            raise HttpProtocolError("token 作废验证未返回明确拒绝")
        if decoded["result"] is not True:
            raise HttpProtocolError("WebUtils result=false")
        return decoded["data"]

    def create_task(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._request("POST", "tasks/", payload=payload)

    def delete_task(self, task_id: int) -> dict[str, Any]:
        return self._request("DELETE", f"tasks/{task_id}/")

    def ingest(self, payload: Mapping[str, Any], token: str) -> dict[str, Any]:
        return self._request("POST", "ingest/", payload=payload, ingest_token=token)

    def expect_ingest_token_rejected(self, token: str) -> None:
        data = self._request(
            "POST",
            "ingest/",
            payload={"instances": [], "relations": []},
            ingest_token=token,
            expect_token_rejection=True,
        )
        if data.get("token_rejected") is not True:
            raise HttpProtocolError("已作废 token 仍被 ingest 接受")

    def create_model_association(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._request("POST", "../model/association/", payload=payload)

    def delete_model_association(self, model_asst_id: str) -> dict[str, Any]:
        encoded = quote(model_asst_id, safe="")
        return self._request("DELETE", f"../model/association/{encoded}/")

    def rotate_credential(self, task_id: int, credential_id: int) -> dict[str, Any]:
        return self._request(
            "POST",
            f"tasks/{task_id}/rotate_credential/",
            payload={"credential_id": credential_id},
        )

    def revoke_credential(self, task_id: int, credential_id: int) -> dict[str, Any]:
        return self._request(
            "POST",
            f"tasks/{task_id}/revoke_credential/",
            payload={"credential_id": credential_id},
        )

    def list_tasks(self, name: str, page: int = 1) -> dict[str, Any]:
        query = urlencode({"name": name, "page": page, "page_size": TASK_SCAN_PAGE_SIZE})
        return self._request("GET", f"tasks/?{query}")


@dataclass(frozen=True)
class PlanStep:
    name: str
    method: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class ExecutionPlan:
    run_id: str
    mode: str
    steps: tuple[PlanStep, ...]
    cleanup_order: tuple[str, ...]

    @property
    def resource_names(self) -> tuple[str, ...]:
        names = []
        for step in self.steps:
            name = step.payload.get("name")
            if isinstance(name, str):
                names.append(name)
        return tuple(names)

    def to_safe_dict(self, known_values: Sequence[str] = ()) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "steps": [
                {
                    "name": step.name,
                    "method": step.method,
                    "payload": _redact(step.payload, known_values),
                }
                for step in self.steps
            ],
            "cleanup_order": list(self.cleanup_order),
            "resource_names": list(self.resource_names),
        }


def _quick_task_payload(run_id: str, org_id: int, classification_id: str) -> dict[str, Any]:
    model_name = f"{run_id}_model"
    return {
        "name": f"{run_id}_quick_task",
        "team": [org_id],
        "config": {
            "mode": "quick",
            "cleanup_strategy": "none",
            "identity_keys": ["inst_name"],
        },
        "quick_model": {
            "model_id": model_name.lower(),
            "model_name": model_name,
            "classification_id": classification_id,
            "identity_keys": ["inst_name"],
        },
        "is_enabled": True,
    }


def _standard_task_payload(run_id: str, org_id: int, model_id: str) -> dict[str, Any]:
    return {
        "name": f"{run_id}_standard_task",
        "team": [org_id],
        "config": {
            "mode": "standard",
            "model_id": model_id,
            "cleanup_strategy": "none",
            "identity_keys": ["inst_name"],
        },
        "is_enabled": True,
    }


def _ingest_payload(run_id: str, suffix: str) -> dict[str, Any]:
    return {
        "instances": [
            {
                "inst_name": f"{run_id}_{suffix}",
                "crv_run_id": run_id,
            }
        ],
        "relations": [],
    }


def _relation_ingest_payloads(run_id: str, model_id: str, model_asst_id: str) -> tuple[dict[str, Any], ...]:
    immediate_source = f"{run_id}_immediate_source"
    immediate_target = f"{run_id}_immediate_target"
    pending_source = f"{run_id}_pending_source"
    backfill_target = f"{run_id}_backfill_target"

    def identity(inst_name: str) -> dict[str, Any]:
        return {"model_id": model_id, "identity": {"inst_name": inst_name}}

    return (
        {
            "instances": [
                {"inst_name": immediate_source, "crv_run_id": run_id},
                {"inst_name": immediate_target, "crv_run_id": run_id},
            ],
            "relations": [
                {
                    "source": identity(immediate_source),
                    "target": identity(immediate_target),
                    "asst_id": model_asst_id,
                }
            ],
        },
        {
            "instances": [{"inst_name": pending_source, "crv_run_id": run_id}],
            "relations": [
                {
                    "source": identity(pending_source),
                    "target": identity(backfill_target),
                    "asst_id": model_asst_id,
                }
            ],
        },
        {
            "instances": [{"inst_name": backfill_target, "crv_run_id": run_id}],
            "relations": [],
        },
    )


def _association_contract(run_id: str, model_id: str) -> tuple[str, str]:
    nonce = run_id.rsplit("_", 1)[-1]
    asst_id = f"crv_rel_{nonce}"
    return asst_id, f"{model_id}_{asst_id}_{model_id}"


def build_execution_plan(
    mode: str,
    run_id: str,
    org_id: int = 1,
    classification_id: str = "other",
) -> ExecutionPlan:
    if mode not in {"quick", "standard"}:
        raise ValueError("mode 仅允许 quick/standard")
    quick = _quick_task_payload(run_id, org_id, classification_id)
    model_id = quick["quick_model"]["model_id"]
    asst_id, model_asst_id = _association_contract(run_id, model_id)
    immediate_payload, pending_payload, backfill_payload = _relation_ingest_payloads(run_id, model_id, model_asst_id)
    steps = [PlanStep("create_quick_task", "POST", quick)]
    if mode == "standard":
        steps.extend(
            [
                PlanStep("delete_seed_task", "DELETE", {}),
                PlanStep("create_standard_task", "POST", {"name": f"{run_id}_standard_task"}),
            ]
        )
    steps.append(
        PlanStep(
            "create_model_association",
            "POST",
            {
                "src_model_id": model_id,
                "dst_model_id": model_id,
                "asst_id": asst_id,
            },
        )
    )
    steps.extend(
        [
            PlanStep("ingest_immediate_relation", "POST", immediate_payload),
            PlanStep("ingest_pending_relation", "POST", pending_payload),
            PlanStep("ingest_backfill_target", "POST", backfill_payload),
            PlanStep("rotate_credential", "POST", {}),
            PlanStep("ingest_with_rotated_token", "POST", _ingest_payload(run_id, "after_rotate")),
            PlanStep("revoke_credential", "POST", {}),
            PlanStep("verify_old_token_rejected", "POST", {"instances": [], "relations": []}),
            PlanStep("verify_revoked_token_rejected", "POST", {"instances": [], "relations": []}),
        ]
    )
    return ExecutionPlan(
        run_id,
        mode,
        tuple(steps),
        ("task", "association", "model_verification"),
    )


class HttpRunner:
    def __init__(
        self,
        *,
        base_url: str,
        allowed_hosts: set[str],
        transport: Transport,
        ledger: ValidationLedger,
        ledger_path: Path,
        execute: bool = False,
        cli_execute: bool = False,
        session_cookie: str | None = None,
        management_api_secret: str | None = None,
        org_id: int | None = None,
        classification_id: str = "other",
        state_backend: LedgerStateBackend | None = None,
        **_ignored: Any,
    ) -> None:
        self.ledger = ledger
        self.ledger_path = ledger_path
        self.org_id = org_id
        self.classification_id = classification_id
        self.state_backend = state_backend
        self.write_enabled = execute is True and cli_execute is True and os.environ.get("CRV_ALLOW_WRITE") == "1"
        self.client = SafeHttpClient(
            base_url=base_url,
            allowed_hosts=allowed_hosts,
            transport=transport,
            write_enabled=self.write_enabled,
            session_cookie=session_cookie,
            management_api_secret=management_api_secret,
            org_id=org_id,
        )

    def reserve_ledger(self) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.ledger_path.open("x", encoding="utf-8") as stream:
                stream.write(self.ledger.to_json())
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            raise SafetyError("账本路径已存在，拒绝覆盖") from None

    def _persist_ledger(self) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.ledger_path.with_name(f".{self.ledger_path.name}.{secrets.token_hex(8)}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                stream.write(self.ledger.to_json())
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.ledger_path)
        finally:
            temporary.unlink(missing_ok=True)

    def _record_owned(self, kind: str, identifier: Any) -> None:
        self.ledger.record(kind, _owned_identifier(self.ledger.run_id, identifier))
        self._persist_ledger()

    @staticmethod
    def _created_task(data: Mapping[str, Any]) -> tuple[int, int, str, str]:
        task_id = data.get("id")
        credential = data.get("credential")
        config = data.get("config")
        raw_token = data.get("token")
        if (
            type(task_id) is not int
            or task_id <= 0
            or not isinstance(credential, dict)
            or type(credential.get("id")) is not int
            or credential["id"] <= 0
            or not isinstance(config, dict)
            or not isinstance(config.get("model_id"), str)
            or not config["model_id"]
            or not isinstance(raw_token, str)
            or not raw_token
        ):
            raise HttpProtocolError("任务创建响应缺少真实 id/config.model_id/credential/token")
        return task_id, credential["id"], config["model_id"], raw_token

    def _create_task(self, payload: Mapping[str, Any], *, expected_model_id: str) -> tuple[int, int, str, str]:
        created = self.client.create_task(payload)
        task_id, credential_id, model_id, raw_token = self._created_task(created)
        self._record_owned("task", task_id)
        self._record_owned("credential", credential_id)
        if model_id != expected_model_id:
            raise HttpProtocolError("任务创建响应 config.model_id 与请求不一致")
        return task_id, credential_id, model_id, raw_token

    def _create_model_association(self, model_id: str) -> str:
        asst_id, expected_model_asst_id = _association_contract(self.ledger.run_id, model_id)
        intent = f"{self.ledger.run_id}_association_intent_{expected_model_asst_id}"
        self.ledger.record("association", intent)
        self._persist_ledger()
        payload = {
            "src_model_id": model_id,
            "dst_model_id": model_id,
            "asst_id": asst_id,
        }
        created = self.client.create_model_association(payload)
        if (
            type(created.get("_id")) is not int
            or created["_id"] <= 0
            or created.get("src_model_id") != model_id
            or created.get("dst_model_id") != model_id
            or created.get("asst_id") != asst_id
            or created.get("model_asst_id") != expected_model_asst_id
        ):
            raise HttpProtocolError("模型关联创建响应与请求不一致")
        model_asst_id = created["model_asst_id"]
        self.ledger.record("association", model_asst_id)
        self._persist_ledger()
        return model_asst_id

    def _ingest_and_record(
        self,
        payload: Mapping[str, Any],
        token: str,
        *,
        expected_instances: int,
        expected_relations: int,
        expected_pending: int,
    ) -> None:
        data = self.client.ingest(payload, token)
        batch_id = data.get("batch_id")
        summary = data.get("summary")
        summary_keys = {
            "instances_received",
            "relations_received",
            "created",
            "updated",
            "deleted",
            "errors",
            "pending_relations",
        }
        if (
            type(batch_id) is not int
            or batch_id <= 0
            or not isinstance(summary, dict)
            or set(summary) != summary_keys
            or any(type(summary[key]) is not int or summary[key] < 0 for key in summary_keys)
            or summary["instances_received"] != expected_instances
            or summary["relations_received"] != expected_relations
            or summary["pending_relations"] != expected_pending
            or summary["errors"] != 0
        ):
            raise HttpProtocolError("ingest 响应 batch/summary 与请求计划不一致")
        self._record_owned("batch", batch_id)

    def _expect_ingest_rejected(self, token: str) -> None:
        self.client.expect_ingest_token_rejected(token)

    def _execute(self, mode: str) -> None:
        if type(self.org_id) is not int or self.org_id <= 0:
            raise SafetyError("CRV_ORG_ID 必须是正整数")
        seed_payload = _quick_task_payload(self.ledger.run_id, self.org_id, self.classification_id)
        expected_model_id = seed_payload["quick_model"]["model_id"]
        self.ledger.record("model", expected_model_id)
        self._persist_ledger()
        task_id, credential_id, model_id, current_token = self._create_task(seed_payload, expected_model_id=expected_model_id)
        if mode == "standard":
            self.client.delete_task(task_id)
            standard_payload = _standard_task_payload(self.ledger.run_id, self.org_id, model_id)
            task_id, credential_id, _model_id, current_token = self._create_task(standard_payload, expected_model_id=model_id)
        model_asst_id = self._create_model_association(model_id)
        immediate_payload, pending_payload, backfill_payload = _relation_ingest_payloads(self.ledger.run_id, model_id, model_asst_id)
        self._ingest_and_record(
            immediate_payload,
            current_token,
            expected_instances=2,
            expected_relations=1,
            expected_pending=0,
        )
        self._ingest_and_record(
            pending_payload,
            current_token,
            expected_instances=1,
            expected_relations=1,
            expected_pending=1,
        )
        self._ingest_and_record(
            backfill_payload,
            current_token,
            expected_instances=1,
            expected_relations=0,
            expected_pending=0,
        )
        rotated = self.client.rotate_credential(task_id, credential_id)
        rotated_credential = rotated.get("credential")
        new_token = rotated.get("token")
        if (
            not isinstance(rotated_credential, dict)
            or rotated_credential.get("id") != credential_id
            or not isinstance(new_token, str)
            or not new_token
        ):
            raise HttpProtocolError("凭据轮换响应形态错误")
        self._ingest_and_record(
            _ingest_payload(self.ledger.run_id, "after_rotate"),
            new_token,
            expected_instances=1,
            expected_relations=0,
            expected_pending=0,
        )
        self._expect_ingest_rejected(current_token)
        revoked = self.client.revoke_credential(task_id, credential_id)
        if revoked.get("credential_id") != credential_id or revoked.get("is_enabled") is not False:
            raise HttpProtocolError("凭据吊销响应形态错误")
        self._expect_ingest_rejected(new_token)

    def run(self, *, mode: str = "quick", token: str | None = None) -> dict[str, Any]:
        del token
        plan = build_execution_plan(
            mode,
            self.ledger.run_id,
            self.org_id if type(self.org_id) is int and self.org_id > 0 else 1,
            self.classification_id,
        )
        known_secrets = (
            self.client.session_cookie or "",
            self.client.management_api_secret or "",
            os.environ.get("CRV_TOKEN", ""),
        )
        result = plan.to_safe_dict(known_secrets)
        result.update({"dry_run": not self.write_enabled, "requests_sent": 0})
        if self.write_enabled:
            self.reserve_ledger()
            self._execute(mode)
            result["requests_sent"] = self.client.requests_sent
        return _redact(result, known_secrets)

    def _scan_owned_tasks(self) -> dict[int, str]:
        data = self.client.list_tasks(self.ledger.run_id, 1)
        if set(data) != {"count", "next", "previous", "results"}:
            raise HttpProtocolError("任务列表分页结构错误")
        results = data["results"]
        if (
            type(data["count"]) is not int
            or data["count"] < 0
            or not isinstance(results, list)
            or data["count"] != len(results)
            or data["count"] > TASK_SCAN_PAGE_SIZE
            or data["next"] is not None
        ):
            raise HttpProtocolError("任务列表分页结构错误")
        allowed_names = {
            f"{self.ledger.run_id}_quick_task",
            f"{self.ledger.run_id}_standard_task",
        }
        owned: dict[int, str] = {}
        for item in results:
            if not isinstance(item, dict) or type(item.get("id")) is not int or item["id"] <= 0 or not isinstance(item.get("name"), str):
                raise HttpProtocolError("任务列表 item 结构错误")
            if item["name"] not in allowed_names or item["id"] in owned:
                raise HttpProtocolError("任务列表包含名称异常或重复任务")
            owned[item["id"]] = item["name"]
        return owned

    def _ledger_task_names(self) -> dict[int, str]:
        task_ids = [_parse_owned_int(self.ledger.run_id, resource.identifier) for resource in self.ledger.resources if resource.kind == "task"]
        if len(task_ids) > 2 or len(task_ids) != len(set(task_ids)):
            raise SafetyError("账本 task 资源数量或身份异常")
        names = (f"{self.ledger.run_id}_quick_task", f"{self.ledger.run_id}_standard_task")
        return dict(zip(task_ids, names))

    def verify(self) -> dict[str, Any]:
        if self.state_backend is None:
            raise SafetyError("verify 必须提供真实 ORM/FalkorDB state backend")
        snapshot = self.state_backend.snapshot(
            ledger=self.ledger,
            org_id=self.org_id,
            expect_present=True,
        )
        return _redact({"verified": True, **snapshot})

    def _cleanup_association_present(self) -> bool | None:
        if self.state_backend is None:
            return None
        pre_cleanup = self.state_backend.snapshot(
            ledger=self.ledger,
            org_id=self.org_id,
            expect_present=False,
        )
        association_count = (pre_cleanup.get("counts") or {}).get("model_association")
        if association_count not in {0, 1}:
            raise CleanupIncompleteError("cleanup association 查询不唯一")
        if any(resource.kind == "association" for resource in self.ledger.resources):
            return association_count == 1
        return None

    def _delete_http_resources(self, existing_tasks: Mapping[int, str], association_present: bool | None) -> None:
        deleted_associations: set[str] = set()
        intent_prefix = f"{self.ledger.run_id}_association_intent_"
        for resource in self.ledger.cleanup_plan():
            if resource.kind == "task":
                task_id = _parse_owned_int(self.ledger.run_id, resource.identifier)
                if task_id in existing_tasks:
                    self.client.delete_task(task_id)
            elif resource.kind == "association":
                if not isinstance(resource.identifier, str):
                    raise SafetyError("association identifier 非法")
                association_id = resource.identifier[len(intent_prefix) :] if resource.identifier.startswith(intent_prefix) else resource.identifier
                if not association_id or association_id in deleted_associations:
                    continue
                if association_present is not False:
                    self.client.delete_model_association(association_id)
                deleted_associations.add(association_id)
            elif resource.kind not in {"credential", "batch", "model"}:
                raise SafetyError(f"cleanup 禁止处理不可证明资源: {resource.kind}")

    def _cleanup_backend(self) -> tuple[dict[str, Any], dict[str, int]]:
        if self.state_backend is None:
            if any(resource.kind == "model" for resource in self.ledger.resources):
                raise CleanupIncompleteError("模型图资源无法由真实 HTTP API 验证，账本已保留交 Task 9")
            return {"deleted": {}}, {}
        backend_result = self.state_backend.cleanup(ledger=self.ledger, org_id=self.org_id)
        snapshot = self.state_backend.snapshot(
            ledger=self.ledger,
            org_id=self.org_id,
            expect_present=False,
        )
        residual = snapshot.get("counts")
        if not isinstance(residual, dict) or any(type(value) is not int or value != 0 for value in residual.values()):
            raise CleanupIncompleteError("cleanup residual 非零，账本已保留")
        return backend_result, residual

    def _preserve_cleanup_ledger(self) -> None:
        try:
            self._persist_ledger()
        except Exception:
            raise CleanupIncompleteError("清理未完整完成，账本持久化状态无法确认") from None

    def cleanup(self, token: str | None = None) -> dict[str, Any]:
        del token
        if not self.write_enabled:
            raise SafetyError("cleanup 被三重执行门拒绝")
        try:
            self._persist_ledger()
            ledger_tasks = self._ledger_task_names()
            existing_tasks = self._scan_owned_tasks()
            for task_id, name in existing_tasks.items():
                if ledger_tasks.get(task_id) != name:
                    raise CleanupIncompleteError("任务列表存在不在账本或名称不匹配的本 run 任务")
            association_present = self._cleanup_association_present()
            self._delete_http_resources(existing_tasks, association_present)
            if self._scan_owned_tasks():
                raise CleanupIncompleteError("清理后仍存在本 run 任务")
            backend_result, residual = self._cleanup_backend()
        except CleanupIncompleteError:
            self._preserve_cleanup_ledger()
            raise
        except Exception:
            self._preserve_cleanup_ledger()
            raise CleanupIncompleteError("清理未完整完成，账本已保留") from None
        self.ledger_path.unlink(missing_ok=True)
        return {"cleaned": True, "deleted": backend_result.get("deleted", {}), "residual": residual}


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="安全自定义上报 HTTP E2E 驱动")
    parser.add_argument("--base-url", default=os.environ.get("CRV_BASE_URL"))
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--mode", choices=("quick", "standard"), default="quick")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--verify-ledger", action="store_true")
    parser.add_argument("--cleanup-ledger", action="store_true")
    return parser.parse_args(argv)


def _build_default_state_backend() -> LedgerStateBackend:
    import django

    django.setup()
    return DjangoFalkorLedgerStateBackend()


def _load_ledger(path: Path) -> ValidationLedger:
    try:
        return ValidationLedger.from_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SystemExit("账本不存在或格式非法") from None


def main(
    argv: Sequence[str] | None = None,
    *,
    transport: Transport | None = None,
    state_backend: LedgerStateBackend | None = None,
    **_ignored: Any,
) -> int:
    args = _parse_args(argv)
    if args.verify_ledger and args.cleanup_ledger:
        raise SystemExit("--verify-ledger 与 --cleanup-ledger 不可同时使用")
    if args.verify_ledger:
        if state_backend is None:
            state_backend = _build_default_state_backend()
        ledger = _load_ledger(args.ledger)
        try:
            org_id = int(os.environ["CRV_ORG_ID"])
        except (KeyError, ValueError):
            raise SystemExit("CRV_ORG_ID 必须是正整数") from None
        result = state_backend.snapshot(ledger=ledger, org_id=org_id, expect_present=True)
        print(json.dumps(_redact({"verified": True, **result}), ensure_ascii=False, sort_keys=True))
        return 0
    if not args.base_url:
        raise SystemExit("必须提供 --base-url 或 CRV_BASE_URL")
    allowed_hosts = {host for host in os.environ.get("CRV_ALLOWED_HOSTS", "").split(",") if host}
    try:
        org_id = int(os.environ["CRV_ORG_ID"]) if "CRV_ORG_ID" in os.environ else None
    except ValueError:
        raise SystemExit("CRV_ORG_ID 必须是正整数") from None
    confirmed = os.environ.get("CRV_EXECUTE_CONFIRMED") == "1"
    if args.cleanup_ledger and not (args.execute and confirmed and os.environ.get("CRV_ALLOW_WRITE") == "1"):
        raise SystemExit("--cleanup-ledger 必须同时开启 --execute/CRV_EXECUTE_CONFIRMED/CRV_ALLOW_WRITE")
    ledger = _load_ledger(args.ledger) if args.cleanup_ledger else ValidationLedger.create()
    if args.cleanup_ledger and state_backend is None:
        state_backend = _build_default_state_backend()
    runner = HttpRunner(
        base_url=args.base_url,
        allowed_hosts=allowed_hosts,
        transport=transport or RequestsTransport(),
        ledger=ledger,
        ledger_path=args.ledger,
        execute=confirmed,
        cli_execute=args.execute,
        session_cookie=os.environ.get("CRV_SESSION_COOKIE"),
        management_api_secret=os.environ.get("CRV_MANAGEMENT_API_SECRET"),
        org_id=org_id,
        classification_id=os.environ.get("CRV_CLASSIFICATION_ID", "other"),
        state_backend=state_backend,
    )
    result = runner.cleanup() if args.cleanup_ledger else runner.run(mode=args.mode)
    print(
        json.dumps(
            _redact(
                result,
                (
                    os.environ.get("CRV_SESSION_COOKIE", ""),
                    os.environ.get("CRV_MANAGEMENT_API_SECRET", ""),
                ),
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
