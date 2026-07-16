from unittest.mock import Mock

import pytest

from apps.cmdb.services.model import ModelManage
from apps.cmdb_enterprise.custom_reporting.models import CustomReportingBatch, CustomReportingCleanupReview, CustomReportingPendingRelation
from apps.cmdb_enterprise.custom_reporting.services import cleanup_service, ingest_service, merge_service, relation_service
from apps.core.exceptions.base_app_exception import BaseAppException
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
@pytest.mark.xfail(strict=True, raises=KnownProductDefect, reason="CRV-F07")
def test_partial_merge_marks_batch_failed_and_skips_snapshot(monkeypatch):
    token_task = create_token_task(cleanup_strategy="snapshot")
    monkeypatch.setattr(
        ingest_service.merge_service,
        "merge_instances",
        lambda *args: _merge_result(created=1, errors=1, covered_ids=[1], old_data=[{"_id": 1}, {"_id": 2}],),
    )
    monkeypatch.setattr(
        ingest_service.relation_service, "process", lambda *args: {"pending": 0},
    )
    snapshot = Mock(return_value={"deleted": 1, "review_created": False})
    monkeypatch.setattr(cleanup_service, "apply_snapshot", snapshot)

    rejected = False
    try:
        ingest_service.ingest(
            token_task.raw_token, {"instances": [{"inst_name": "a"}, {"inst_name": "b"}]},
        )
    except BaseAppException as exc:
        assert "部分失败" in str(exc)
        rejected = True

    batch = CustomReportingBatch.objects.get(task=token_task.task)
    _assert_contract_or_known_defect(
        actual=(rejected, batch.status, snapshot.call_count),
        expected=(True, CustomReportingBatch.STATUS_FAILED, 0),
        known_bad=(False, CustomReportingBatch.STATUS_SUCCESS, 1),
        finding="CRV-F07",
    )


@pytest.mark.django_db
@pytest.mark.xfail(strict=True, raises=KnownProductDefect, reason="CRV-F08")
def test_merge_query_is_scoped_by_owner_and_team(monkeypatch):
    token_task = create_token_task(team=[1])
    model_id = token_task.task.config["model_id"]
    filters_seen = []

    class RecordingGraph:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def query_entity(self, entity, filters):
            filters_seen.extend(filters)
            return [], 0

    class EmptyManagement:
        def __init__(self, **kwargs):
            self.add_list = []
            self.update_list = []

        def add_inst(self, items):
            return {"success": [], "failed": []}

        def update_inst(self, items):
            return {"success": [], "failed": []}

    monkeypatch.setattr(merge_service, "GraphClient", RecordingGraph)
    monkeypatch.setattr(merge_service, "Management", EmptyManagement)
    monkeypatch.setattr(ModelManage, "search_model_attr", lambda model_id: [])

    merge_service.merge_instances(token_task.task, model_id, [], "crval_validator")

    model_filter = {"field": "model_id", "type": "str=", "value": model_id}
    owner_filter = {
        "field": "collect_task",
        "type": "str=",
        "value": f"cr_{token_task.task.id}",
    }
    organization_filter = {
        "field": "organization",
        "type": "list-in",
        "value": [1],
    }
    _assert_contract_or_known_defect(
        actual=filters_seen, expected=[model_filter, owner_filter, organization_filter], known_bad=[model_filter], finding="CRV-F08",
    )


@pytest.mark.django_db
@pytest.mark.xfail(strict=True, raises=KnownProductDefect, reason="CRV-F09")
@pytest.mark.parametrize("outside_endpoint", ["source", "target"])
def test_direct_relation_rejects_endpoint_outside_task_team_before_side_effects(
    monkeypatch, outside_endpoint,
):
    token_task = create_token_task(team=[1])
    model_id = token_task.task.config["model_id"]
    target_model = unique_crval_name("target_model")
    resolve = Mock(return_value={"_id": 2, "model_id": target_model, "organization": [1]})
    graph_write = Mock()
    monkeypatch.setattr(relation_service, "_resolve_instance", resolve)
    monkeypatch.setattr(relation_service, "_create_edge", graph_write)
    relation = {
        "source": {"_id": 1, "model_id": model_id, "organization": [2] if outside_endpoint == "source" else [1]},
        "target": {"model_id": target_model, "identity": {"inst_name": "target"}, "organization": [2] if outside_endpoint == "target" else [1]},
        "asst_id": unique_crval_name("association"),
    }

    rejected = False
    try:
        relation_service.process(token_task.task, [relation], {}, "crval_validator")
    except BaseAppException:
        rejected = True

    _assert_contract_or_known_defect(
        actual=(rejected, resolve.call_count, graph_write.call_count), expected=(True, 0, 0), known_bad=(False, 1, 1), finding="CRV-F09",
    )


