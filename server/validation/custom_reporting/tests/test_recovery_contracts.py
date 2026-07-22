from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.utils.timezone import now

from apps.cmdb.models.change_record import ChangeRecord
from apps.cmdb.models.operation import ChangeRecordMirrorOutbox
from apps.cmdb.services.change_record_mirror import ChangeRecordMirrorService
from apps.cmdb_enterprise.custom_reporting import models as reporting_models
from apps.cmdb_enterprise.custom_reporting.models import (
    CustomReportingBatch,
    CustomReportingCleanupReview,
    CustomReportingCredential,
    CustomReportingPendingRelation,
)
from apps.cmdb_enterprise.custom_reporting.services import (
    cleanup_service,
    ingest_service,
    model_service,
    reconcile_service,
    relation_service,
    task_service,
)
from apps.cmdb_enterprise.custom_reporting.services.operation_service import CustomReportingOperationService, OperationConflict
from apps.core.exceptions.base_app_exception import BaseAppException
from validation.custom_reporting.tests.factories import create_token_task, unique_crval_name
from validation.custom_reporting.tests.test_runtime_contracts import KnownProductDefect


def _merge_result(**overrides):
    result = {
        "created": 0,
        "updated": 0,
        "deleted": 0,
        "errors": 0,
        "covered_ids": [],
        "old_data": [],
        "index": {},
    }
    result.update(overrides)
    return result


def _operations_for_scope(scope_key):
    operation_model = getattr(reporting_models, "CustomReportingOperation", None)
    if operation_model is None:
        return []
    return list(operation_model.objects.filter(scope_key=scope_key))


def _deliveries_for_pending(task, pending):
    delivery_model = getattr(reporting_models, "PendingRelationDelivery", None)
    if delivery_model is None:
        return []
    fields = {field.name for field in delivery_model._meta.get_fields()}
    if "pending_relation" in fields:
        return list(delivery_model.objects.filter(pending_relation=pending))
    return list(delivery_model.objects.filter(task=task))


