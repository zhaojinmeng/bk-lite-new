from unittest.mock import Mock

import pytest

from apps.cmdb.services.model import ModelManage
from apps.cmdb_enterprise.custom_reporting.models import CustomReportingBatch, CustomReportingCleanupReview
from apps.cmdb_enterprise.custom_reporting.services import cleanup_service, merge_service
from apps.core.exceptions.base_app_exception import BaseAppException
from validation.custom_reporting.tests.factories import create_token_task
from validation.custom_reporting.tests.test_runtime_contracts import KnownProductDefect, _assert_contract_or_known_defect


@pytest.mark.django_db
@pytest.mark.xfail(strict=True, raises=KnownProductDefect, reason="CRV-F17")
def test_empty_snapshot_requires_authoritative_declaration_and_then_forces_review(monkeypatch):
    token_task = create_token_task(cleanup_strategy="snapshot")
    unconfirmed_batch = CustomReportingBatch.objects.create(task=token_task.task)
    confirmed_batch = CustomReportingBatch.objects.create(task=token_task.task)
    deleted = []
    monkeypatch.setattr(cleanup_service, "_delete_instances", lambda ids, operator: deleted.append(list(ids)))

    unconfirmed = cleanup_service.apply_snapshot(token_task.task, unconfirmed_batch, old_ids=[10, 11], covered_ids=[], operator="crval_validator",)
    token_task.task.config["snapshot_authoritative"] = True
    token_task.task.save(update_fields=["config", "updated_at"], sync_scopes=False)
    confirmed = cleanup_service.apply_snapshot(token_task.task, confirmed_batch, old_ids=[10, 11], covered_ids=[], operator="crval_validator",)

    current_bad = (
        unconfirmed == {"deleted": 2, "review_created": False}
        and confirmed == {"deleted": 2, "review_created": False}
        and deleted == [[10, 11], [10, 11]]
    )
    if current_bad:
        raise KnownProductDefect("CRV-F17: empty snapshots delete without an authoritative declaration or review")
    assert unconfirmed == {"deleted": 0, "review_created": False}
    assert confirmed == {"deleted": 0, "review_created": True}
    assert deleted == []
    assert CustomReportingCleanupReview.objects.filter(batch=confirmed_batch).exists()


@pytest.mark.django_db
@pytest.mark.xfail(strict=True, raises=KnownProductDefect, reason="CRV-F22")
def test_duplicate_identity_is_rejected_before_any_graph_write(monkeypatch):
    token_task = create_token_task(identity_keys=["inst_name"])
    add_write = Mock(return_value={"success": [], "failed": []})
    update_write = Mock(return_value={"success": [], "failed": []})
    monkeypatch.setattr(ModelManage, "search_model_attr", lambda model_id: [])
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
        merge_service.merge_instances(
            token_task.task, token_task.task.config["model_id"], [{"inst_name": "duplicate"}, {"inst_name": "duplicate"}], "crval_validator",
        )
    except BaseAppException:
        rejected = True

    _assert_contract_or_known_defect(
        actual=(rejected, add_write.call_count, update_write.call_count), expected=(True, 0, 0), known_bad=(False, 1, 1), finding="CRV-F22",
    )
