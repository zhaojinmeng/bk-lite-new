"""自定义上报入站服务。

负责：
- _resolve_credential：按 token 查找有效凭据
- ingest：完整上报流程（鉴权 → 创建 Batch → 调用 merge → 写摘要）
"""

import hashlib
from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from apps.cmdb.services.auto_relation_reconcile import AutoRelationDispatchIntents, capture_auto_relation_dispatch_intents
from apps.cmdb_enterprise.custom_reporting.models import (
    CustomReportingBatch,
    CustomReportingCredential,
    CustomReportingOperation,
    CustomReportingOperationState,
    CustomReportingOutbox,
    CustomReportingOutboxState,
)
from apps.cmdb_enterprise.custom_reporting.services import merge_service, model_service, relation_service, schema_service
from apps.cmdb_enterprise.custom_reporting.services.operation_service import CustomReportingOperationService, OperationConflict
from apps.cmdb_enterprise.custom_reporting.services.resource_budget import RESOURCE_BUDGET
from apps.core.exceptions.base_app_exception import BaseAppException, UnauthorizedException, ValidationAppException

AUTO_RELATION_INSTANCE_EVENT = "auto_relation_instance"
AUTO_RELATION_RULE_EVENT = "auto_relation_rule"
AUTO_RELATION_EVENTS = (AUTO_RELATION_INSTANCE_EVENT, AUTO_RELATION_RULE_EVENT)


def _resolve_credential(token):
    """按 raw token 查找匹配的有效凭据，找不到则抛出 401 认证异常。

    Args:
        token: 上报方提供的原始 token 字符串。

    Returns:
        匹配的 CustomReportingCredential 实例（已 select_related task）。

    Raises:
        UnauthorizedException: token 为空或无匹配凭据。
    """
    if not token:
        raise UnauthorizedException("缺少上报令牌")

    token_hash = hashlib.sha256(str(token).encode("utf-8")).hexdigest()
    token_lookup = token_hash[: CustomReportingCredential.TOKEN_LOOKUP_PREFIX_LENGTH]
    for cred in CustomReportingCredential.objects.select_related("task").filter(is_enabled=True, token_lookup=token_lookup):
        if cred.matches_token(token):
            return cred

    raise UnauthorizedException("上报令牌无效或已作废")


def _existing_result(operation):
    operation.refresh_from_db()
    if operation.result_summary and operation.state in {
        CustomReportingOperationState.POST_ACTIONS_PENDING,
        CustomReportingOperationState.COMPLETED,
    }:
        return operation.result_summary
    if operation.state == CustomReportingOperationState.MANUAL_FAILED:
        raise OperationConflict("该幂等上报已进入人工处理，禁止盲目重放图写")
    return {
        "operation_id": str(operation.operation_id),
        "operation_status": operation.state,
    }


def _commit_ingest_result(operation, lease, task, batch, summary, intents):
    result = {"batch_id": batch.id, "summary": summary}
    with transaction.atomic():
        batch.summary = summary
        batch.status = CustomReportingBatch.STATUS_SUCCESS
        batch.save(update_fields=["summary", "status", "updated_at"])
        task.last_reported_at = timezone.now()
        task.save(update_fields=["last_reported_at", "updated_at"], sync_scopes=False)
        has_post_actions = bool(intents.instance_ids or intents.rule_ids)
        if has_post_actions:
            for instance_id in intents.instance_ids:
                CustomReportingOperationService.enqueue_outbox(
                    operation=operation,
                    event_type=AUTO_RELATION_INSTANCE_EVENT,
                    dedupe_key=f"instance:{int(instance_id)}",
                    payload={"instance_id": int(instance_id)},
                )
            for rule_id in intents.rule_ids:
                CustomReportingOperationService.enqueue_outbox(
                    operation=operation, event_type=AUTO_RELATION_RULE_EVENT, dedupe_key=f"rule:{rule_id}", payload={"rule_id": str(rule_id)},
                )
            transitioned = CustomReportingOperationService.transition(
                operation.operation_id,
                generation=lease.generation,
                owner_token=lease.owner_token,
                from_state=CustomReportingOperationState.GRAPH_APPLIED,
                to_state=CustomReportingOperationState.POST_ACTIONS_PENDING,
                result_summary=result,
            )
        else:
            transitioned = CustomReportingOperationService.finalize(
                operation.operation_id,
                generation=lease.generation,
                owner_token=lease.owner_token,
                state=CustomReportingOperationState.COMPLETED,
                result_summary=result,
            )
        if not transitioned:
            raise OperationConflict("ingest operation finalize CAS 已失效")
    return result