@pytest.mark.django_db
@pytest.mark.parametrize(
    "authoritative_payload", [{}, {"snapshot_authoritative": False}], ids=["missing", "false"],
)
def test_empty_snapshot_requires_per_request_authoritative_before_side_effects(
    api_client, monkeypatch, authoritative_payload,
):
    token_task = create_token_task(mode="quick", cleanup_strategy="snapshot")
    # 长期任务配置不能替代本次请求的显式确认。
    token_task.task.config["snapshot_authoritative"] = True
    token_task.task.save(update_fields=["config", "updated_at"], sync_scopes=False)
    credential = token_task.task.credentials.get()
    register_fields = Mock(return_value=[])
    merge_instances = Mock(return_value=_merge_result(old_data=[{"_id": 10}, {"_id": 11}]))
    graph_delete = Mock()
    monkeypatch.setattr(
        "apps.cmdb.services.model.ModelManage.search_model_attr", lambda model_id: [{"attr_id": "inst_name", "attr_type": "str"}],
    )
    monkeypatch.setattr(ingest_service.model_service, "register_model_fields", register_fields)
    monkeypatch.setattr(ingest_service.merge_service, "merge_instances", merge_instances)
    monkeypatch.setattr(ingest_service.relation_service, "process", lambda *args: {"pending": 0})
    monkeypatch.setattr(cleanup_service, "_owned_instance_ids", lambda task, ids: list(ids))
    monkeypatch.setattr(cleanup_service, "_delete_instances", graph_delete)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token_task.raw_token}")

    response = api_client.post(
        "/api/v1/cmdb/api/custom_reporting/ingest/",
        {"instances": [], **authoritative_payload},
        format="json",
        HTTP_IDEMPOTENCY_KEY="empty-snapshot-rejected",
    )

    assert response.status_code == 400
    credential.refresh_from_db()
    assert credential.last_used_at is None
    assert not CustomReportingBatch.objects.filter(task=token_task.task).exists()
    register_fields.assert_not_called()
    merge_instances.assert_not_called()
    graph_delete.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "raw_authoritative", ["true", "yes", "1", "on", 1, 1.0], ids=["true-string", "yes-string", "one-string", "on-string", "integer", "float"],
)
def test_empty_snapshot_rejects_non_boolean_authoritative_before_side_effects(
    api_client, monkeypatch, raw_authoritative,
):
    token_task = create_token_task(mode="quick", cleanup_strategy="snapshot")
    credential = token_task.task.credentials.get()
    register_fields = Mock(return_value=[])
    merge_instances = Mock(return_value=_merge_result(old_data=[{"_id": 10}, {"_id": 11}]))
    graph_delete = Mock()
    monkeypatch.setattr(
        "apps.cmdb.services.model.ModelManage.search_model_attr", lambda model_id: [{"attr_id": "inst_name", "attr_type": "str"}],
    )
    monkeypatch.setattr(ingest_service.model_service, "register_model_fields", register_fields)
    monkeypatch.setattr(ingest_service.merge_service, "merge_instances", merge_instances)
    monkeypatch.setattr(ingest_service.relation_service, "process", lambda *args: {"pending": 0})
    monkeypatch.setattr(cleanup_service, "_owned_instance_ids", lambda task, ids: list(ids))
    monkeypatch.setattr(cleanup_service, "_delete_instances", graph_delete)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token_task.raw_token}")

    response = api_client.post(
        "/api/v1/cmdb/api/custom_reporting/ingest/",
        {"instances": [], "snapshot_authoritative": raw_authoritative},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"invalid-authoritative-{raw_authoritative!r}",
    )

    assert response.status_code == 400
    credential.refresh_from_db()
    assert credential.last_used_at is None
    assert not CustomReportingBatch.objects.filter(task=token_task.task).exists()
    register_fields.assert_not_called()
    merge_instances.assert_not_called()
    graph_delete.assert_not_called()


@pytest.mark.django_db
def test_authoritative_empty_snapshot_always_creates_review_at_zero_threshold(
    api_client, monkeypatch,
):
    token_task = create_token_task(cleanup_strategy="snapshot")
    merge_instances = Mock(return_value=_merge_result(old_data=[{"_id": 10}, {"_id": 11}]))
    graph_delete = Mock()
    monkeypatch.setattr(
        "apps.cmdb.services.model.ModelManage.search_model_attr", lambda model_id: [{"attr_id": "inst_name", "attr_type": "str"}],
    )
    monkeypatch.setattr(ingest_service.merge_service, "merge_instances", merge_instances)
    monkeypatch.setattr(ingest_service.relation_service, "process", lambda *args: {"pending": 0})
    monkeypatch.setattr(cleanup_service, "_owned_instance_ids", lambda task, ids: list(ids))
    monkeypatch.setattr(cleanup_service, "_delete_instances", graph_delete)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token_task.raw_token}")

    response = api_client.post(
        "/api/v1/cmdb/api/custom_reporting/ingest/",
        {"instances": [], "snapshot_authoritative": True},
        format="json",
        HTTP_IDEMPOTENCY_KEY="authoritative-empty-snapshot",
    )

    assert response.status_code == 200
    batch = CustomReportingBatch.objects.get(task=token_task.task)
    assert batch.status == CustomReportingBatch.STATUS_SUCCESS
    assert batch.summary["deleted"] == 0
    graph_delete.assert_not_called()
    assert CustomReportingCleanupReview.objects.filter(batch=batch, status=CustomReportingCleanupReview.STATUS_PENDING,).exists()


