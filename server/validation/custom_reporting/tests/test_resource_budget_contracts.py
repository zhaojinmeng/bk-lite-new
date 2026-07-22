from unittest.mock import Mock

import pytest

from apps.cmdb.services.model import ModelManage
from apps.cmdb_enterprise.custom_reporting.models import (
    CustomReportingBatch,
    CustomReportingCleanupReview,
    CustomReportingCredential,
    PendingRelationDelivery,
)
from apps.cmdb_enterprise.custom_reporting.services import cleanup_service, ingest_service, merge_service, relation_service
from apps.core.exceptions.base_app_exception import BaseAppException
from validation.custom_reporting.tests.factories import create_token_task, unique_crval_name
from validation.custom_reporting.tests.test_runtime_contracts import _assert_contract_or_known_defect


@pytest.mark.django_db
def test_empty_snapshot_requires_authoritative_declaration_and_then_forces_review(monkeypatch):
    token_task = create_token_task(cleanup_strategy="snapshot")
    monkeypatch.setattr(
        cleanup_service,
        "_owned_instance_ids",
        lambda task, inst_ids: list(inst_ids),
    )
    unconfirmed_batch = CustomReportingBatch.objects.create(task=token_task.task)
    confirmed_batch = CustomReportingBatch.objects.create(task=token_task.task)
    unconfirmed_before = (unconfirmed_batch.status, unconfirmed_batch.summary)
    deleted = []
    monkeypatch.setattr(cleanup_service, "_delete_instances", lambda ids, operator: deleted.append(list(ids)))

    unconfirmed = cleanup_service.apply_snapshot(
        token_task.task,
        unconfirmed_batch,
        old_ids=[10, 11],
        covered_ids=[],
        operator="crval_validator",
        snapshot_authoritative=False,
    )
    confirmed = cleanup_service.apply_snapshot(
        token_task.task,
        confirmed_batch,
        old_ids=[10, 11],
        covered_ids=[],
        operator="crval_validator",
        snapshot_authoritative=True,
    )

    assert unconfirmed == {"deleted": 0, "review_created": False}
    unconfirmed_batch.refresh_from_db()
    assert (unconfirmed_batch.status, unconfirmed_batch.summary) == unconfirmed_before
    assert not CustomReportingCleanupReview.objects.filter(batch=unconfirmed_batch).exists()
    assert confirmed == {"deleted": 0, "review_created": True}
    assert deleted == []
    assert CustomReportingCleanupReview.objects.filter(batch=confirmed_batch).exists()


@pytest.mark.django_db
def test_duplicate_identity_is_rejected_before_any_graph_write(monkeypatch):
    token_task = create_token_task(identity_keys=["inst_name"])
    add_write = Mock(return_value={"success": [], "failed": []})
    update_write = Mock(return_value={"success": [], "failed": []})
    monkeypatch.setattr(
        ModelManage,
        "search_model_attr",
        lambda model_id: [{"attr_id": "serial", "attr_type": "int"}],
    )
    monkeypatch.setattr(merge_service.Management, "add_inst", add_write)
    monkeypatch.setattr(merge_service.Management, "update_inst", update_write)

    class EmptyGraph:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def query_entity(self, *args, **kwargs):
            return [], 0

    monkeypatch.setattr(merge_service, "GraphClient", EmptyGraph)
    rejected = False
    try:
        token_task.task.config["identity_keys"] = ["serial"]
        merge_service.merge_instances(
            token_task.task,
            token_task.task.config["model_id"],
            [{"serial": "1"}, {"serial": 1}],
            "crval_validator",
        )
    except BaseAppException:
        rejected = True

    _assert_contract_or_known_defect(
        actual=(rejected, add_write.call_count, update_write.call_count), expected=(True, 0, 0), known_bad=(False, 1, 1), finding="CRV-F22",
    )


@pytest.mark.django_db
def test_ingest_rejects_oversized_instances_before_batch_and_graph(monkeypatch):
    token_task = create_token_task()
    graph_write = Mock(return_value={"created": 0, "updated": 0, "deleted": 0, "errors": 0, "covered_ids": [], "old_data": [], "index": {}})
    attr_lookup = Mock(return_value=[{"attr_id": "inst_name", "attr_type": "str"}])
    monkeypatch.setattr(ModelManage, "search_model_attr", attr_lookup)
    monkeypatch.setattr(merge_service, "merge_instances", graph_write)

    with pytest.raises(BaseAppException, match="预算|过大|超过"):
        ingest_service.ingest(
            token_task.raw_token,
            {"instances": [{"inst_name": f"host-{index}"} for index in range(1001)], "relations": []},
            idempotency_key=unique_crval_name("budget"),
        )

    assert not CustomReportingBatch.objects.filter(task=token_task.task).exists()
    graph_write.assert_not_called()
    attr_lookup.assert_not_called()


