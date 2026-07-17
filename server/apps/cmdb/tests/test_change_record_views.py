"""CMDB 变更记录视图覆盖测试（真实 ChangeRecord DB）。

对照 spec/prd/CMDB·操作日志：变更记录列表/详情、变更类型与场景枚举、按过滤条件导出 Excel。
"""

import json

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.cmdb.models.change_record import (
    CUSTOM_REPORTING_CHANGE,
    DEVICE_LIFECYCLE,
    MODEL_MANAGEMENT_CHANGE,
    ORDINARY_ATTRIBUTE_CHANGE,
    ChangeRecord,
)
from apps.cmdb.utils import change_record as change_record_utils
from apps.cmdb.views.change_record import ChangeRecordViewSet


@pytest.fixture
def superuser(authenticated_user):
    u = authenticated_user
    u.is_superuser = True
    u.locale = "zh-Hans"
    return u


@pytest.fixture
def normal_user(authenticated_user):
    user = authenticated_user
    user.is_superuser = False
    user.locale = "zh-Hans"
    return user


@pytest.fixture
def record(db):
    return ChangeRecord.objects.create(
        inst_id=1, model_id="host", label="主机", type="create_entity",
        operator="admin", model_object="主机", message="创建实例",
        before_data={}, after_data={"inst_name": "h1"}, scenario="ordinary_attribute_change",
    )


def _req(method, user, query=""):
    factory = APIRequestFactory()
    path = "/x/" + (f"?{query}" if query else "")
    request = getattr(factory, method)(path)
    force_authenticate(request, user=user)
    return request


def _body(response):
    if hasattr(response, "render"):
        response.render()
        return json.loads(response.rendered_content)
    return json.loads(response.content)


@pytest.mark.django_db
def test_list(superuser, record):
    response = ChangeRecordViewSet.as_view({"get": "list"})(_req("get", superuser))
    assert response.status_code == 200


@pytest.mark.django_db
def test_retrieve(superuser, record):
    response = ChangeRecordViewSet.as_view({"get": "retrieve"})(_req("get", superuser), pk=record.id)
    assert response.status_code == 200
    assert _body(response)["data"]["model_id"] == "host"


@pytest.mark.django_db
def test_enum_data(superuser):
    response = ChangeRecordViewSet.as_view({"get": "enum_data"})(_req("get", superuser))
    body = _body(response)
    assert "create_entity" in body["data"]


@pytest.mark.django_db
def test_enum_scenarios(superuser):
    response = ChangeRecordViewSet.as_view({"get": "enum_scenarios"})(_req("get", superuser))
    body = _body(response)
    assert "ordinary_attribute_change" in body["data"]
    assert CUSTOM_REPORTING_CHANGE in body["data"]


@pytest.mark.django_db
def test_export(superuser, record):
    response = ChangeRecordViewSet.as_view({"get": "export"})(_req("get", superuser))
    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment")
    assert b"PK" == response.content[:2]  # xlsx 是 zip 容器


@pytest.mark.django_db
@pytest.mark.parametrize("permission", ["search-View", "asset_info-View"])
def test_home_recent_allows_home_read_permissions_without_snapshots(normal_user, record, permission):
    normal_user.permission = {"cmdb": {permission}}

    response = ChangeRecordViewSet.as_view({"get": "home_recent"})(_req("get", normal_user))

    body = _body(response)["data"]
    assert response.status_code == 200
    assert body["count"] == 1
    assert "before_data" not in body["items"][0]
    assert "after_data" not in body["items"][0]


@pytest.mark.django_db
def test_home_recent_filters_non_asset_scenarios_and_query_cannot_expand_scope(normal_user, record):
    normal_user.permission = {"cmdb": {"search-View"}}
    ChangeRecord.objects.create(
        inst_id=2,
        model_id="host",
        label="主机",
        type="update_entity",
        operator="admin",
        model_object="主机",
        message="修改模型",
        scenario=MODEL_MANAGEMENT_CHANGE,
    )

    response = ChangeRecordViewSet.as_view({"get": "home_recent"})(_req("get", normal_user))
    expanded_response = ChangeRecordViewSet.as_view({"get": "home_recent"})(
        _req("get", normal_user, query=f"scenarios={MODEL_MANAGEMENT_CHANGE}")
    )
    scenarios = {item["scenario"] for item in _body(response)["data"]["items"]}

    assert scenarios == {ORDINARY_ATTRIBUTE_CHANGE}
    assert _body(expanded_response)["data"]["count"] == 0


@pytest.mark.django_db
def test_home_recent_caps_page_size_and_generic_list_stays_denied(normal_user):
    normal_user.permission = {"cmdb": {"asset_info-View"}}
    ChangeRecord.objects.bulk_create([
        ChangeRecord(
            inst_id=index,
            model_id="host",
            label="主机",
            type="create_entity",
            operator="admin",
            model_object="主机",
            message=f"创建实例 {index}",
            scenario=DEVICE_LIFECYCLE,
        )
        for index in range(1, 106)
    ])

    home_response = ChangeRecordViewSet.as_view({"get": "home_recent"})(
        _req("get", normal_user, query="page=1&page_size=1000")
    )
    list_response = ChangeRecordViewSet.as_view({"get": "list"})(_req("get", normal_user))

    assert len(_body(home_response)["data"]["items"]) == 100
    assert list_response.status_code == 403


@pytest.mark.django_db
def test_home_recent_denies_user_without_home_read_permission(normal_user):
    normal_user.permission = {"cmdb": set()}

    response = ChangeRecordViewSet.as_view({"get": "home_recent"})(_req("get", normal_user))

    assert response.status_code == 403


@pytest.mark.django_db
def test_custom_reporting_change_record_helper_writes_custom_scenario():
    assert hasattr(change_record_utils, "create_custom_reporting_change_record")

    change_record_utils.create_custom_reporting_change_record(
        inst_id=99,
        model_id="report_asset",
        label="主机",
        _type="update_entity",
        after_data={"inst_name": "asset-99"},
        operator="custom-reporting-task-1",
        message="自定义上报更新",
    )

    record = ChangeRecord.objects.get(inst_id=99, model_id="report_asset")
    assert record.scenario == CUSTOM_REPORTING_CHANGE