def dispatch_auto_relation(event: CustomReportingOutbox) -> None:
    """单事件直接执行一个趋同动作；成功后才允许 Outbox 标记 SUCCESS。"""

    from apps.cmdb.services.auto_relation_reconcile import AutoRelationRuleReconcileService

    if event.event_type == AUTO_RELATION_INSTANCE_EVENT:
        AutoRelationRuleReconcileService.reconcile_for_instance(int(event.payload["instance_id"]))
        return
    if event.event_type == AUTO_RELATION_RULE_EVENT:
        AutoRelationRuleReconcileService.full_sync_rule(str(event.payload["rule_id"]))
        return
    raise ValueError(f"未知 ingest Outbox 事件: {event.event_type}")


def consume_ingest_outbox(event_id, *, owner_token: str | None = None) -> bool:
    token = owner_token or uuid4().hex
    lease = CustomReportingOperationService.claim_outbox(event_id, owner_token=token)
    if lease is None:
        event = CustomReportingOutbox.objects.filter(event_id=event_id).first()
        return bool(event and event.state == CustomReportingOutboxState.SUCCESS)
    event = CustomReportingOutbox.objects.select_related("operation").get(event_id=event_id)
    try:
        dispatch_auto_relation(event)
    except Exception as error:  # noqa: BLE001 - 安全错误摘要由 OperationService 持久化
        CustomReportingOperationService.schedule_outbox_retry(
            event_id, owner_token=lease.owner_token, attempt_count=lease.attempt_count, error=error,
        )
        return False
    return CustomReportingOperationService.finalize_outbox_and_complete_operation(
        event_id, owner_token=lease.owner_token, attempt_count=lease.attempt_count,
    )


def process_ingest_outbox_batch(*, batch_size: int = 100) -> dict:
    event_ids = CustomReportingOperationService.ready_outbox_event_ids(event_types=AUTO_RELATION_EVENTS, batch_size=batch_size,)
    succeeded = sum(1 for event_id in event_ids if consume_ingest_outbox(event_id))
    return {"scanned": len(event_ids), "succeeded": succeeded, "failed": len(event_ids) - succeeded}


def recover_ingest_operation(operation_id) -> dict:
    """只用已持久化 graph fact 补 DB/Outbox；缺 fact 时禁止图重放。"""

    operation = CustomReportingOperation.objects.get(operation_id=operation_id)
    if operation.result_summary and operation.state in {
        CustomReportingOperationState.POST_ACTIONS_PENDING,
        CustomReportingOperationState.COMPLETED,
    }:
        if operation.state == CustomReportingOperationState.POST_ACTIONS_PENDING:
            CustomReportingOperationService.complete_post_actions(operation.operation_id)
        return operation.result_summary
    facts = operation.fact_snapshot or {}
    lease = CustomReportingOperationService.claim(operation.operation_id, owner_token=uuid4().hex, force=True,)
    if lease is None:
        return _existing_result(operation)
    if facts.get("phase") != "graph_applied" or not facts.get("batch_id") or not isinstance(facts.get("summary"), dict):
        result = {
            "batch_id": facts.get("batch_id"),
            "operation_status": CustomReportingOperationState.MANUAL_FAILED,
            "recovery": "fail_closed_new_key_required",
        }
        with transaction.atomic():
            if facts.get("batch_id"):
                CustomReportingBatch.objects.filter(id=facts["batch_id"]).update(
                    status=CustomReportingBatch.STATUS_FAILED, summary={"error": "图写入结果不确定，需使用新幂等键重新上报"},
                )
            if not CustomReportingOperationService.finalize(
                operation.operation_id,
                generation=lease.generation,
                owner_token=lease.owner_token,
                state=CustomReportingOperationState.MANUAL_FAILED,
                result_summary=result,
            ):
                raise OperationConflict("ingest 不确定图事实无法收敛人工终态")
        return result
    if not CustomReportingOperationService.transition(
        operation.operation_id,
        generation=lease.generation,
        owner_token=lease.owner_token,
        from_state=CustomReportingOperationState.CLAIMED,
        to_state=CustomReportingOperationState.GRAPH_APPLIED,
        fact_snapshot=facts,
    ):
        raise OperationConflict("ingest recovery graph fact CAS 已失效")
    auto_relation = facts.get("auto_relation") or {}
    intents = AutoRelationDispatchIntents(
        instance_ids=list(auto_relation.get("instance_ids") or []), rule_ids=list(auto_relation.get("rule_ids") or []),
    )
    batch = CustomReportingBatch.objects.get(id=facts["batch_id"])
    return _commit_ingest_result(operation, lease, batch.task, batch, facts["summary"], intents,)


