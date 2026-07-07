from apps.operation_analysis.constants.constants import PERMISSION_DIRECTORY
from apps.operation_analysis.nats import nats as N


def _user_info(**overrides):
    data = {
        "team": 1,
        "user": "alice",
        "domain": "tenant.example",
        "is_superuser": False,
        "include_children": False,
        "group_tree": [{"id": 1, "subGroups": [{"id": 2, "subGroups": []}]}],
        "permission": {"ops-analysis": ["view-View"]},
    }
    data.update(overrides)
    return data


def test_module_data_rejects_missing_user_info():
    result = N.get_operation_analysis_module_data(PERMISSION_DIRECTORY, "topology", page=1, page_size=10, group_id=1)

    assert result["result"] is False
    assert "user_info" in result["message"]


def test_module_data_rejects_group_outside_user_scope(mocker):
    service = mocker.patch("apps.operation_analysis.nats.nats.DictDirectoryService.get_operation_analysis_module_data")
    result = N.get_operation_analysis_module_data(
        PERMISSION_DIRECTORY,
        "topology",
        page=1,
        page_size=10,
        group_id=2,
        user_info=_user_info(include_children=False),
    )

    assert result["result"] is False
    assert "组织" in result["message"]
    service.assert_not_called()


def test_module_data_allows_authorized_group(mocker):
    service = mocker.patch(
        "apps.operation_analysis.nats.nats.DictDirectoryService.get_operation_analysis_module_data",
        return_value={"count": 1, "items": [{"id": 1, "name": "拓扑C"}]},
    )
    result = N.get_operation_analysis_module_data(
        PERMISSION_DIRECTORY,
        "topology",
        page=1,
        page_size=10,
        group_id=2,
        user_info=_user_info(include_children=True),
    )

    assert result["count"] == 1
    assert result["items"][0]["name"] == "拓扑C"
    service.assert_called_once_with(module=PERMISSION_DIRECTORY, child_module="topology", page=1, page_size=10, group_id=2)
