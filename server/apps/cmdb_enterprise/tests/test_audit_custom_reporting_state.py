import json
from io import StringIO

import pytest
from django.core.management import call_command

from apps.cmdb_enterprise.custom_reporting.models import (
    CustomReportingPendingRelation,
    CustomReportingTask,
    PendingRelationDelivery,
)


def _run_audit(*args):
    out = StringIO()
    call_command("audit_custom_reporting_state", *args, stdout=out)
    return json.loads(out.getvalue())


def _task(name="audit-task", *, config=None):
    return CustomReportingTask.objects.create(
        name=name,
        team=[1],
        config=config or {"mode": "standard", "model_id": "host", "identity_keys": ["inst_name"]},
    )


@pytest.mark.django_db
def test_dry_run_reports_invalid_identity_without_writes():
    task = _task("bad-identity", config={"mode": "standard", "model_id": "host", "identity_keys": []})

    report = _run_audit()

    task.refresh_from_db()
    assert task.sync_status == CustomReportingTask.SYNC_ACTIVE
    assert report["dry_run"] is True
    assert report["summary"]["findings"] == 1
    assert report["findings"][0]["code"] == "invalid_task_config"
    assert report["findings"][0]["task_id"] == task.id
    assert report["findings"][0]["safe_action"] == "mark_task_degraded"


@pytest.mark.django_db
def test_apply_marks_invalid_config_degraded_idempotently():
    task = _task("apply-bad-identity", config={"mode": "quick", "model_id": "host", "identity_keys": ["_id"]})

    first = _run_audit("--apply-safe-fixes")
    second = _run_audit("--apply-safe-fixes")

    task.refresh_from_db()
    assert task.sync_status == CustomReportingTask.SYNC_DEGRADED
    assert first["summary"]["applied"] == 1
    assert second["summary"]["applied"] == 0


@pytest.mark.django_db
def test_missing_mapping_is_reported_and_manual_failed_without_payload_defaulting():
    task = _task()
    payload = {
        "source": {"_id": 1, "model_id": "host"},
        "target": {"_id": 2, "model_id": "service"},
        "asst_id": "host_service",
    }
    delivery = PendingRelationDelivery.objects.create(
        task=task,
        fingerprint="missing-mapping",
        payload_hash="payload-hash",
        source_model_id="host",
        target_model_id="service",
        relation_payload=payload,
    )

    report = _run_audit("--apply-safe-fixes")

    delivery.refresh_from_db()
    assert report["findings"][0]["code"] == "relation_missing_mapping"
    assert report["findings"][0]["safe_action"] == "manual_failed"
    assert delivery.state == PendingRelationDelivery.STATE_DEAD_LETTER
    assert "mapping" not in delivery.relation_payload


@pytest.mark.django_db
def test_apply_creates_one_delivery_for_exact_duplicate_legacy_pending():
    task = _task()
    payload = {
        "source": {"_id": 1, "model_id": "host"},
        "target": {"_id": 2, "model_id": "service"},
        "asst_id": "host_service",
        "mapping": "depends_on",
    }
    CustomReportingPendingRelation.objects.create(task=task, source_model_id="host", target_model_id="service", relation_payload=payload)
    CustomReportingPendingRelation.objects.create(task=task, source_model_id="host", target_model_id="service", relation_payload=dict(payload))

    report = _run_audit("--apply-safe-fixes")

    assert report["summary"]["applied"] == 1
    assert PendingRelationDelivery.objects.filter(task=task).count() == 1
    delivery = PendingRelationDelivery.objects.get(task=task)
    assert delivery.relation_payload == payload


@pytest.mark.django_db
def test_apply_does_not_dedupe_fingerprint_collision_with_different_payload(monkeypatch):
    from apps.cmdb_enterprise.custom_reporting.services import relation_service

    task = _task()
    monkeypatch.setattr(relation_service, "_relation_fingerprint", lambda _payload: "forced-collision")
    first = {
        "source": {"_id": 1, "model_id": "host"},
        "target": {"_id": 2, "model_id": "service"},
        "asst_id": "host_service",
        "mapping": "depends_on",
    }
    second = {
        "source": {"_id": 3, "model_id": "host"},
        "target": {"_id": 4, "model_id": "service"},
        "asst_id": "host_service",
        "mapping": "depends_on",
    }
    CustomReportingPendingRelation.objects.create(task=task, source_model_id="host", target_model_id="service", relation_payload=first)
    CustomReportingPendingRelation.objects.create(task=task, source_model_id="host", target_model_id="service", relation_payload=second)

    report = _run_audit("--apply-safe-fixes")

    codes = [finding["code"] for finding in report["findings"]]
    assert codes.count("pending_fingerprint_conflict") == 1
    assert PendingRelationDelivery.objects.filter(task=task, fingerprint="forced-collision").count() == 1
    assert PendingRelationDelivery.objects.get(task=task, fingerprint="forced-collision").relation_payload == first
