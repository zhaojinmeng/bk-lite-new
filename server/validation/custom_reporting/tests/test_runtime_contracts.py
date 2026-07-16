from unittest.mock import patch

import pytest

from apps.cmdb_enterprise.custom_reporting.models import CustomReportingTask
from apps.cmdb_enterprise.custom_reporting.provider import CustomReportingProvider
from apps.cmdb_enterprise.custom_reporting.services import credential_service, ingest_service
from apps.core.exceptions.base_app_exception import BaseAppException
from validation.custom_reporting.tests.factories import create_token_task, unique_crval_name

TASKS_URL = "/api/v1/cmdb/api/custom_reporting/tasks/"


def _payload(*, team=None):
    return {
        "name": unique_crval_name("task"),
        "team": list(team or [1]),
        "config": {
            "mode": "standard",
            "model_id": unique_crval_name("model"),
            "identity_keys": ["inst_name"],
        },
        "is_enabled": True,
    }


def _authorize(api_client, authenticated_user, *permissions):
    authenticated_user.permission = {"cmdb": set(permissions)}
    authenticated_user.is_superuser = False
    api_client.force_authenticate(authenticated_user)


@pytest.fixture
def allowed_org_one(monkeypatch):
    monkeypatch.setattr(
        CustomReportingProvider,
        "_allowed_orgs",
        staticmethod(lambda request: [1]),
    )


@pytest.mark.django_db
@pytest.mark.xfail(strict=True, reason="CRV-F01")
def test_create_rejects_team_outside_requester_scope(
    api_client,
    authenticated_user,
    allowed_org_one,
):
    _authorize(api_client, authenticated_user, "model_management-Add Model")
    before = CustomReportingTask.objects.count()

    response = api_client.post(TASKS_URL, _payload(team=[2]), format="json")

    assert (response.status_code, CustomReportingTask.objects.count()) == (403, before)


@pytest.mark.django_db
@pytest.mark.xfail(strict=True, reason="CRV-F01")
def test_update_rejects_moving_task_outside_requester_scope(
    api_client,
    authenticated_user,
    allowed_org_one,
):
    token_task = create_token_task(team=[1])
    _authorize(api_client, authenticated_user, "model_management-Edit Model")

    response = api_client.put(
        f"{TASKS_URL}{token_task.task.id}/",
        {"team": [2]},
        format="json",
    )

    token_task.task.refresh_from_db()
    assert (response.status_code, token_task.task.team) == (403, [1])


@pytest.mark.django_db
def test_list_allows_model_management_view_permission(
    api_client,
    authenticated_user,
    allowed_org_one,
):
    create_token_task(team=[1])
    _authorize(api_client, authenticated_user, "model_management-View")

    response = api_client.get(TASKS_URL)

    assert response.status_code == 200


@pytest.mark.django_db
@pytest.mark.xfail(strict=True, reason="CRV-F02")
def test_list_rejects_user_without_model_management_view_permission(
    api_client,
    authenticated_user,
    allowed_org_one,
):
    create_token_task(team=[1])
    _authorize(api_client, authenticated_user)

    response = api_client.get(TASKS_URL)

    assert response.status_code == 403


@pytest.mark.django_db
def test_create_allows_model_management_add_permission(
    api_client,
    authenticated_user,
    allowed_org_one,
):
    _authorize(api_client, authenticated_user, "model_management-Add Model")
    payload = _payload(team=[1])

    response = api_client.post(TASKS_URL, payload, format="json")

    assert response.status_code == 200
    assert CustomReportingTask.objects.filter(name=payload["name"], team=[1]).exists()


@pytest.mark.django_db
@pytest.mark.xfail(strict=True, reason="CRV-F02")
def test_create_rejects_user_without_model_management_add_permission(
    api_client,
    authenticated_user,
    allowed_org_one,
):
    _authorize(api_client, authenticated_user, "model_management-View")
    payload = _payload(team=[1])

    response = api_client.post(TASKS_URL, payload, format="json")

    assert (
        response.status_code,
        CustomReportingTask.objects.filter(name=payload["name"]).exists(),
    ) == (403, False)


@pytest.mark.django_db
def test_update_allows_model_management_edit_permission(
    api_client,
    authenticated_user,
    allowed_org_one,
):
    token_task = create_token_task(team=[1])
    _authorize(api_client, authenticated_user, "model_management-Edit Model")
    new_name = unique_crval_name("updated_task")

    response = api_client.put(
        f"{TASKS_URL}{token_task.task.id}/",
        {"name": new_name},
        format="json",
    )

    assert response.status_code == 200
    token_task.task.refresh_from_db()
    assert token_task.task.name == new_name


@pytest.mark.django_db
@pytest.mark.xfail(strict=True, reason="CRV-F02")
def test_update_rejects_user_without_model_management_edit_permission(
    api_client,
    authenticated_user,
    allowed_org_one,
):
    token_task = create_token_task(team=[1])
    _authorize(api_client, authenticated_user, "model_management-View")
    original_name = token_task.task.name

    response = api_client.put(
        f"{TASKS_URL}{token_task.task.id}/",
        {"name": unique_crval_name("updated_task")},
        format="json",
    )

    token_task.task.refresh_from_db()
    assert (response.status_code, token_task.task.name) == (403, original_name)


def _successful_merge(*args, **kwargs):
    return {
        "created": 0,
        "updated": 0,
        "deleted": 0,
        "errors": 0,
        "covered_ids": [],
        "old_data": [],
        "index": {},
    }


@pytest.mark.django_db
def test_factory_token_is_accepted_by_ingest_capability():
    token_task = create_token_task()

    with patch.object(ingest_service.merge_service, "merge_instances", _successful_merge):
        result = ingest_service.ingest(
            token_task.raw_token,
            {"instances": [], "relations": []},
            operator="crval_runtime",
        )

    assert result["summary"]["errors"] == 0
    token_task.task.refresh_from_db()
    assert token_task.task.last_reported_at is not None


@pytest.mark.django_db
def test_rotating_factory_token_invalidates_old_and_accepts_new():
    token_task = create_token_task()
    credential = token_task.task.credentials.get()

    rotated = credential_service.rotate(token_task.task.id, credential.id)

    with pytest.raises(BaseAppException, match="无效|作废"):
        ingest_service.ingest(token_task.raw_token, {"instances": []})
    with patch.object(ingest_service.merge_service, "merge_instances", _successful_merge):
        result = ingest_service.ingest(rotated["token"], {"instances": [], "relations": []})
    assert result["summary"]["errors"] == 0


@pytest.mark.django_db
def test_revoking_factory_token_blocks_ingest_capability():
    token_task = create_token_task()
    credential = token_task.task.credentials.get()

    credential_service.revoke(token_task.task.id, credential.id)

    with pytest.raises(BaseAppException, match="无效|作废"):
        ingest_service.ingest(token_task.raw_token, {"instances": []})
