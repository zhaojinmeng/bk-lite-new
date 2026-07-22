"""自定义上报关系服务。

负责：
- _resolve_instance: 通过 OwnedInstanceRef 在任务 owner/team 范围内解析实例
- _create_edge: 幂等地创建实例关联边
- _src_id: 从 source 字段解析出源实例 _id（批次内直接引用或批次索引查找）
- process: 处理本次上报中的 relations，立即创建可创建的，将缺少目标的写入 pending
- backfill: 尝试补建 pending 中的关系
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from time import sleep

from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.models import F, Q
from django.utils.timezone import now

from apps.cmdb.constants.constants import INSTANCE, INSTANCE_ASSOCIATION
from apps.cmdb.graph.drivers.graph_client import GraphClient
from apps.cmdb.services.instance import InstanceManage
from apps.cmdb.services.model import ModelManage
from apps.cmdb_enterprise.custom_reporting.models import CustomReportingPendingRelation, PendingRelationDelivery
from apps.cmdb_enterprise.custom_reporting.services import ownership_service
from apps.cmdb_enterprise.custom_reporting.services.ownership_service import OwnedInstanceRef
from apps.cmdb_enterprise.custom_reporting.services.resource_budget import RESOURCE_BUDGET
from apps.cmdb_enterprise.custom_reporting.services.value_objects import GraphId
from apps.core.exceptions.base_app_exception import BaseAppException

MAX_PENDING_DELIVERY_BATCH_SIZE = 100
PENDING_DELIVERY_LEASE_SECONDS = 60
PENDING_DELIVERY_RETRY_BASE_SECONDS = 5
PENDING_DELIVERY_RETRY_MAX_SECONDS = 300
SQLITE_LOCK_RETRY_DELAYS = (0.0, 0.005, 0.01, 0.02, 0.04, 0.08)


class PermanentPendingRelationDeliveryError(Exception):
    """待投递关系中已确定无法通过重试恢复的载荷或契约错误。"""


@dataclass(frozen=True)
class RelationPlanItem:
    relation: dict
    association: dict
    source_model_id: str
    target_model_id: str


@dataclass(frozen=True)
class RelationPlan:
    items: tuple[RelationPlanItem, ...]


def _load_association(model_asst_id: str) -> dict:
    asso = ModelManage.model_association_info_search(model_asst_id)
    if not asso:
        raise BaseAppException(f"关联类型不存在: {model_asst_id}")
    ModelManage.validate_model_association_mapping(asso)
    return asso


def compile_plan(task, relations: list) -> RelationPlan:
    task_model_id = task.config.get("model_id", "")
    items = []
    for relation in relations:
        source = relation.get("source", {})
        target = relation.get("target", {})
        source_model_id = source.get("model_id")
        target_model_id = target.get("model_id")
        if source_model_id != task_model_id:
            raise BaseAppException(f"关系源模型必须与任务模型一致: {source_model_id!r} != {task_model_id!r}")

        model_asst_id = relation.get("asst_id", "")
        association = _load_association(model_asst_id)
        if association.get("model_asst_id") not in {None, model_asst_id}:
            raise BaseAppException("关系关联定义与 model_asst_id 不一致")
        if association.get("src_model_id") != source_model_id:
            raise BaseAppException("关系源模型与关联定义不一致")
        if association.get("dst_model_id") != target_model_id:
            raise BaseAppException("关系目标模型与关联定义不一致")
        if "mapping" in relation and relation["mapping"] != association["mapping"]:
            raise BaseAppException("关系 mapping 与关联定义不一致")

        association_snapshot = {
            "model_asst_id": model_asst_id,
            "src_model_id": association.get("src_model_id"),
            "dst_model_id": association.get("dst_model_id"),
            "mapping": association.get("mapping"),
            "asst_id": association.get("asst_id"),
        }
        items.append(
            RelationPlanItem(relation=relation, association=association_snapshot, source_model_id=source_model_id, target_model_id=target_model_id,)
        )
    return RelationPlan(items=tuple(items))


def _resolve_instance(task, ref: OwnedInstanceRef):
    """在当前任务 owner/team 范围内解析单个实例端点。"""
    return ownership_service.resolve_owned_instance(task, ref, graph_client_cls=GraphClient,)


def _create_edge(
    src_id: int, dst_id: int, model_asst_id: str, operator: str, expected_association: dict | None = None,
):
    """幂等地创建实例关联边。

    实例关联边必须带上 src_model_id/dst_model_id/asst_id（取自模型关联类型定义），
    否则建边后回查格式化会因缺字段报错。关联类型不存在时抛出明确异常。
    若边已存在（message 含 "repetition"），静默忽略；其他异常向上抛。
    """
    asso = expected_association or _load_association(model_asst_id)
    data = {
        "src_inst_id": src_id,
        "dst_inst_id": dst_id,
        "model_asst_id": model_asst_id,
        "src_model_id": asso.get("src_model_id"),
        "dst_model_id": asso.get("dst_model_id"),
        "asst_id": asso.get("asst_id"),
    }
    InstanceManage.instance_association_ensure(data, operator, expected_association=asso)


def _src_id(source: dict, batch_index: dict, identity_keys: list):
    """从 source 字段解析源实例 _id。

    优先使用直接指定的 _id；否则以 (model_id, identity_tuple) 在
    batch_index 中查找（batch_index 由 merge_service 构建）。

    identity_tuple 按 identity_keys 顺序构造，与 merge_service 的索引键对齐，
    避免因 payload 字典字段顺序不同导致查找失败。
    """
    if "_id" in source:
        return source["_id"]
    model_id = source.get("model_id")
    identity = source.get("identity", {})
    key = (model_id, tuple(str(identity.get(k)) for k in identity_keys))
    return batch_index.get(key)


def _graph_id(value) -> int:
    return GraphId(value).value


def _resolved_id(ref: OwnedInstanceRef, instance: dict | None) -> int | None:
    if instance is None:
        return None
    if ref.instance_id is not None:
        return ref.instance_id
    return _graph_id(instance.get("_id"))


def _has_resolvable_endpoint_identity(ref: OwnedInstanceRef) -> bool:
    return ref.instance_id is not None or any(
        value not in (None, "")
        for _key, value in ref.identity
    )


def _canonical_relation_payload(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _payload_hash(payload: dict) -> str:
    return hashlib.sha256(_canonical_relation_payload(payload).encode("utf-8")).hexdigest()


def _relation_fingerprint(payload: dict) -> str:
    return hashlib.sha256(_canonical_relation_payload(payload).encode("utf-8")).hexdigest()


def _is_sqlite_lock_error(error: OperationalError) -> bool:
    return connection.vendor == "sqlite" and any(marker in str(error).lower() for marker in ("locked", "busy"))


def _validate_delivery_reuse(delivery: PendingRelationDelivery, *, payload_hash: str, relation_payload: dict) -> None:
    if delivery.payload_hash != payload_hash or _canonical_relation_payload(delivery.relation_payload) != _canonical_relation_payload(
        relation_payload
    ):
        raise BaseAppException("relation fingerprint payload 冲突")


def enqueue_pending_delivery(
    *, task, relation_payload: dict, source_model_id: str, target_model_id: str, pending_relation: CustomReportingPendingRelation | None = None,
) -> PendingRelationDelivery:
    """按类型敏感 canonical fingerprint 幂等持久化单条待补关系。"""

    fingerprint = _relation_fingerprint(relation_payload)
    payload_hash = _payload_hash(relation_payload)
    last_lock_error = None
    for delay in SQLITE_LOCK_RETRY_DELAYS:
        if delay:
            sleep(delay)
        try:
            with transaction.atomic():
                delivery, created = PendingRelationDelivery.objects.get_or_create(
                    task=task,
                    fingerprint=fingerprint,
                    defaults={
                        "payload_hash": payload_hash,
                        "source_model_id": source_model_id,
                        "target_model_id": target_model_id,
                        "relation_payload": relation_payload,
                    },
                )
                if not created:
                    _validate_delivery_reuse(delivery, payload_hash=payload_hash, relation_payload=relation_payload)
                    if pending_relation is not None and pending_relation.id != delivery.pending_relation_id:
                        pending_relation.delete()
                    return delivery
                if pending_relation is None:
                    pending_relation = CustomReportingPendingRelation.objects.create(
                        task=task, source_model_id=source_model_id, target_model_id=target_model_id, relation_payload=relation_payload,
                    )
                delivery.pending_relation = pending_relation
                delivery.save(update_fields=["pending_relation", "updated_at"])
                return delivery
        except IntegrityError:
            winner = PendingRelationDelivery.objects.filter(task=task, fingerprint=fingerprint).first()
            if winner is not None:
                _validate_delivery_reuse(winner, payload_hash=payload_hash, relation_payload=relation_payload)
                return winner
            raise
        except OperationalError as error:
            if not _is_sqlite_lock_error(error):
                raise
            last_lock_error = error
    raise last_lock_error


def claim_pending_deliveries(
    *,
    owner_token: str,
    batch_size: int = MAX_PENDING_DELIVERY_BATCH_SIZE,
    lease_seconds: int = PENDING_DELIVERY_LEASE_SECONDS,
    task_id: int | None = None,
) -> list[PendingRelationDelivery]:
    try:
        RESOURCE_BUDGET.ensure_time_remaining()
    except BaseAppException:
        return []
    current_time = now()
    limit = min(RESOURCE_BUDGET.clamp_batch_size(batch_size), MAX_PENDING_DELIVERY_BATCH_SIZE)
    ready = PendingRelationDelivery.objects.filter(
        Q(state__in=[PendingRelationDelivery.STATE_PENDING, PendingRelationDelivery.STATE_RETRY], next_retry_at__lte=current_time)
        | Q(state=PendingRelationDelivery.STATE_SENDING, lease_expires_at__lte=current_time)
    )
    if task_id is not None:
        ready = ready.filter(task_id=task_id)
    candidate_ids = list(ready.order_by("next_retry_at", "id").values_list("id", flat=True)[:limit])
    claimed = []
    sqlite_lock_retry_index = 1
    for delivery_id in candidate_ids:
        try:
            RESOURCE_BUDGET.ensure_time_remaining()
        except BaseAppException:
            break
        claimable = PendingRelationDelivery.objects.filter(id=delivery_id).filter(
            Q(state__in=[PendingRelationDelivery.STATE_PENDING, PendingRelationDelivery.STATE_RETRY], next_retry_at__lte=current_time)
            | Q(state=PendingRelationDelivery.STATE_SENDING, lease_expires_at__lte=current_time)
        )
        while True:
            try:
                updated = claimable.update(
                    state=PendingRelationDelivery.STATE_SENDING,
                    owner_token=owner_token,
                    lease_expires_at=current_time + timedelta(seconds=max(1, int(lease_seconds))),
                    generation=F("generation") + 1,
                    attempt_count=F("attempt_count") + 1,
                    updated_at=current_time,
                )
                break
            except OperationalError as error:
                if not _is_sqlite_lock_error(error) or sqlite_lock_retry_index >= len(SQLITE_LOCK_RETRY_DELAYS):
                    raise
                sleep(SQLITE_LOCK_RETRY_DELAYS[sqlite_lock_retry_index])
                sqlite_lock_retry_index += 1
        if updated:
            delivery_query = PendingRelationDelivery.objects.filter(
                id=delivery_id,
                state=PendingRelationDelivery.STATE_SENDING,
                owner_token=owner_token,
                generation__gt=0,
            )
            while True:
                try:
                    delivery = delivery_query.first()
                    break
                except OperationalError as error:
                    if not _is_sqlite_lock_error(error) or sqlite_lock_retry_index >= len(SQLITE_LOCK_RETRY_DELAYS):
                        raise
                    sleep(SQLITE_LOCK_RETRY_DELAYS[sqlite_lock_retry_index])
                    sqlite_lock_retry_index += 1
            if delivery is not None:
                claimed.append(delivery)
    return claimed


def _owned_sending(delivery_id, *, owner_token: str, generation: int):
    return PendingRelationDelivery.objects.filter(
        id=delivery_id, state=PendingRelationDelivery.STATE_SENDING, owner_token=owner_token, generation=generation, lease_expires_at__gt=now(),
    )


def ack_pending_delivery(delivery_id, *, owner_token: str, generation: int) -> bool:
    with transaction.atomic():
        delivery = _owned_sending(delivery_id, owner_token=owner_token, generation=generation).first()
        if delivery is None:
            return False
        updated = _owned_sending(delivery_id, owner_token=owner_token, generation=generation).update(
            state=PendingRelationDelivery.STATE_SUCCESS, owner_token="", lease_expires_at=None, last_error="", updated_at=now(),
        )
        if not updated:
            return False
        if delivery.pending_relation_id is not None:
            CustomReportingPendingRelation.objects.filter(id=delivery.pending_relation_id).delete()
        return True


def retry_pending_delivery(delivery_id, *, owner_token: str, generation: int, error: Exception) -> bool:
    delivery = _owned_sending(delivery_id, owner_token=owner_token, generation=generation).first()
    if delivery is None:
        return False
    delay = min(PENDING_DELIVERY_RETRY_BASE_SECONDS * (2 ** max(delivery.attempt_count - 1, 0)), PENDING_DELIVERY_RETRY_MAX_SECONDS,)
    return bool(
        _owned_sending(delivery_id, owner_token=owner_token, generation=generation).update(
            state=PendingRelationDelivery.STATE_RETRY,
            owner_token="",
            lease_expires_at=None,
            next_retry_at=now() + timedelta(seconds=delay),
            last_error=str(error)[:1000],
            updated_at=now(),
        )
    )


def dead_letter_pending_delivery(delivery_id, *, owner_token: str, generation: int, error: Exception) -> bool:
    return bool(
        _owned_sending(delivery_id, owner_token=owner_token, generation=generation).update(
            state=PendingRelationDelivery.STATE_DEAD_LETTER, owner_token="", lease_expires_at=None, last_error=str(error)[:1000], updated_at=now(),
        )
    )


def _deliver_pending_relation(delivery: PendingRelationDelivery, operator: str) -> None:
    try:
        item = compile_plan(delivery.task, [delivery.relation_payload]).items[0]
    except BaseAppException as error:
        raise PermanentPendingRelationDeliveryError(str(error)) from error
    if delivery.source_model_id != item.source_model_id:
        raise PermanentPendingRelationDeliveryError("pending 关系源模型与任务模型不一致")
    if delivery.target_model_id != item.target_model_id:
        raise PermanentPendingRelationDeliveryError("pending 关系目标模型与载荷不一致")
    try:
        source_ref = OwnedInstanceRef.from_payload(item.relation.get("source", {}))
        target_ref = OwnedInstanceRef.from_payload(item.relation.get("target", {}))
    except (TypeError, ValueError, BaseAppException) as error:
        raise PermanentPendingRelationDeliveryError(str(error)) from error
    if not _has_resolvable_endpoint_identity(source_ref) or not _has_resolvable_endpoint_identity(target_ref):
        raise PermanentPendingRelationDeliveryError("pending relation endpoint lacks _id or non-empty identity")
    try:
        source_id = _resolved_id(source_ref, _resolve_instance(delivery.task, source_ref))
        target_id = _resolved_id(target_ref, _resolve_instance(delivery.task, target_ref))
    except BaseAppException as error:
        raise PermanentPendingRelationDeliveryError(str(error)) from error
    if source_id is None or target_id is None:
        raise RuntimeError("pending relation endpoint is not available")
    _create_edge(source_id, target_id, item.relation["asst_id"], operator, item.association)


def process_pending_delivery_batch(
    *, batch_size: int = MAX_PENDING_DELIVERY_BATCH_SIZE, owner_token: str, operator: str, task_id: int | None = None,
) -> dict:
    deliveries = claim_pending_deliveries(owner_token=owner_token, batch_size=batch_size, task_id=task_id)
    result = {"claimed": len(deliveries), "succeeded": 0, "retried": 0, "dead_lettered": 0}
    for delivery in deliveries:
        try:
            _deliver_pending_relation(delivery, operator)
            if ack_pending_delivery(delivery.id, owner_token=owner_token, generation=delivery.generation):
                result["succeeded"] += 1
        except PermanentPendingRelationDeliveryError as error:
            if dead_letter_pending_delivery(delivery.id, owner_token=owner_token, generation=delivery.generation, error=error):
                result["dead_lettered"] += 1
        except Exception as error:
            if retry_pending_delivery(delivery.id, owner_token=owner_token, generation=delivery.generation, error=error):
                result["retried"] += 1
    return result


def process(task, relations: list, batch_index: dict, operator: str, relation_plan: RelationPlan | None = None,) -> dict:
    """处理本次上报的关系列表。

    Args:
        task: CustomReportingTask 实例。
        relations: 上报 payload 中的 relations 列表，每项形如
            {"source": {...}, "target": {"model_id": ..., "identity": {...}}, "asst_id": ...}。
        batch_index: merge_service 返回的 index，键为 (model_id, identity_tuple)，值为 _id。
        operator: 操作人标识。

    Returns:
        dict with key "pending": 本次写入 pending 表的数量。
    """
    pending_count = 0
    identity_keys = task.config.get("identity_keys") or []
    plan = relation_plan or compile_plan(task, relations)

    for item in plan.items:
        rel = item.relation
        source = dict(rel.get("source", {}))
        if "_id" not in source:
            batch_src_id = _src_id(source, batch_index, identity_keys)
            if batch_src_id is not None:
                source["_id"] = batch_src_id
        source_ref = OwnedInstanceRef.from_payload(source)
        source_inst = _resolve_instance(task, source_ref)
        src_id = _resolved_id(source_ref, source_inst)

        target_ref = OwnedInstanceRef.from_payload(rel.get("target", {}))
        target_inst = _resolve_instance(task, target_ref)
        target_id = _resolved_id(target_ref, target_inst)

        if src_id is not None and target_id is not None:
            _create_edge(
                src_id, target_id, rel["asst_id"], operator, item.association,
            )
        else:
            enqueue_pending_delivery(
                task=task, source_model_id=item.source_model_id, target_model_id=item.target_model_id, relation_payload=rel,
            )
            pending_count += 1

    return {"pending": pending_count}


def backfill(task, operator: str, batch_size: int = MAX_PENDING_DELIVERY_BATCH_SIZE) -> int:
    """兼容入口：有界迁移 legacy pending 后，交给逐条隔离的 delivery worker。"""

    legacy = CustomReportingPendingRelation.objects.filter(task=task, delivery__isnull=True).order_by("id")[:MAX_PENDING_DELIVERY_BATCH_SIZE]
    for pending in legacy:
        enqueue_pending_delivery(
            task=task,
            source_model_id=pending.source_model_id,
            target_model_id=pending.target_model_id,
            relation_payload=pending.relation_payload,
            pending_relation=pending,
        )
    result = process_pending_delivery_batch(batch_size=batch_size, owner_token=f"legacy-backfill-{task.id}", operator=operator, task_id=task.id,)
    return result["succeeded"]
