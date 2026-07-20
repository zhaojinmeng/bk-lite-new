from unittest.mock import Mock

import pytest

from apps.cmdb_enterprise.custom_reporting import models as reporting_models
from apps.cmdb_enterprise.custom_reporting.models import (
    CustomReportingBatch,
    CustomReportingCredential,
    CustomReportingPendingRelation,
    CustomReportingTask,
)
from apps.cmdb_enterprise.custom_reporting.services import (
    ingest_service,
    model_service,
    relation_service,
    task_service,
)
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
@pytest.mark.xfail(strict=True, raises=KnownProductDefect, reason="CRV-F19")
@pytest.mark.parametrize(
    "failure_point",
    ["graph_model", "graph_attribute", "db_task", "db_credential", "credential_token"],
)
def test_quick_task_failures_leave_a_scope_bound_recoverable_operation(
    monkeypatch,
    failure_point,
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
            "identity_keys": ["inst_name"],
        },
        "is_enabled": True,
    }

    def fail_bootstrap(quick_model, **kwargs):
        if failure_point != "graph_model":
            graph_facts.append({"kind": "model", "model_id": quick_model["model_id"]})
        if failure_point == "graph_model":
            raise RuntimeError("graph_model write failed")
        if failure_point == "graph_attribute":
            raise RuntimeError("graph_attribute write failed after model fact")
        graph_facts.append({"kind": "identity_attribute", "attr_id": "inst_name"})

    monkeypatch.setattr(model_service, "bootstrap_model", fail_bootstrap)
    if failure_point == "db_task":
        monkeypatch.setattr(
            CustomReportingTask,
            "save",
            Mock(side_effect=RuntimeError("db task write failed")),
        )
    elif failure_point == "db_credential":
        monkeypatch.setattr(
            CustomReportingCredential.objects,
            "create",
            Mock(side_effect=RuntimeError("db credential write failed")),
        )
    elif failure_point == "credential_token":
        monkeypatch.setattr(
            CustomReportingCredential,
            "issue_token",
            Mock(side_effect=RuntimeError("credential token write failed")),
        )

    with pytest.raises(RuntimeError, match="failed"):
        task_service.create_task(payload, username="crval_validator")

    operations = _operations_for_scope(scope_key)
    if not operations:
        raise KnownProductDefect(
            f"CRV-F19: {failure_point} left graph facts without a scope-bound recovery operation"
        )

    if failure_point == "graph_model":
        assert graph_facts == []
    elif failure_point == "graph_attribute":
        assert graph_facts == [{"kind": "model", "model_id": payload["quick_model"]["model_id"]}]
    else:
        assert graph_facts == [
            {"kind": "model", "model_id": payload["quick_model"]["model_id"]},
            {"kind": "identity_attribute", "attr_id": "inst_name"},
        ]
    assert len(operations) == 1
    operation = operations[0]
    assert operation.action == "task_provision"
    assert operation.scope_key == scope_key
    assert operation.state in {"pending", "retry", "compensating", "manual_failed"}
    assert operation.desired_snapshot["quick_model"]["model_id"] == payload["quick_model"]["model_id"]
    if failure_point == "graph_attribute":
        assert payload["quick_model"]["model_id"] in str(operation.fact_snapshot)


@pytest.mark.django_db
@pytest.mark.xfail(strict=True, raises=KnownProductDefect, reason="CRV-F20")
def test_quick_group_sync_failure_preserves_effective_team_and_retryable_desired_operation(monkeypatch):
    token_task = create_token_task(mode="quick", team=[1])
    token_task.task.config["quick_model"] = {
        "model_id": token_task.task.config["model_id"],
        "model_name": "CRV quick model",
        "identity_keys": ["inst_name"],
    }
    token_task.task.save(update_fields=["config", "updated_at"], sync_scopes=False)
    desired_team = [2]
    scope_key = unique_crval_name("update_scope")
    monkeypatch.setattr(
        model_service,
        "sync_model_group",
        Mock(side_effect=RuntimeError("graph group sync failed")),
    )

    with pytest.raises(RuntimeError, match="graph group sync failed"):
        task_service.update_task(
            token_task.task.id,
            {
                "team": desired_team,
                "quick_model": token_task.task.config["quick_model"],
                "scope_key": scope_key,
                "idempotency_key": scope_key,
            },
            username="crval_validator",
        )

    token_task.task.refresh_from_db()
    operations = _operations_for_scope(scope_key)
    if token_task.task.team == desired_team and not operations:
        raise KnownProductDefect(
            "CRV-F20: graph sync failure changed effective team and discarded retryable desired state"
        )

    assert token_task.task.team == [1]
    assert len(operations) == 1
    operation = operations[0]
    assert operation.action == "task_update"
    assert operation.state in {"pending", "retry", "compensating", "manual_failed"}
    assert operation.desired_snapshot["team"] == desired_team


@pytest.mark.django_db
@pytest.mark.xfail(strict=True, raises=KnownProductDefect, reason="CRV-F23")
def test_poison_pending_relation_is_dead_lettered_once_without_blocking_later_ingest(monkeypatch):
    token_task = create_token_task()
    pending = CustomReportingPendingRelation.objects.create(
        task=token_task.task,
        source_model_id=token_task.task.config["model_id"],
        target_model_id="target",
        relation_payload={
            "source": {"_id": 1},
            "target": {"model_id": "target", "identity": {"inst_name": "target"}},
            "asst_id": "deterministically-invalid-association",
        },
    )
    edge_write = Mock(side_effect=BaseAppException("关联类型不存在"))
    monkeypatch.setattr(ingest_service.merge_service, "merge_instances", lambda *args: _merge_result())
    monkeypatch.setattr(ingest_service.relation_service, "process", lambda *args: {"pending": 0})
    monkeypatch.setattr(
        relation_service,
        "_resolve_instance",
        lambda model_id, identity: {"_id": 2},
    )
    monkeypatch.setattr(relation_service, "_create_edge", edge_write)

    rejected = []
    for inst_name in ("first", "second"):
        try:
            ingest_service.ingest(token_task.raw_token, {"instances": [{"inst_name": inst_name}]})
        except BaseAppException:
            rejected.append(inst_name)

    deliveries = _deliveries_for_pending(token_task.task, pending)
    if rejected and not deliveries:
        raise KnownProductDefect(
            "CRV-F23: deterministic poison pending relation blocks ingest without a dead-letter delivery"
        )

    assert rejected == []
    assert edge_write.call_count <= 1
    assert len(deliveries) == 1
    delivery = deliveries[0]
    assert delivery.state == "dead_letter"
    assert delivery.attempt_count == 1
    assert delivery.last_error
    assert CustomReportingBatch.objects.filter(task=token_task.task, status=CustomReportingBatch.STATUS_SUCCESS).count() == 2
