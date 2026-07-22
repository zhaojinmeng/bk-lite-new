from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.utils.timezone import now

from apps.cmdb.models.change_record import COLLECT_AUTOMATION_CHANGE, CREATE_INST, ChangeRecord
from apps.cmdb.models.operation import ChangeRecordMirrorOutbox
from apps.cmdb.services.change_record_mirror import ChangeRecordMirrorService
from apps.cmdb.utils.change_record import batch_create_change_record
from apps.system_mgmt.models.operation_log import OperationLog
from apps.system_mgmt.nats_api import save_operation_log


def _records(count):
    return [
        {
            "inst_id": index,
            "model_id": "host",
            "after_data": {"_id": index, "model_id": "host", "inst_name": f"host-{index}"},
            "model_object": "模型实例",
            "message": f"自动采集新增实例 host-{index}",
        }
        for index in range(count)
    ]


def _payloads(count):
    return [
        {
            "username": "system",
            "source_ip": "127.0.0.1",
            "app": "cmdb",
            "action_type": "create",
            "summary": f"create host-{index}",
            "target_type": "host",
            "target_id": str(index),
            "detail": {"source": "change_record"},
        }
        for index in range(count)
    ]


@pytest.mark.django_db(transaction=True)
@patch("apps.cmdb.utils.change_record.SystemMgmt")
@patch("apps.cmdb.services.change_record_mirror.dispatch_change_record_mirror")
def test_batch_change_records_persist_bounded_outbox_without_sync_rpc(mock_dispatch, mock_sm):
    batch_create_change_record(
        "instance", CREATE_INST, _records(250), operator="system", scenario=COLLECT_AUTOMATION_CHANGE,
    )

    assert ChangeRecord.objects.count() == 250
    assert ChangeRecordMirrorOutbox.objects.count() == 3
    assert not mock_sm.return_value.save_operation_log.called
    assert mock_dispatch.call_count == 3


@pytest.mark.django_db(transaction=True)
@patch("apps.cmdb.services.change_record_mirror.SystemMgmt")
def test_outbox_worker_has_fixed_rpc_budget_and_marks_success(mock_sm):
    [outbox] = ChangeRecordMirrorService.enqueue_payloads(_payloads(100))
    mock_sm.return_value.save_operation_log.return_value = {"result": True}

    assert ChangeRecordMirrorService.consume(outbox.event_id, owner_token="worker-1") is True
    outbox.refresh_from_db()
    assert outbox.status == "success"
    assert mock_sm.return_value.save_operation_log.call_count == 100


@pytest.mark.django_db(transaction=True)
@patch("apps.cmdb.services.change_record_mirror.SystemMgmt")
def test_outbox_worker_retries_when_downstream_returns_result_false(mock_sm):
    [outbox] = ChangeRecordMirrorService.enqueue_payloads(_payloads(1))
    mock_sm.return_value.save_operation_log.return_value = {"result": False, "message": "receiver rejected"}

    assert ChangeRecordMirrorService.consume(outbox.event_id, owner_token="worker-1") is False

    outbox.refresh_from_db()
    assert outbox.status == "retry"
    assert outbox.last_error == "downstream operation log rejected"


@pytest.mark.django_db(transaction=True)
@patch("apps.cmdb.services.change_record_mirror.SystemMgmt")
def test_outbox_ack_loss_replay_uses_receiver_operation_event_idempotency(mock_sm):
    operation_event_id = uuid4()
    payload = {**_payloads(1)[0], "operation_event_id": str(operation_event_id)}
    [outbox] = ChangeRecordMirrorOutbox.objects.bulk_create([ChangeRecordMirrorOutbox(payloads=[payload])])
    calls = []

    def save_then_lose_first_ack(**kwargs):
        calls.append(kwargs)
        response = save_operation_log(**kwargs)
        if len(calls) == 1:
            ChangeRecordMirrorOutbox.objects.filter(id=outbox.id).update(
                owner_token="crashed-worker", lease_expires_at=now() - timedelta(seconds=1),
            )
        return response

    mock_sm.return_value.save_operation_log.side_effect = save_then_lose_first_ack

    assert ChangeRecordMirrorService.consume(outbox.event_id, owner_token="worker-1") is False
    outbox.refresh_from_db()
    assert outbox.status == "sending"
    assert ChangeRecordMirrorService.consume(outbox.event_id, owner_token="worker-2") is True

    outbox.refresh_from_db()
    assert outbox.status == "success"
    assert len(calls) == 2
    assert OperationLog.objects.filter(operation_event_id=operation_event_id).count() == 1


@pytest.mark.django_db(transaction=True)
@patch(
    "apps.cmdb.tasks.celery_tasks.consume_change_record_mirror_outbox.delay", side_effect=RuntimeError("broker secret should not escape"),
)
def test_broker_failure_keeps_outbox_pending_without_breaking_batch_write(_mock_delay):
    batch_create_change_record(
        "instance", CREATE_INST, _records(1), operator="system", scenario=COLLECT_AUTOMATION_CHANGE,
    )

    assert ChangeRecord.objects.count() == 1
    assert ChangeRecordMirrorOutbox.objects.filter(status="pending").count() == 1


def test_mirror_outbox_recovery_is_registered_in_beat():
    from apps.cmdb.config import CELERY_BEAT_SCHEDULE

    schedule = CELERY_BEAT_SCHEDULE["recover_change_record_mirror_outbox_task"]
    assert schedule["task"] == "apps.cmdb.tasks.celery_tasks.recover_change_record_mirror_outbox_task"