@pytest.mark.django_db
def test_snapshot_payload_without_instances_is_rejected_before_side_effects(
    api_client, monkeypatch,
):
    token_task = create_token_task(mode="quick", cleanup_strategy="snapshot")
    credential = token_task.task.credentials.get()
    register_fields = Mock(return_value=[])
    merge_instances = Mock(return_value=_merge_result(old_data=[{"_id": 10}]))
    graph_delete = Mock()
    monkeypatch.setattr(
        "apps.cmdb.services.model.ModelManage.search_model_attr", lambda model_id: [{"attr_id": "inst_name", "attr_type": "str"}],
    )
    monkeypatch.setattr(ingest_service.model_service, "register_model_fields", register_fields)
    monkeypatch.setattr(ingest_service.merge_service, "merge_instances", merge_instances)
    monkeypatch.setattr(ingest_service.relation_service, "process", lambda *args: {"pending": 0})
    monkeypatch.setattr(cleanup_service, "_owned_instance_ids", lambda task, ids: list(ids))
    monkeypatch.setattr(cleanup_service, "_delete_instances", graph_delete)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token_task.raw_token}")

    response = api_client.post(
        "/api/v1/cmdb/api/custom_reporting/ingest/",
        {"relations": []},
        format="json",
        HTTP_IDEMPOTENCY_KEY="missing-snapshot-instances",
    )

    assert response.status_code == 400
    credential.refresh_from_db()
    assert credential.last_used_at is None
    assert not CustomReportingBatch.objects.filter(task=token_task.task).exists()
    register_fields.assert_not_called()
    merge_instances.assert_not_called()
    graph_delete.assert_not_called()


@pytest.mark.django_db
def test_backfill_dead_letters_legacy_association_without_mapping_and_does_not_retry(monkeypatch,):
    token_task = create_token_task()
    model_id = token_task.task.config["model_id"]
    target_model = unique_crval_name("legacy_target")
    association_id = unique_crval_name("legacy_association")
    pending = CustomReportingPendingRelation.objects.create(
        task=token_task.task,
        source_model_id=model_id,
        target_model_id=target_model,
        relation_payload={
            "source": {"_id": 1, "model_id": model_id},
            "target": {"model_id": target_model, "identity": {"inst_name": "target"}},
            "asst_id": association_id,
        },
    )
    monkeypatch.setattr(
        "apps.cmdb.services.model.ModelManage.model_association_info_search",
        lambda model_asst_id: {"model_asst_id": model_asst_id, "src_model_id": model_id, "dst_model_id": target_model, "asst_id": "legacy"},
    )
    monkeypatch.setattr(
        relation_service, "_resolve_instance", lambda model_id, identity: {"_id": 2},
    )
    edge_write = Mock()
    monkeypatch.setattr(
        "apps.cmdb.services.instance.InstanceManage.instance_association_create", edge_write,
    )

    assert relation_service.backfill(token_task.task, "crval_validator") == 0

    edge_write.assert_not_called()
    assert CustomReportingPendingRelation.objects.filter(id=pending.id).exists()
    delivery = _deliveries_for_pending(token_task.task, pending)[0]
    assert delivery.state == "dead_letter"
    assert delivery.attempt_count == 1
    assert relation_service.backfill(token_task.task, "crval_validator") == 0
    delivery.refresh_from_db()
    assert delivery.attempt_count == 1


