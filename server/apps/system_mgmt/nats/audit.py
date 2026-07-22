# flake8: noqa
from time import sleep

from django.db import IntegrityError, OperationalError, connection

from .common import *  # noqa: F401,F403


@nats_client.register
def save_error_log(username, app, module, error_message, domain="domain.com"):
    """
    保存错误日志
    :param username: 用户名
    :param app: 应用模块
    :param module: 功能模块
    :param error_message: 错误信息
    :param domain: 域名
    """
    try:
        ErrorLog.objects.create(
            username=username,
            app=app,
            module=module,
            error_message=error_message,
            domain=domain,
        )
        return {"result": True, "message": "Error log saved successfully"}
    except Exception as e:
        logger.exception(f"Failed to save error log: {e}")
        return {"result": False, "message": str(e)}


@nats_client.register
def save_operation_log(
    username, source_ip, app, action_type, summary="", domain="domain.com",
    target_type="", target_id="", detail=None, operation_event_id=None,
):
    """
    保存操作日志
    :param username: 用户名
    :param source_ip: 源IP地址
    :param app: 应用模块
    :param action_type: 操作类型 (create/update/delete/execute)
    :param summary: 操作概要
    :param domain: 域名
    :param target_type: 操作目标类型（可选）
    :param target_id: 操作目标ID（可选）
    :param detail: 操作详情 JSON（可选，默认空字典）
    """
    try:
        # 验证 action_type 是否合法
        valid_actions = [
            OperationLog.ACTION_CREATE,
            OperationLog.ACTION_UPDATE,
            OperationLog.ACTION_DELETE,
            OperationLog.ACTION_EXECUTE,
        ]
        if action_type not in valid_actions:
            return {
                "result": False,
                "message": f"Invalid action_type. Must be one of: {', '.join(valid_actions)}",
            }

        defaults = {
            "username": username,
            "source_ip": source_ip,
            "app": app,
            "action_type": action_type,
            "summary": summary,
            "domain": domain,
            "target_type": target_type or "",
            "target_id": str(target_id or ""),
            "detail": detail or {},
        }
        if operation_event_id is None:
            OperationLog.objects.create(**defaults)
        else:
            for delay in (0, 0.005, 0.01, 0.02, 0.04, 0.08):
                try:
                    OperationLog.objects.get_or_create(operation_event_id=operation_event_id, defaults=defaults)
                    break
                except IntegrityError:
                    if OperationLog.objects.filter(operation_event_id=operation_event_id).exists():
                        break
                    raise
                except OperationalError:
                    if connection.vendor != "sqlite" or delay == 0.08:
                        raise
                    sleep(delay)
        return {"result": True, "message": "Operation log saved successfully"}
    except Exception as e:
        logger.exception(f"Failed to save operation log: {e}")
        return {"result": False, "message": str(e)}
