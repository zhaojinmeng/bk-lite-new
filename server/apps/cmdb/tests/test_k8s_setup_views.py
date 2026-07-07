import json

import pytest
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.cmdb.views.k8s_setup import K8sSetupOpenViewSet, K8sSetupViewSet


def _request(user, data=None):
    request = APIRequestFactory().post("/x/", data=data or {}, format="json")
    request.COOKIES["current_team"] = "1"
    force_authenticate(request, user=user)
    return request


def _body(response):
    if hasattr(response, "render"):
        response.render()
        return json.loads(response.rendered_content)
    return json.loads(response.content)


@pytest.fixture
def normal_user(authenticated_user):
    authenticated_user.is_superuser = False
    authenticated_user.permission = {"cmdb": set()}
    return authenticated_user


@pytest.fixture
def auto_collection_user(authenticated_user):
    authenticated_user.is_superuser = False
    authenticated_user.permission = {"cmdb": {"auto_collection-Execute"}}
    return authenticated_user


@pytest.mark.django_db
def test_install_token_requires_auto_collection_execute_permission(normal_user, mocker):
    service = mocker.patch(
        "apps.cmdb.views.k8s_setup.K8sSetupService.generate_install_token",
        return_value={"token": "secret-token"},
    )

    response = K8sSetupViewSet.as_view({"post": "install_token"})(
        _request(normal_user, {"collector_cluster_id": "c1", "cloud_region_id": 1})
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    service.assert_not_called()


@pytest.mark.django_db
def test_install_command_allows_auto_collection_execute_permission(auto_collection_user, mocker):
    mocker.patch(
        "apps.cmdb.views.k8s_setup.K8sSetupService.generate_install_command",
        return_value={"command": "kubectl apply -f -"},
    )

    response = K8sSetupViewSet.as_view({"post": "install_command"})(
        _request(auto_collection_user, {"collector_cluster_id": "c1", "cloud_region_id": 1})
    )

    assert response.status_code == status.HTTP_200_OK
    assert _body(response)["data"]["command"] == "kubectl apply -f -"


@pytest.mark.django_db
def test_verify_requires_auto_collection_execute_permission(normal_user, mocker):
    service = mocker.patch(
        "apps.cmdb.views.k8s_setup.K8sSetupService.verify_collector_reporting",
        return_value={"is_reporting": True},
    )

    response = K8sSetupViewSet.as_view({"post": "verify"})(
        _request(normal_user, {"collector_cluster_id": "c1"})
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    service.assert_not_called()


@pytest.mark.django_db
def test_open_api_render_keeps_token_based_path(mocker):
    mocker.patch(
        "apps.cmdb.views.k8s_setup.K8sSetupService.render_yaml_by_token",
        return_value={"yaml": "apiVersion: v1\nkind: ConfigMap\n", "remaining_usage": 1},
    )

    response = K8sSetupOpenViewSet.as_view({"post": "render"})(
        APIRequestFactory().post("/x/", data={"token": "valid-token"}, format="json")
    )

    assert response.status_code == status.HTTP_200_OK
    assert response["X-Token-Remaining-Usage"] == "1"
    assert "apiVersion: v1" in response.content.decode()