@pytest.mark.django_db
def test_backfill_dead_letters_association_endpoint_mismatch_before_resolution_and_does_not_retry(monkeypatch,):
    token_task = create_token_task()
    model_id = token_task.task.config["model_id"]
    target_model = unique_crval_name("target")
    association_id = unique_crval_name("association")
    pending = CustomReportingPendingRelation.objects.create(
        task=token_task.task,
        source_model_id=model_id,
        target_model_id=target_model,
        relation_payload={
            "source": {"_id": 1, "model_id": model_id},
            "target": {"model_id": target_model, "identity": {"inst_name": "target"}},
            "asst_id": association_id,
        },
    )
    monkeypatch.setattr(
        "apps.cmdb.services.model.ModelManage.model_association_info_search",
        lambda model_asst_id: {
            "model_asst_id": association_id,
            "src_model_id": model_id,
            "dst_model_id": unique_crval_name("other_target"),
            "asst_id": "connect",
            "mapping": "n:n",
        },
    )
    resolve_instance = Mock(return_value={"_id": 2})
    edge_write = Mock()
    monkeypatch.setattr(relation_service, "_resolve_instance", resolve_instance)
    monkeypatch.setattr(relation_service, "_create_edge", edge_write)

    assert relation_service.backfill(token_task.task, "crval_validator") == 0

    resolve_instance.assert_not_called()
    edge_write.assert_not_called()
    assert CustomReportingPendingRelation.objects.filter(id=pending.id).exists()
    delivery = _deliveries_for_pending(token_task.task, pending)[0]
    assert delivery.state == "dead_letter"
    assert delivery.attempt_count == 1
    assert relation_service.backfill(token_task.task, "crval_validator") == 0
    delivery.refresh_from_db()
    assert delivery.attempt_count == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "failure_point", ["graph_model", "graph_subordinate", "graph_field_group", "graph_attribute", "db_task", "db_credential", "credential_token",],
)
def test_quick_task_failures_leave_a_scope_bound_recoverable_operation(
    monkeypatch, failure_point,
):
    scope_key = unique_crval_name("provision_scope")
    graph_facts = []
    payload = {
        "name": unique_crval_name("quick_task"),
        "team": [1],
        "config": {"mode": "quick"},
        "scope_key": scope_key,
        "idempotency_key": scope_key,
        "quick_model": {
            "model_id": unique_crval_name("quick_model"),
            "model_name": "CRV quick model",
            "classification_id": "server",
            "identity_keys": ["inst_name", "serial_no"],
        },
        "is_enabled": True,
    }

    def ensure(kind, fact):
        if failure_point == f"graph_{kind}":
            raise RuntimeError(f"graph_{kind} write failed")
        graph_facts.append(fact)
        return fact

    monkeypatch.setattr(
        model_service,
        "ensure_quick_model_fact",
        lambda quick_model, *args, **kwargs: ensure("model", {"kind": "model", "natural_key": quick_model["model_id"]}),
    )
    monkeypatch.setattr(
        model_service,
        "ensure_quick_subordinate_fact",
        lambda quick_model, *args, **kwargs: ensure("subordinate", {"kind": "subordinate", "natural_key": quick_model["model_id"]},),
    )
    monkeypatch.setattr(
        model_service,
        "ensure_quick_field_group_fact",
        lambda quick_model, *args, **kwargs: ensure("field_group", {"kind": "field_group", "natural_key": quick_model["model_id"]},),
    )
    monkeypatch.setattr(
        model_service,
        "ensure_quick_attribute_fact",
        lambda model_id, attr_id, *args, **kwargs: ensure("attribute", {"kind": "attribute", "natural_key": f"{model_id}:{attr_id}"}),
    )
    monkeypatch.setattr(model_service, "refresh_quick_model_cache", lambda model_id: True)
    if failure_point == "db_task":
        monkeypatch.setattr(
            reconcile_service, "_create_provisioned_task", Mock(side_effect=RuntimeError("db task write failed")),
        )
    elif failure_point == "db_credential":
        monkeypatch.setattr(
            reconcile_service, "_create_provisioned_credential", Mock(side_effect=RuntimeError("db credential write failed")),
        )
    elif failure_point == "credential_token":
        monkeypatch.setattr(
            CustomReportingCredential, "issue_token", Mock(side_effect=RuntimeError("credential token write failed")),
        )

    with pytest.raises(RuntimeError, match="failed"):
        task_service.create_task(payload, username="crval_validator")

    operations = _operations_for_scope(scope_key)
    if not operations:
        raise KnownProductDefect(f"CRV-F19: {failure_point} left graph facts without a scope-bound recovery operation")

    assert len(operations) == 1
    operation = operations[0]
    assert operation.action == "task_provision"
    assert operation.scope_key == scope_key
    assert operation.state in {"pending", "retry", "compensating", "manual_failed"}
    assert operation.desired_snapshot["quick_model"]["model_id"] == payload["quick_model"]["model_id"]
    assert graph_facts == operation.fact_snapshot["facts"]


