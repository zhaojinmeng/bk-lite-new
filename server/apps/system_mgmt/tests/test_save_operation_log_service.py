from queue import Queue
from threading import Barrier, Thread
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from django.db import close_old_connections

from apps.system_mgmt.models.operation_log import OperationLog
from apps.system_mgmt.nats_api import save_operation_log


@pytest.mark.django_db
def test_save_operation_log_persists_target_and_detail():
    res = save_operation_log(
        username="alice",
        source_ip="127.0.0.1",
        app="cmdb",
        action_type="update",
        summary="编辑模型",
        target_type="host",
        target_id="42",
        detail={"scenario": "model_management_change"},
    )
    assert res["result"] is True
    log = OperationLog.objects.get()
    assert (log.target_type, log.target_id) == ("host", "42")
    assert log.detail == {"scenario": "model_management_change"}


@pytest.mark.django_db
def test_save_operation_log_backward_compatible_without_new_params():
    res = save_operation_log(username="bob", source_ip="127.0.0.1", app="job", action_type="create", summary="x")
    assert res["result"] is True
    log = OperationLog.objects.get()
    assert log.target_type == "" and log.detail == {}


@pytest.mark.django_db
def test_save_operation_log_rejects_bad_action_type():
    res = save_operation_log(username="x", source_ip="127.0.0.1", app="cmdb", action_type="frobnicate")
    assert res["result"] is False


@pytest.mark.django_db(transaction=True)
def test_save_operation_log_repeated_operation_event_is_idempotent():
    operation_event_id = uuid4()
    payload = dict(
        username="idempotent", source_ip="127.0.0.1", app="cmdb", action_type="delete", summary="一次删除", operation_event_id=str(operation_event_id),
    )

    assert save_operation_log(**payload)["result"] is True
    assert save_operation_log(**payload)["result"] is True
    assert OperationLog.objects.filter(operation_event_id=operation_event_id).count() == 1


@pytest.mark.django_db(transaction=True)
def test_save_operation_log_concurrent_operation_event_is_idempotent():
    operation_event_id = uuid4()
    barrier = Barrier(2)
    outcomes = Queue()

    def save_from_connection():
        close_old_connections()
        try:
            barrier.wait(timeout=5)
            outcomes.put(
                save_operation_log(
                    username="concurrent",
                    source_ip="127.0.0.1",
                    app="cmdb",
                    action_type="delete",
                    summary="并发删除",
                    operation_event_id=str(operation_event_id),
                )
            )
        finally:
            close_old_connections()

    workers = [Thread(target=save_from_connection) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert not any(worker.is_alive() for worker in workers)
    assert [outcome["result"] for outcome in list(outcomes.queue)] == [True, True]
    assert OperationLog.objects.filter(operation_event_id=operation_event_id).count() == 1


@patch("apps.rpc.system_mgmt.AppClient")
def test_system_mgmt_rpc_forwards_operation_event_id(mock_client):
    from apps.rpc.system_mgmt import SystemMgmt

    operation_event_id = uuid4()
    client = Mock()
    mock_client.return_value = client

    SystemMgmt().save_operation_log(
        username="rpc", source_ip="127.0.0.1", app="cmdb", action_type="delete", operation_event_id=str(operation_event_id),
    )

    assert client.run.call_args.kwargs["operation_event_id"] == str(operation_event_id)
