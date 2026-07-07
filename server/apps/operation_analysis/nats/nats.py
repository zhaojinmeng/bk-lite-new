# -- coding: utf-8 --
# @File: nats.py
# @Time: 2025/9/4 11:36
# @Author: windyzhao
import nats_client
from apps.core.utils.viewset_utils import GenericViewSetFun
from apps.operation_analysis.constants.constants import PERMISSION_DIRECTORY, PERMISSION_DATASOURCE
from apps.operation_analysis.services.directory_service import DictDirectoryService


def _error(message):
    return {"result": False, "data": [], "message": message}


def _get_authorized_group_ids(user_info):
    try:
        current_team = int((user_info or {}).get("team"))
    except (TypeError, ValueError):
        return None

    group_ids = [current_team]
    if (user_info or {}).get("include_children"):
        child_group_ids = GenericViewSetFun.extract_child_group_ids(
            (user_info or {}).get("group_tree", []),
            current_team,
        )
        if child_group_ids:
            group_ids = child_group_ids
    return group_ids


@nats_client.register
def get_operation_analysis_module_data(module, child_module, page, page_size, group_id, user_info=None):
    """
    获取运维分析模块数据的NATS接口
    :param module: 模块名称
    :param child_module: 子模块名称
    :param page: 页码
    :param page_size: 每页大小
    :param group_id: 组ID
    :param user_info: 调用方用户与组织上下文
    :return: 模块数据
    """
    if not isinstance(user_info, dict):
        return _error("缺少 user_info")

    authorized_group_ids = _get_authorized_group_ids(user_info)
    try:
        target_group_id = int(group_id)
    except (TypeError, ValueError):
        return _error("group_id 格式错误")

    if not authorized_group_ids or target_group_id not in authorized_group_ids:
        return _error("无权访问该组织数据")

    result = DictDirectoryService.get_operation_analysis_module_data(module=module, child_module=child_module,page=page,
                                                                     page_size=page_size, group_id=group_id)
    return result


@nats_client.register
def get_operation_analysis_module_list():
    """
    获取运维分析模块列表的NATS接口
    :return: 模块列表
    """
    result = [
        {"name": PERMISSION_DIRECTORY, "display_name": "目录", "children":  [
            {"name": "dashboard", "display_name": "仪表盘"},
            {"name": "topology", "display_name": "拓扑图"},
            {"name": "architecture", "display_name": "架构图"}
        ]},
        {"name": PERMISSION_DATASOURCE, "display_name": "数据源", "children": []},
    ]
    return result
