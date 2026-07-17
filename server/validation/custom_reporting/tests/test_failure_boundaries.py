from unittest.mock import Mock, call

import pytest

from apps.cmdb.graph.format_type import FORMAT_TYPE_PARAMS, ParameterCollector
from apps.cmdb.services import instance as instance_service
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


def _record_instance_queries(monkeypatch, foreign_instance):
    filters_seen = []

    class RecordingGraph:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def query_entity(self, entity, filters):
            filters_seen.append(list(filters))
            scoped_fields = {item["field"] for item in filters}
            if {"collect_task", "organization"}.issubset(scoped_fields):
                return [], 0
            return [foreign_instance], 1

    monkeypatch.setattr(instance_service, "GraphClient", RecordingGraph)
    return filters_seen


@pytest.mark.django_db
@pytest.mark.xfail(strict=True, raises=KnownProductDefect, reason="CRV-F07")
def test_partial_merge_marks_batch_failed_and_skips_snapshot(monkeypatch):
    token_task = create_token_task(cleanup_strategy="snapshot")
    monkeypatch.setattr(
        ingest_service.merge_service,
        "merge_instances",
        lambda *args: _merge_result(
            created=1,
            errors=1,
            covered_ids=[1],
            old_data=[{"_id": 1}, {"_id": 2}],
        ),
    )
    monkeypatch.setattr(
        ingest_service.relation_service,
        "process",
        lambda *args: {"pending": 0},
    )
    snapshot = Mock(return_value={"deleted": 1, "review_created": False})
    monkeypatch.setattr(cleanup_service, "apply_snapshot", snapshot)

    rejected = False
    try:
        ingest_service.ingest(
            token_task.raw_token,
            {"instances": [{"inst_name": "a"}, {"inst_name": "b"}]},
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
        "type": "list[]",
        "value": [1],
    }
    collector = ParameterCollector()
    formatted = FORMAT_TYPE_PARAMS[organization_filter["type"]](organization_filter, collector)
    assert formatted == "ALL(x IN $list1 WHERE x IN n.organization)"
    assert collector.get_params() == {"list1": [1]}
    _assert_contract_or_known_defect(
        actual=filters_seen,
        expected=[model_filter, owner_filter, organization_filter],
        known_bad=[model_filter],
        finding="CRV-F08",
    )


@pytest.mark.django_db
@pytest.mark.xfail(strict=True, raises=KnownProductDefect, reason="CRV-F09")
def test_direct_relation_does_not_link_foreign_target_or_trust_source_id(monkeypatch):
    token_task = create_token_task(team=[1])
    foreign_task = create_token_task(team=[2])
    model_id = token_task.task.config["model_id"]
    target_model = unique_crval_name("target_model")
    foreign_target = {
        "_id": 2,
        "model_id": target_model,
        "organization": [2],
        "collect_task": f"cr_{foreign_task.task.id}",
    }
    filters_seen = _record_instance_queries(monkeypatch, foreign_target)
    graph_write = Mock()
    monkeypatch.setattr(relation_service, "_create_edge", graph_write)
    asst_id = unique_crval_name("association")
    relation = {
        "source": {"_id": 1, "model_id": model_id},
        "target": {
            "model_id": target_model,
            "identity": {"inst_name": "target"},
        },
        "asst_id": asst_id,
    }

    result = relation_service.process(token_task.task, [relation], {}, "crval_validator")

    unscoped_filters = [
        [
            {"field": "model_id", "type": "str=", "value": target_model},
            {"field": "inst_name", "type": "str=", "value": "target"},
        ]
    ]
    current_bad = (
        filters_seen == unscoped_filters
        and graph_write.call_args_list == [call(1, foreign_target["_id"], asst_id, "crval_validator")]
        and result == {"pending": 0}
    )
    if current_bad:
        raise KnownProductDefect(
            "CRV-F09: direct source _id was forwarded without lookup and an " "unscoped target query created an edge to a foreign owner/team node"
        )

    queried_fields = {item["field"] for item in filters_seen[0]}
    assert {"collect_task", "organization"}.issubset(queried_fields)
    graph_write.assert_not_called()
    assert result == {"pending": 1}


@pytest.mark.django_db
@pytest.mark.xfail(strict=True, raises=KnownProductDefect, reason="CRV-F09")
def test_pending_backfill_does_not_link_foreign_target(monkeypatch):
    token_task = create_token_task(team=[1])
    foreign_task = create_token_task(team=[2])
    model_id = token_task.task.config["model_id"]
    target_model = unique_crval_name("target_model")
    asst_id = unique_crval_name("association")
    pending = CustomReportingPendingRelation.objects.create(
        task=token_task.task,
        source_model_id=model_id,
        target_model_id=target_model,
        relation_payload={
            "source": {"_id": 1, "model_id": model_id},
            "target": {
                "model_id": target_model,
                "identity": {"inst_name": "target"},
            },
            "asst_id": asst_id,
        },
    )
    foreign_target = {
        "_id": 2,
        "model_id": target_model,
        "organization": [2],
        "collect_task": f"cr_{foreign_task.task.id}",
    }
    filters_seen = _record_instance_queries(monkeypatch, foreign_target)
    graph_write = Mock()
    monkeypatch.setattr(relation_service, "_create_edge", graph_write)

    resolved = relation_service.backfill(token_task.task, "crval_validator")

    unscoped_filters = [
        [
            {"field": "model_id", "type": "str=", "value": target_model},
            {"field": "inst_name", "type": "str=", "value": "target"},
        ]
    ]
    current_bad = (
        filters_seen == unscoped_filters
        and graph_write.call_args_list == [call(1, foreign_target["_id"], asst_id, "crval_validator")]
        and resolved == 1
        and not CustomReportingPendingRelation.objects.filter(id=pending.id).exists()
    )
    if current_bad:
        raise KnownProductDefect(
            "CRV-F09: pending backfill used an unscoped target query, created " "an edge to a foreign owner/team node, and deleted the pending record"
        )

    queried_fields = {item["field"] for item in filters_seen[0]}
    assert {"collect_task", "organization"}.issubset(queried_fields)
    graph_write.assert_not_called()
    assert resolved == 0
    assert CustomReportingPendingRelation.objects.filter(id=pending.id).exists()


