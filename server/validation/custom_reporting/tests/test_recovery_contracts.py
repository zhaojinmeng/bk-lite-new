from unittest.mock import Mock

import pytest

from apps.cmdb_enterprise.custom_reporting.models import CustomReportingBatch, CustomReportingPendingRelation, CustomReportingTask
from apps.cmdb_enterprise.custom_reporting.services import ingest_service, model_service, relation_service, task_service
from validation.custom_reporting.tests.factories import create_token_task, unique_crval_name
from validation.custom_reporting.tests.test_runtime_contracts import KnownProductDefect, _assert_contract_or_known_defect


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


@pytest.mark.django_db
@pytest.mark.xfail(strict=True, raises=KnownProductDefect, reason="CRV-F19")
def test_quick_task_db_failure_does_not_leave_an_unowned_graph_model(monkeypatch):
    graph_models = []
    payload = {
        "name": unique_crval_name("quick_task"),
        "team": [1],
        "config": {"mode": "quick"},
        "quick_model": {"model_id": unique_crval_name("quick_model"), "model_name": "CRV quick model", "identity_keys": ["inst_name"],},
        "is_enabled": True,
    }
    monkeypatch.setattr(
        model_service, "bootstrap_model", lambda quick_model, **kwargs: graph_models.append(quick_model["model_id"]),
    )
    monkeypatch.setattr(
        CustomReportingTask, "save", Mock(side_effect=RuntimeError("database write failed")),
    )

    with pytest.raises(RuntimeError, match="database write failed"):
        task_service.create_task(payload, username="crval_validator")

    _assert_contract_or_known_defect(
        actual=(CustomReportingTask.objects.filter(name=payload["name"]).count(), graph_models),
        expected=(0, []),
        known_bad=(0, [payload["quick_model"]["model_id"]]),
        finding="CRV-F19",
    )


@pytest.mark.django_db
@pytest.mark.xfail(strict=True, raises=KnownProductDefect, reason="CRV-F20")
def test_quick_model_group_sync_failure_keeps_effective_team_unchanged(monkeypatch):
    token_task = create_token_task(mode="quick", team=[1])
    token_task.task.config["quick_model"] = {
        "model_id": token_task.task.config["model_id"],
        "model_name": "CRV quick model",
        "identity_keys": ["inst_name"],
    }
    token_task.task.save(update_fields=["config", "updated_at"], sync_scopes=False)
    desired_team = [2]
    monkeypatch.setattr(
        model_service, "sync_model_group", Mock(side_effect=RuntimeError("graph group sync failed")),
    )

    with pytest.raises(RuntimeError, match="graph group sync failed"):
        task_service.update_task(
            token_task.task.id, {"team": desired_team, "quick_model": token_task.task.config["quick_model"]}, username="crval_validator",
        )

    token_task.task.refresh_from_db()
    _assert_contract_or_known_defect(
        actual=token_task.task.team, expected=[1], known_bad=desired_team, finding="CRV-F20",
    )


@pytest.mark.django_db
@pytest.mark.xfail(strict=True, raises=KnownProductDefect, reason="CRV-F23")
def test_poison_pending_relation_isolated_so_new_ingest_can_succeed(monkeypatch):
    token_task = create_token_task()
    CustomReportingPendingRelation.objects.create(
        task=token_task.task,
        source_model_id=token_task.task.config["model_id"],
        target_model_id="target",
        relation_payload={"source": {"_id": 1}, "target": {"model_id": "target"}, "asst_id": "bad"},
    )
    monkeypatch.setattr(ingest_service.merge_service, "merge_instances", lambda *args: _merge_result())
    monkeypatch.setattr(ingest_service.relation_service, "process", lambda *args: {"pending": 0})
    monkeypatch.setattr(
        relation_service, "backfill", Mock(side_effect=RuntimeError("poison pending relation")),
    )

    rejected = False
    try:
        result = ingest_service.ingest(token_task.raw_token, {"instances": [{"inst_name": "fresh"}]})
    except RuntimeError as exc:
        assert "poison pending relation" in str(exc)
        rejected = True
        result = None

    batch = CustomReportingBatch.objects.get(task=token_task.task)
    _assert_contract_or_known_defect(
        actual=(rejected, result, batch.status),
        expected=(False, {"batch_id": batch.id, "summary": batch.summary}, CustomReportingBatch.STATUS_SUCCESS),
        known_bad=(True, None, CustomReportingBatch.STATUS_FAILED),
        finding="CRV-F23",
    )