@pytest.mark.django_db
def test_quick_group_sync_failure_preserves_effective_team_and_retryable_desired_operation(monkeypatch):
    token_task = create_token_task(mode="quick", team=[1])
    token_task.task.config["quick_model"] = {
        "model_id": token_task.task.config["model_id"],
        "model_name": "CRV quick model",
        "classification_id": "server",
        "identity_keys": ["inst_name"],
    }
    token_task.task.save(update_fields=["config", "updated_at"], sync_scopes=False)
    desired_team = [2]
    scope_key = unique_crval_name("update_scope")
    monkeypatch.setattr(
        model_service, "sync_model_group", Mock(side_effect=RuntimeError("graph group sync failed")),
    )
    monkeypatch.setattr(
        model_service.ModelManage,
        "search_model_info",
        lambda model_id: {
            "_id": 9,
            "model_id": model_id,
            "model_name": "CRV quick model",
            "classification_id": "server",
            "group": [1],
            "is_custom_reporting": True,
            "custom_reporting_operation_id": str(token_task.task.provision_operation_id),
        },
    )

    with pytest.raises(RuntimeError, match="graph group sync failed"):
        task_service.update_task(
            token_task.task.id,
            {"team": desired_team, "quick_model": token_task.task.config["quick_model"], "scope_key": scope_key, "idempotency_key": scope_key},
            username="crval_validator",
        )

    token_task.task.refresh_from_db()
    operations = _operations_for_scope(scope_key)
    if token_task.task.team == desired_team and not operations:
        raise KnownProductDefect("CRV-F20: graph sync failure changed effective team and discarded retryable desired state")

    assert token_task.task.team == [1]
    assert len(operations) == 1
    operation = operations[0]
    assert operation.action == "task_update"
    assert operation.state in {"pending", "retry", "compensating", "manual_failed"}
    assert operation.desired_snapshot["team"] == desired_team


@pytest.mark.django_db
def test_poison_pending_relation_is_dead_lettered_once_without_blocking_later_ingest(monkeypatch):
    token_task = create_token_task()
    pending = CustomReportingPendingRelation.objects.create(
        task=token_task.task,
        source_model_id=token_task.task.config["model_id"],
        target_model_id="target",
        relation_payload={
            "source": {"_id": 1, "model_id": token_task.task.config["model_id"]},
            "target": {"model_id": "target", "identity": {"inst_name": "target"}},
            "asst_id": "deterministically-invalid-association",
        },
    )
    edge_write = Mock()
    monkeypatch.setattr(
        ingest_service.merge_service, "merge_instances", lambda *args, **kwargs: _merge_result(),
    )
    monkeypatch.setattr(
        "apps.cmdb.services.model.ModelManage.search_model_attr", lambda model_id: [{"attr_id": "inst_name", "attr_type": "str"}],
    )
    monkeypatch.setattr(ingest_service.relation_service, "process", lambda *args: {"pending": 0})
    monkeypatch.setattr(
        relation_service,
        "_load_association",
        Mock(side_effect=BaseAppException("关联类型不存在")),
    )
    monkeypatch.setattr(relation_service, "_resolve_instance", lambda task, ref: {"_id": ref.instance_id or 2})
    monkeypatch.setattr(relation_service, "_create_edge", edge_write)

    assert relation_service.backfill(token_task.task, "crval_validator") == 0
    assert relation_service.backfill(token_task.task, "crval_validator") == 0

    rejected = []
    for inst_name in ("first", "second"):
        try:
            ingest_service.ingest(token_task.raw_token, {"instances": [{"inst_name": inst_name}]})
        except BaseAppException:
            rejected.append(inst_name)

    deliveries = _deliveries_for_pending(token_task.task, pending)
    if rejected and not deliveries:
        raise KnownProductDefect("CRV-F23: deterministic poison pending relation blocks ingest without a dead-letter delivery")

    assert rejected == []
    assert edge_write.call_count <= 1
    assert len(deliveries) == 1
    delivery = deliveries[0]
    assert delivery.state == "dead_letter"
    assert delivery.attempt_count == 1
    assert delivery.last_error
    assert CustomReportingBatch.objects.filter(task=token_task.task, status=CustomReportingBatch.STATUS_SUCCESS).count() == 2