@pytest.mark.django_db
@pytest.mark.xfail(strict=True, raises=KnownProductDefect, reason="CRV-F10")
def test_review_approval_does_not_delete_without_durable_approved_state(monkeypatch):
    token_task = create_token_task(cleanup_strategy="snapshot")
    batch = CustomReportingBatch.objects.create(
        task=token_task.task,
        status=CustomReportingBatch.STATUS_SUCCESS,
    )
    review = CustomReportingCleanupReview.objects.create(
        batch=batch,
        status=CustomReportingCleanupReview.STATUS_PENDING,
        review_payload={"delete_ids": [10, 11]},
    )
    deleted = []
    monkeypatch.setattr(
        cleanup_service,
        "_delete_instances",
        lambda ids, operator: deleted.extend(ids),
    )
    original_save = CustomReportingCleanupReview.save
    failed_once = False

    def fail_approved_save(self, *args, **kwargs):
        nonlocal failed_once
        if self.id == review.id and self.status == self.STATUS_APPROVED and not failed_once:
            failed_once = True
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
def test_review_approval_retry_advances_after_transient_db_failure(monkeypatch):
    token_task = create_token_task(cleanup_strategy="snapshot")
    batch = CustomReportingBatch.objects.create(task=token_task.task, status=CustomReportingBatch.STATUS_SUCCESS)
    review = CustomReportingCleanupReview.objects.create(
        batch=batch,
        status=CustomReportingCleanupReview.STATUS_PENDING,
        review_payload={"delete_ids": [10, 11]},
    )
    deleted = []
    monkeypatch.setattr(cleanup_service, "_delete_instances", lambda ids, operator: deleted.extend(ids))
    original_save = CustomReportingCleanupReview.save
    failed_once = False

    def fail_first_approved_save(self, *args, **kwargs):
        nonlocal failed_once
        if self.id == review.id and self.status == self.STATUS_APPROVED and not failed_once:
            failed_once = True
            raise RuntimeError("injected review save failure")
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(CustomReportingCleanupReview, "save", fail_first_approved_save)

    with pytest.raises(RuntimeError, match="injected review save failure"):
        cleanup_service.approve(token_task.task.id, review.id, "crval_validator")

    result = cleanup_service.approve(token_task.task.id, review.id, "crval_validator")

    review.refresh_from_db()
    assert result == {"id": review.id, "status": review.STATUS_APPROVED}
    assert review.status == review.STATUS_APPROVED
    assert deleted == [10, 11, 10, 11]


@pytest.mark.django_db
def test_review_graph_failure_keeps_review_pending(monkeypatch):
    token_task = create_token_task(cleanup_strategy="snapshot")
    batch = CustomReportingBatch.objects.create(
        task=token_task.task,
        status=CustomReportingBatch.STATUS_SUCCESS,
    )
    review = CustomReportingCleanupReview.objects.create(
        batch=batch,
        status=CustomReportingCleanupReview.STATUS_PENDING,
        review_payload={"delete_ids": [10]},
    )
    monkeypatch.setattr(
        cleanup_service,
        "_delete_instances",
        Mock(side_effect=RuntimeError("injected graph failure")),
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
        cleanup_service,
        "_delete_instances",
        lambda ids, operator: deleted.append(list(ids)),
    )

    equal = cleanup_service.apply_snapshot(
        token_task.task,
        equal_batch,
        old_ids=[1, 2],
        covered_ids=[1],
        operator="crval_validator",
    )
    over = cleanup_service.apply_snapshot(
        token_task.task,
        over_batch,
        old_ids=[1, 2, 3],
        covered_ids=[1],
        operator="crval_validator",
    )

    assert equal == {"deleted": 1, "review_created": False}
    assert deleted == [[2]]
    assert over == {"deleted": 0, "review_created": True}
    assert CustomReportingCleanupReview.objects.filter(batch=over_batch).count() == 1

    none_task = create_token_task(cleanup_strategy="none")
    monkeypatch.setattr(
        ingest_service.merge_service,
        "merge_instances",
        lambda *args: _merge_result(old_data=[{"_id": 99}]),
    )
    monkeypatch.setattr(
        ingest_service.relation_service,
        "process",
        lambda *args: {"pending": 0},
    )
    snapshot = Mock()
    monkeypatch.setattr(cleanup_service, "apply_snapshot", snapshot)

    result = ingest_service.ingest(none_task.raw_token, {"instances": []})

    assert result["summary"]["errors"] == 0
    snapshot.assert_not_called()