@pytest.mark.django_db
@pytest.mark.xfail(strict=True, raises=KnownProductDefect, reason="CRV-F09")
def test_pending_backfill_rejects_endpoint_outside_task_team(monkeypatch):
    token_task = create_token_task(team=[1])
    model_id = token_task.task.config["model_id"]
    target_model = unique_crval_name("target_model")
    pending = CustomReportingPendingRelation.objects.create(
        task=token_task.task,
        source_model_id=model_id,
        target_model_id=target_model,
        relation_payload={
            "source": {"_id": 1, "model_id": model_id, "organization": [2]},
            "target": {"model_id": target_model, "identity": {"inst_name": "target"}, "organization": [1]},
            "asst_id": unique_crval_name("association"),
        },
    )
    resolve = Mock(return_value={"_id": 2, "organization": [1]})
    graph_write = Mock()
    monkeypatch.setattr(relation_service, "_resolve_instance", resolve)
    monkeypatch.setattr(relation_service, "_create_edge", graph_write)

    rejected = False
    try:
        relation_service.backfill(token_task.task, "crval_validator")
    except BaseAppException:
        rejected = True

    _assert_contract_or_known_defect(
        actual=(rejected, resolve.call_count, graph_write.call_count, CustomReportingPendingRelation.objects.filter(id=pending.id).count(),),
        expected=(True, 0, 0, 1),
        known_bad=(False, 1, 1, 0),
        finding="CRV-F09",
    )


@pytest.mark.django_db
@pytest.mark.xfail(strict=True, raises=KnownProductDefect, reason="CRV-F10")
def test_review_approval_does_not_delete_without_durable_approved_state(monkeypatch):
    token_task = create_token_task(cleanup_strategy="snapshot")
    batch = CustomReportingBatch.objects.create(task=token_task.task, status=CustomReportingBatch.STATUS_SUCCESS,)
    review = CustomReportingCleanupReview.objects.create(
        batch=batch, status=CustomReportingCleanupReview.STATUS_PENDING, review_payload={"delete_ids": [10, 11]},
    )
    deleted = []
    monkeypatch.setattr(
        cleanup_service, "_delete_instances", lambda ids, operator: deleted.extend(ids),
    )
    original_save = CustomReportingCleanupReview.save

    def fail_approved_save(self, *args, **kwargs):
        if self.id == review.id and self.status == self.STATUS_APPROVED:
            raise RuntimeError("injected review save failure")
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(CustomReportingCleanupReview, "save", fail_approved_save)

    with pytest.raises(RuntimeError, match="injected review save failure"):
        cleanup_service.approve(token_task.task.id, review.id, "crval_validator")

    review.refresh_from_db()
    _assert_contract_or_known_defect(
        actual=(deleted, review.status),
        expected=([], CustomReportingCleanupReview.STATUS_PENDING),
        known_bad=([10, 11], CustomReportingCleanupReview.STATUS_PENDING),
        finding="CRV-F10",
    )


@pytest.mark.django_db
def test_review_graph_failure_keeps_review_pending(monkeypatch):
    token_task = create_token_task(cleanup_strategy="snapshot")
    batch = CustomReportingBatch.objects.create(task=token_task.task, status=CustomReportingBatch.STATUS_SUCCESS,)
    review = CustomReportingCleanupReview.objects.create(
        batch=batch, status=CustomReportingCleanupReview.STATUS_PENDING, review_payload={"delete_ids": [10]},
    )
    monkeypatch.setattr(
        cleanup_service, "_delete_instances", Mock(side_effect=RuntimeError("injected graph failure")),
    )

    with pytest.raises(RuntimeError, match="injected graph failure"):
        cleanup_service.approve(token_task.task.id, review.id, "crval_validator")

    review.refresh_from_db()
    assert review.status == CustomReportingCleanupReview.STATUS_PENDING
    assert review.reviewed_at is None


@pytest.mark.django_db
def test_snapshot_threshold_and_none_strategy_keep_safe_positive_branches(monkeypatch):
    token_task = create_token_task(cleanup_strategy="snapshot")
    token_task.task.config["snapshot_delete_ratio_threshold"] = 50
    token_task.task.save(update_fields=["config", "updated_at"], sync_scopes=False)
    equal_batch = CustomReportingBatch.objects.create(task=token_task.task)
    over_batch = CustomReportingBatch.objects.create(task=token_task.task)
    deleted = []
    monkeypatch.setattr(
        cleanup_service, "_delete_instances", lambda ids, operator: deleted.append(list(ids)),
    )

    equal = cleanup_service.apply_snapshot(token_task.task, equal_batch, old_ids=[1, 2], covered_ids=[1], operator="crval_validator",)
    over = cleanup_service.apply_snapshot(token_task.task, over_batch, old_ids=[1, 2, 3], covered_ids=[1], operator="crval_validator",)

    assert equal == {"deleted": 1, "review_created": False}
    assert deleted == [[2]]
    assert over == {"deleted": 0, "review_created": True}
    assert CustomReportingCleanupReview.objects.filter(batch=over_batch).count() == 1

    none_task = create_token_task(cleanup_strategy="none")
    monkeypatch.setattr(
        ingest_service.merge_service, "merge_instances", lambda *args: _merge_result(old_data=[{"_id": 99}]),
    )
    monkeypatch.setattr(
        ingest_service.relation_service, "process", lambda *args: {"pending": 0},
    )
    snapshot = Mock()
    monkeypatch.setattr(cleanup_service, "apply_snapshot", snapshot)

    result = ingest_service.ingest(none_task.raw_token, {"instances": []})

    assert result["summary"]["errors"] == 0
    snapshot.assert_not_called()