@pytest.mark.django_db
def test_ingest_rejects_too_many_fields_before_batch_and_graph(monkeypatch):
    token_task = create_token_task(mode="quick")
    graph_write = Mock(return_value={"created": 0, "updated": 0, "deleted": 0, "errors": 0, "covered_ids": [], "old_data": [], "index": {}})
    monkeypatch.setattr(merge_service, "merge_instances", graph_write)
    monkeypatch.setattr(ModelManage, "search_model_attr", Mock(return_value=[{"attr_id": "inst_name", "attr_type": "str"}]))
    monkeypatch.setattr(ingest_service.model_service, "register_model_fields", Mock(return_value=[]))

    oversized_instance = {"inst_name": "host-1"}
    oversized_instance.update({f"field_{index}": "x" for index in range(129)})

    with pytest.raises(BaseAppException, match="字段|预算|超过"):
        ingest_service.ingest(
            token_task.raw_token,
            {"instances": [oversized_instance], "relations": []},
            idempotency_key=unique_crval_name("fields"),
        )

    assert not CustomReportingBatch.objects.filter(task=token_task.task).exists()
    graph_write.assert_not_called()


@pytest.mark.django_db
def test_token_lookup_does_not_compare_against_every_enabled_credential(monkeypatch):
    target = create_token_task()
    for _index in range(12):
        create_token_task()
    matches = []
    original_matches = CustomReportingCredential.matches_token

    def recording_matches(self, raw_token):
        matches.append(self.id)
        return original_matches(self, raw_token)

    monkeypatch.setattr(CustomReportingCredential, "matches_token", recording_matches)

    resolved = ingest_service._resolve_credential(target.raw_token)

    assert resolved.task_id == target.task.id
    assert matches == [resolved.id]


@pytest.mark.django_db
def test_merge_old_data_uses_keyset_pages_without_count(monkeypatch):
    token_task = create_token_task()
    calls = []

    class PagedGraph:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def query_entity(self, label, filters, **kwargs):
            calls.append((label, filters, kwargs))
            return [], 9999

    monkeypatch.setattr(merge_service, "GraphClient", PagedGraph)
    monkeypatch.setattr(
        ModelManage,
        "search_model_attr",
        lambda _model_id: [{"attr_id": "inst_name", "attr_name": "实例名", "attr_type": "str"}],
    )
    monkeypatch.setattr(merge_service.Management, "add_inst", lambda self, items: {"success": [], "failed": []})
    monkeypatch.setattr(merge_service.Management, "update_inst", lambda self, items: {"success": [], "failed": []})

    result = merge_service.merge_instances(
        token_task.task,
        token_task.task.config["model_id"],
        [{"inst_name": "host-1"}],
        "crval_validator",
        instance_plan=merge_service.schema_service.NormalizedInstancePlan(
            schema=merge_service.schema_service.CompiledTaskSchema(mode="standard", identity_keys=("inst_name",)),
            attrs=[{"attr_id": "inst_name", "attr_type": "str"}],
            declared_attr_ids=frozenset({"inst_name"}),
            instances=[{"inst_name": "host-1"}],
        ),
    )

    assert result["old_data"] == []
    assert calls
    assert all(call_kwargs.get("include_count") is False for _label, _filters, call_kwargs in calls)
    assert all(0 < call_kwargs.get("page", {}).get("limit", 0) <= 500 for _label, _filters, call_kwargs in calls)
    assert any(any(item["field"] == "id" and item["type"] == "id>" for item in filters) for _label, filters, _kwargs in calls)


@pytest.mark.django_db
def test_expire_cleanup_respects_budget_page_and_deadline(monkeypatch):
    token_task = create_token_task(cleanup_strategy="expire")
    token_task.task.config["expire_days"] = 1
    token_task.task.save(update_fields=["config"])
    query_calls = []

    class BudgetedGraph:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def query_entity(self, label, filters, **kwargs):
            query_calls.append((label, filters, kwargs))
            return [], 0

    monkeypatch.setattr(cleanup_service, "GraphClient", BudgetedGraph)
    monkeypatch.setattr(cleanup_service, "_delete_owned_instances", Mock())

    cleanup_service.expire_cleanup()

    assert query_calls
    assert all(call_kwargs.get("include_count") is False for _label, _filters, call_kwargs in query_calls)
    assert all(0 < call_kwargs.get("page", {}).get("limit", 0) <= 500 for _label, _filters, call_kwargs in query_calls)
    assert any(any(item["field"] == "id" and item["type"] == "id>" for item in filters) for _label, filters, _kwargs in query_calls)


@pytest.mark.django_db
def test_pending_delivery_claim_honors_deadline_and_page_limit(monkeypatch):
    token_task = create_token_task()
    for index in range(3):
        relation_service.enqueue_pending_delivery(
            task=token_task.task,
            relation_payload={
                "source": {"_id": index, "model_id": token_task.task.config["model_id"]},
                "target": {"_id": index + 100, "model_id": "target"},
                "asst_id": f"asst-{index}",
            },
            source_model_id=token_task.task.config["model_id"],
            target_model_id="target",
        )

    class ExpiredBudget:
        def with_deadline(self, *, seconds):
            return self

        def ensure_time_remaining(self):
            raise BaseAppException("资源预算已耗尽")

        def clamp_batch_size(self, value):
            return min(int(value), 100)

    monkeypatch.setattr(relation_service, "RESOURCE_BUDGET", ExpiredBudget(), raising=False)

    claimed = relation_service.claim_pending_deliveries(owner_token="budget-worker", batch_size=1000)

    assert claimed == []
    assert PendingRelationDelivery.objects.filter(state=PendingRelationDelivery.STATE_SENDING).count() == 0