@pytest.mark.django_db
def test_cleanup_graph_applied_retry_finalizes_without_second_delete(monkeypatch):
    """图删除成功而 DB finalize 失败时，恢复只能补事实/审核，不能再次图删。"""
    token_task = create_token_task(cleanup_strategy="snapshot")
    batch = CustomReportingBatch.objects.create(task=token_task.task, status=CustomReportingBatch.STATUS_SUCCESS,)
    review = CustomReportingCleanupReview.objects.create(
        batch=batch, status=CustomReportingCleanupReview.STATUS_PENDING, review_payload={"delete_ids": [301]},
    )
    graph_deletes = []
    original_finalize = CustomReportingOperationService.finalize
    finalize_calls = 0

    monkeypatch.setattr(cleanup_service, "_owned_instance_ids", lambda task, ids: list(ids))
    monkeypatch.setattr(
        cleanup_service, "_snapshot_instances", lambda ids: [{"_id": item, "model_id": token_task.task.config["model_id"]} for item in ids],
    )

    def delete_with_predelete_fact(ids, operator, *args, **kwargs):
        operation = reporting_models.CustomReportingOperation.objects.get(action="cleanup_review_approve")
        facts = (operation.fact_snapshot or {}).get("facts", [])
        assert any(item["kind"] == "cleanup_candidate_snapshot" for item in facts)
        graph_deletes.append((list(ids), operator))

    def fail_first_finalize(*args, **kwargs):
        nonlocal finalize_calls
        finalize_calls += 1
        if finalize_calls == 1:
            return False
        return original_finalize(*args, **kwargs)

    monkeypatch.setattr(cleanup_service, "_delete_instances", delete_with_predelete_fact)
    monkeypatch.setattr(CustomReportingOperationService, "finalize", fail_first_finalize)

    with pytest.raises(OperationConflict, match="finalize"):
        cleanup_service.approve(token_task.task.id, review.id, "reviewer")

    operation = reporting_models.CustomReportingOperation.objects.get(action="cleanup_review_approve")
    assert operation.state == "graph_applied"
    reporting_models.CustomReportingOperation.objects.filter(id=operation.id).update(lease_expires_at=now() - timedelta(seconds=1))
    monkeypatch.setattr(CustomReportingOperationService, "finalize", original_finalize)

    reconcile_service.reconcile_operation(operation.operation_id)

    review.refresh_from_db()
    operation.refresh_from_db()
    assert graph_deletes == [([301], "reviewer")]
    assert review.status == CustomReportingCleanupReview.STATUS_APPROVED
    assert operation.state == "completed"