def recover_ingest_operations_batch(*, batch_size: int = 100) -> dict:
    operation_ids = CustomReportingOperationService.ready_ingest_operation_ids(batch_size=batch_size)
    recovered = 0
    manual = 0
    for operation_id in operation_ids:
        try:
            recover_ingest_operation(operation_id)
            recovered += 1
        except OperationConflict:
            manual += 1
    return {"scanned": len(operation_ids), "recovered": recovered, "manual": manual}


def ingest(token: str, payload: dict, operator: str = "custom_reporting", *, idempotency_key: str | None = None,) -> dict:
    """处理一次自定义上报请求。

    Args:
        token: 上报令牌（Bearer token 字符串）。
        payload: 请求体字典，含 instances / relations 列表。
        operator: 操作人标识，写入变更记录。

    Returns:
        dict with keys: batch_id, summary.

    Raises:
        UnauthorizedException: token 无效。
        其他异常：透传，batch 状态置为 FAILED。
    """
    cred = _resolve_credential(token)
    task = cred.task
    RESOURCE_BUDGET.validate_ingest_payload(payload)

    if task.sync_status != task.SYNC_ACTIVE:
        raise BaseAppException("任务尚未完成同步，当前状态不可上报")
    if not task.is_enabled:
        raise BaseAppException("任务已停用")

    instances_supplied = "instances" in payload
    raw_instances = payload.get("instances")
    if task.config.get("cleanup_strategy") == "snapshot":
        if not instances_supplied:
            raise ValidationAppException("snapshot 上报必须显式提供 instances")
        if raw_instances == [] and payload.get("snapshot_authoritative") is not True:
            raise ValidationAppException("空 snapshot 必须显式声明 snapshot_authoritative=true")

    instances = raw_instances or []
    relations = payload.get("relations") or []
    model_id = task.config.get("model_id")
    compiled_schema = schema_service.compile_task_schema(task.config)
    from apps.cmdb.services.model import ModelManage

    attrs = ModelManage.search_model_attr(model_id)
    instance_plan = schema_service.compile_instance_plan(task.config, instances, attrs, compiled_schema=compiled_schema,)
    instances = instance_plan.instances
    relation_plan = relation_service.compile_plan(task, relations)

    # HTTP 边界强制调用方提供稳定 key；直接服务调用保留唯一 key 以兼容内部任务，
    # 但不会让两个独立调用被错误地永久去重。
    normalized_key = str(idempotency_key).strip() if idempotency_key is not None else uuid4().hex
    operation_start = CustomReportingOperationService.start_ingest(task_id=task.id, idempotency_key=normalized_key, payload=payload,)
    operation = operation_start.operation
    if operation_start.reused:
        return _existing_result(operation)
    lease = CustomReportingOperationService.claim(operation.operation_id, owner_token=uuid4().hex, force=True)
    if lease is None:
        return _existing_result(operation)
    batch = None
    try:
        pregraph_fact = {"phase": "pregraph"}
        if not CustomReportingOperationService.record_fact_snapshot(
            operation.operation_id,
            generation=lease.generation,
            owner_token=lease.owner_token,
            state=CustomReportingOperationState.CLAIMED,
            fact_snapshot=pregraph_fact,
        ):
            raise OperationConflict("ingest pregraph fact CAS 已失效")

        prepared_batch = None
        with transaction.atomic():
            cred.mark_used()
            prepared_batch = CustomReportingBatch.objects.create(task=task, status=CustomReportingBatch.STATUS_RUNNING,)
            graph_uncertain_fact = {"phase": "graph_may_have_started", "batch_id": prepared_batch.id}
            if not CustomReportingOperationService.transition(
                operation.operation_id,
                generation=lease.generation,
                owner_token=lease.owner_token,
                from_state=CustomReportingOperationState.CLAIMED,
                to_state=CustomReportingOperationState.GRAPH_WRITING,
                fact_snapshot=graph_uncertain_fact,
            ):
                raise OperationConflict("ingest graph transition CAS 已失效")
        batch = prepared_batch

        # 快速模式：将新字段自动追加为未定型（str）属性，并记录字段登记元数据
        if task.config.get("mode") == "quick" and model_id:
            from apps.cmdb_enterprise.custom_reporting.services import field_service

            added = model_service.register_model_fields(model_id, instances, username=operator, declared_attr_ids=instance_plan.declared_attr_ids,)
            field_service.record_registrations(model_id, added or [], instances)

        with capture_auto_relation_dispatch_intents() as dispatch_intents:
            merge_result = merge_service.merge_instances(task, model_id, instances, operator, instance_plan=instance_plan,)
            if merge_result.get("errors", 0) > 0:
                raise BaseAppException("实例合并部分失败")

            batch_index = merge_result.get("index", {})
            rel_result = relation_service.process(task, relations, batch_index, operator, relation_plan,)

            # 快照清理策略：删除本次未覆盖的旧实例（或创建人工审核）
            deleted_count = merge_result.get("deleted", 0)
            if task.config.get("cleanup_strategy") == "snapshot":
                from apps.cmdb_enterprise.custom_reporting.services import cleanup_service

                old_ids = [o["_id"] for o in merge_result.get("old_data", []) if o.get("_id") is not None]
                snap = cleanup_service.apply_snapshot(
                    task,
                    batch,
                    old_ids=old_ids,
                    covered_ids=merge_result.get("covered_ids", []),
                    operator=operator,
                    snapshot_authoritative=payload.get("snapshot_authoritative") is True,
                )
                deleted_count = snap["deleted"]

        summary = {
            "instances_received": len(instances),
            "relations_received": len(relations),
            "created": merge_result.get("created", 0),
            "updated": merge_result.get("updated", 0),
            "deleted": deleted_count,
            "errors": merge_result.get("errors", 0),
            "pending_relations": rel_result["pending"],
        }

        facts = {
            "phase": "graph_applied",
            "batch_id": batch.id,
            "summary": summary,
            "auto_relation": {"instance_ids": list(dispatch_intents.instance_ids), "rule_ids": list(dispatch_intents.rule_ids)},
        }
        if not CustomReportingOperationService.transition(
            operation.operation_id,
            generation=lease.generation,
            owner_token=lease.owner_token,
            from_state=CustomReportingOperationState.GRAPH_WRITING,
            to_state=CustomReportingOperationState.GRAPH_APPLIED,
            fact_snapshot=facts,
        ):
            raise OperationConflict("ingest graph fact CAS 已失效")

        result = _commit_ingest_result(operation, lease, task, batch, summary, dispatch_intents)

        return result

    except Exception as e:  # noqa: BLE001 - 失败原因落库便于排查，异常继续向上抛
        if batch is not None:
            batch.status = CustomReportingBatch.STATUS_FAILED
            batch.summary = {"error": str(e) or e.__class__.__name__}
            batch.save()
        operation.refresh_from_db()
        if operation.state == CustomReportingOperationState.GRAPH_APPLIED and operation.fact_snapshot:
            CustomReportingOperationService.schedule_retry(
                operation.operation_id, generation=lease.generation, owner_token=lease.owner_token, error=e, base_delay_seconds=1,
            )
        else:
            CustomReportingOperationService.finalize(
                operation.operation_id,
                generation=lease.generation,
                owner_token=lease.owner_token,
                state=CustomReportingOperationState.MANUAL_FAILED,
                result_summary={
                    "batch_id": batch.id if batch is not None else None,
                    "operation_status": CustomReportingOperationState.MANUAL_FAILED,
                    "recovery": "fail_closed_new_key_required",
                },
            )
        raise