@pytest.mark.django_db(transaction=True)
def test_cleanup_audit_rollback_has_no_external_operation_log(monkeypatch):
    """审核 DB finalize 回滚时，外部操作日志不得先于本地事务泄漏。"""
    token_task = create_token_task(cleanup_strategy="snapshot")
    batch = CustomReportingBatch.objects.create(task=token_task.task, status=CustomReportingBatch.STATUS_SUCCESS)
    review = CustomReportingCleanupReview.objects.create(
        batch=batch, status=CustomReportingCleanupReview.STATUS_PENDING, review_payload={"delete_ids": [304]},
    )
    external_client = Mock()

    monkeypatch.setattr(cleanup_service, "_owned_instance_ids", lambda task, ids: list(ids))
    monkeypatch.setattr(
        cleanup_service, "_snapshot_instances", lambda ids: [{"_id": item, "model_id": token_task.task.config["model_id"]} for item in ids],
    )
    monkeypatch.setattr(cleanup_service, "_delete_instances", Mock())
    monkeypatch.setattr("apps.cmdb.utils.change_record.SystemMgmt", lambda: external_client)
    monkeypatch.setattr("apps.cmdb.services.change_record_mirror.SystemMgmt", lambda: external_client)
    monkeypatch.setattr(CustomReportingOperationService, "finalize", lambda *args, **kwargs: False)

    with pytest.raises(OperationConflict, match="finalize"):
        cleanup_service.approve(token_task.task.id, review.id, "reviewer")

    assert ChangeRecord.objects.filter(inst_id=304).count() == 0
    assert ChangeRecordMirrorOutbox.objects.count() == 0
    external_client.save_operation_log.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_cleanup_audit_mirror_is_durable_and_exactly_once_across_completed_retry(monkeypatch):
    """提交后才投递审计镜像；重复完成路径不得产生第二条本地或外部审计。"""
    token_task = create_token_task(cleanup_strategy="snapshot")
    batch = CustomReportingBatch.objects.create(task=token_task.task, status=CustomReportingBatch.STATUS_SUCCESS)
    review = CustomReportingCleanupReview.objects.create(
        batch=batch, status=CustomReportingCleanupReview.STATUS_PENDING, review_payload={"delete_ids": [305]},
    )
    external_client = Mock()
    dispatch = Mock()

    monkeypatch.setattr(cleanup_service, "_owned_instance_ids", lambda task, ids: list(ids))
    monkeypatch.setattr(
        cleanup_service, "_snapshot_instances", lambda ids: [{"_id": item, "model_id": token_task.task.config["model_id"]} for item in ids],
    )
    monkeypatch.setattr(cleanup_service, "_delete_instances", Mock())
    monkeypatch.setattr("apps.cmdb.services.change_record_mirror.SystemMgmt", lambda: external_client)
    monkeypatch.setattr("apps.cmdb.services.change_record_mirror.dispatch_change_record_mirror", dispatch)
    external_client.save_operation_log.return_value = {"result": True}

    result = cleanup_service.approve(token_task.task.id, review.id, "reviewer")
    operation = reporting_models.CustomReportingOperation.objects.get(action="cleanup_review_approve")
    [outbox] = ChangeRecordMirrorOutbox.objects.all()

    assert result == {"id": review.id, "status": "approved"}
    assert ChangeRecord.objects.filter(inst_id=305).count() == 1
    assert outbox.payloads[0]["operation_event_id"] == str(ChangeRecord.objects.get(inst_id=305).operation_event_id)
    assert dispatch.call_args_list == [((outbox.event_id,), {})]
    external_client.save_operation_log.assert_not_called()
    assert ChangeRecordMirrorService.consume(outbox.event_id, owner_token="audit-worker") is True
    assert external_client.save_operation_log.call_count == 1
    assert reconcile_service.reconcile_operation(operation.operation_id) == result
    assert ChangeRecordMirrorService.consume(outbox.event_id, owner_token="retry-worker") is False
    assert ChangeRecord.objects.filter(inst_id=305).count() == 1
    assert ChangeRecordMirrorOutbox.objects.count() == 1
    assert external_client.save_operation_log.call_count == 1


@pytest.mark.django_db
def test_cleanup_crash_after_delete_uses_global_absence_to_finalize_once(monkeypatch):
    """图调用返回前崩溃时，只能用持久候选的全局缺失证实已删，再补审计。"""
    token_task = create_token_task(cleanup_strategy="snapshot")
    batch = CustomReportingBatch.objects.create(task=token_task.task, status=CustomReportingBatch.STATUS_SUCCESS)
    review = CustomReportingCleanupReview.objects.create(
        batch=batch, status=CustomReportingCleanupReview.STATUS_PENDING, review_payload={"delete_ids": [302]},
    )
    before_data = [{"_id": 302, "model_id": token_task.task.config["model_id"], "inst_name": "deleted-before-fact"}]
    graph_calls = []
    monkeypatch.setattr(cleanup_service, "_owned_instance_ids", lambda task, ids: list(ids))
    monkeypatch.setattr(cleanup_service, "_snapshot_instances", lambda ids: list(before_data))

    def crash_after_graph_delete(ids, operator, *args, **kwargs):
        graph_calls.append(list(ids))
        raise KeyboardInterrupt("simulated crash after graph delete")

    monkeypatch.setattr(cleanup_service, "_delete_instances", crash_after_graph_delete)
    with pytest.raises(KeyboardInterrupt, match="after graph delete"):
        cleanup_service.approve(token_task.task.id, review.id, "reviewer")

    operation = reporting_models.CustomReportingOperation.objects.get(action="cleanup_review_approve")
    assert operation.state == "graph_writing"
    assert any(item["kind"] == "cleanup_candidate_snapshot" for item in operation.fact_snapshot["facts"])
    reporting_models.CustomReportingOperation.objects.filter(id=operation.id).update(lease_expires_at=now() - timedelta(seconds=1))
    monkeypatch.setattr(cleanup_service, "_snapshot_instances", lambda ids: [])
    monkeypatch.setattr(cleanup_service, "_delete_instances", Mock())

    reconcile_service.reconcile_operation(operation.operation_id)

    review.refresh_from_db()
    operation.refresh_from_db()
    assert graph_calls == [[302]]
    assert review.status == CustomReportingCleanupReview.STATUS_APPROVED
    assert operation.state == "completed"
    assert ChangeRecord.objects.filter(inst_id=302).count() == 1


@pytest.mark.django_db
def test_cleanup_recovery_with_any_globally_present_candidate_is_manual_failed_without_redelete(monkeypatch):
    token_task = create_token_task(cleanup_strategy="snapshot")
    batch = CustomReportingBatch.objects.create(task=token_task.task, status=CustomReportingBatch.STATUS_SUCCESS)
    review = CustomReportingCleanupReview.objects.create(batch=batch, status="approving", review_payload={"delete_ids": [303]},)
    start = CustomReportingOperationService.start_cleanup_approval(
        review_id=review.id, desired_snapshot={"task_id": token_task.task.id, "review_id": review.id, "candidate_ids": [303], "operator": "reviewer"},
    )
    operation = start.operation
    reporting_models.CustomReportingOperation.objects.filter(id=operation.id).update(
        state="graph_writing",
        owner_token="crashed-worker",
        lease_expires_at=now() - timedelta(seconds=1),
        fact_snapshot={
            "facts": [
                {
                    "kind": "cleanup_candidate_snapshot",
                    "natural_key": str(review.id),
                    "candidate_ids": [303],
                    "before_data": [{"_id": 303, "model_id": token_task.task.config["model_id"]}],
                }
            ]
        },
    )
    graph_delete = Mock()
    monkeypatch.setattr(cleanup_service, "_snapshot_instances", lambda ids: [{"_id": 303, "model_id": "other-owner-model"}])
    monkeypatch.setattr(cleanup_service, "_delete_instances", graph_delete)

    with pytest.raises(model_service.GraphFactConflict, match="候选仍存在"):
        reconcile_service.reconcile_operation(operation.operation_id)

    operation.refresh_from_db()
    review.refresh_from_db()
    assert operation.state == "manual_failed"
    assert review.status == "approving"
    graph_delete.assert_not_called()
