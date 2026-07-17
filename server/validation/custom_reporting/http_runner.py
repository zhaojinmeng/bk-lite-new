import argparse
import ipaddress
import json
import os
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode, urljoin, urlsplit

import requests

from validation.custom_reporting.ledger import ValidationLedger

CONNECT_TIMEOUT = 3.0
READ_TIMEOUT = 10.0
MAX_RESPONSE_BYTES = 1024 * 1024
TASK_SCAN_PAGE_SIZE = 200
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
SENSITIVE_KEY_PARTS = ("auth", "cookie", "password", "secret", "token")


class SafetyError(RuntimeError):
    pass


class HttpProtocolError(RuntimeError):
    pass


class CleanupIncompleteError(RuntimeError):
    pass


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class Transport(Protocol):
    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None,
        connect_timeout: float,
        read_timeout: float,
        max_response_bytes: int,
    ) -> HttpResponse:
        ...


class RequestsTransport:
    """Bounded requests adapter with environment proxies and redirects disabled."""

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.trust_env = False

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None,
        connect_timeout: float,
        read_timeout: float,
        max_response_bytes: int,
    ) -> HttpResponse:
        with self._session.request(
            method,
            url,
            headers=dict(headers),
            json=json_body,
            timeout=(connect_timeout, read_timeout),
            allow_redirects=False,
            stream=True,
        ) as response:
            body = bytearray()
            for chunk in response.iter_content(64 * 1024):
                body.extend(chunk)
                if len(body) > max_response_bytes:
                    break
            return HttpResponse(response.status_code, dict(response.headers), bytes(body))


def _redact(value: Any, known_values: Sequence[str] = ()) -> Any:
    known = tuple(item for item in known_values if item)
    if isinstance(value, Mapping):
        return {
            str(key): ("***" if any(part in str(key).lower() for part in SENSITIVE_KEY_PARTS) else _redact(item, known))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item, known) for item in value]
    if isinstance(value, str):
        redacted = value
        for secret in known:
            redacted = redacted.replace(secret, "***")
        return redacted
    return value


def _normalize_allowed_hosts(allowed_hosts: set[str]) -> frozenset[str]:
    normalized = frozenset(host.strip().lower() for host in allowed_hosts if host.strip())
    if not normalized or any("*" in host for host in normalized):
        raise SafetyError("CRV_ALLOWED_HOSTS 必须是非空精确 IP literal 列表，且禁止通配符")
    return normalized


def _validate_url(url: str, allowed_hosts: frozenset[str], *, execute: bool) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise SafetyError("URL scheme 仅允许 http/https")
    if parsed.username is not None or parsed.password is not None:
        raise SafetyError("URL 禁止 userinfo")
    hostname = (parsed.hostname or "").lower()
    if hostname not in allowed_hosts:
        raise SafetyError("URL host 不在 CRV_ALLOWED_HOSTS 精确允许列表")
    if not execute:
        return
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        raise SafetyError("execute 仅允许精确 loopback IP literal，禁止 DNS hostname") from None
    if not address.is_loopback:
        raise SafetyError("execute 仅允许 127/8 或 ::1 loopback 地址")


def _owned_identifier(run_id: str, identifier: Any) -> str:
    if type(identifier) is not int or identifier <= 0:
        raise HttpProtocolError("响应缺少合法正整数 id")
    return f"{run_id}:{identifier}"


def _parse_owned_int(run_id: str, identifier: Any) -> int:
    if not isinstance(identifier, str):
        raise SafetyError("账本资源 identifier 不是 owned identifier")
    prefix = f"{run_id}:"
    if not identifier.startswith(prefix):
        raise SafetyError("账本资源不属于当前 run_id")
    raw = identifier[len(prefix) :]
    if not raw.isascii() or not raw.isdecimal() or raw.startswith("0"):
        raise SafetyError("账本资源真实 id 格式非法")
    value = int(raw)
    if value <= 0:
        raise SafetyError("账本资源真实 id 格式非法")
    return value


class SafeHttpClient:
    def __init__(
        self,
        *,
        base_url: str,
        allowed_hosts: set[str],
        transport: Transport,
        write_enabled: bool,
        session_cookie: str | None,
        org_id: int | None,
    ) -> None:
        self.allowed_hosts = _normalize_allowed_hosts(allowed_hosts)
        self.base_url = base_url.rstrip("/") + "/"
        _validate_url(self.base_url, self.allowed_hosts, execute=write_enabled)
        self.transport = transport
        self.write_enabled = write_enabled
        self.session_cookie = session_cookie
        self.org_id = org_id
        self.requests_sent = 0

    def _management_headers(self) -> dict[str, str]:
        if not self.session_cookie or type(self.org_id) is not int or self.org_id <= 0:
            raise SafetyError("execute 管理请求必须提供 CRV_SESSION_COOKIE 与正整数 CRV_ORG_ID")
        cookie = self.session_cookie.strip().rstrip(";")
        if "\r" in cookie or "\n" in cookie:
            raise SafetyError("session cookie 格式非法")
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Cookie": f"{cookie}; current_team={self.org_id}",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
        ingest_token: str | None = None,
    ) -> dict[str, Any]:
        if not self.write_enabled:
            raise SafetyError("网络请求被三重执行门拒绝")
        url = urljoin(self.base_url, path)
        _validate_url(url, self.allowed_hosts, execute=True)
        if ingest_token is None:
            headers = self._management_headers()
        else:
            if not ingest_token:
                raise SafetyError("ingest 缺少 token")
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {ingest_token}",
            }
        try:
            response = self.transport.request(
                method=method,
                url=url,
                headers=headers,
                json_body=payload,
                connect_timeout=CONNECT_TIMEOUT,
                read_timeout=READ_TIMEOUT,
                max_response_bytes=MAX_RESPONSE_BYTES,
            )
        except Exception:
            raise HttpProtocolError("HTTP transport failed") from None
        self.requests_sent += 1
        if len(response.body) > MAX_RESPONSE_BYTES:
            raise HttpProtocolError("HTTP 响应体超过上限")
        if response.status in REDIRECT_STATUSES:
            raise HttpProtocolError("HTTP 重定向已拒绝，禁止重放请求")
        if not 200 <= response.status < 300:
            raise HttpProtocolError(f"HTTP {response.status}")
        try:
            decoded = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HttpProtocolError("HTTP 响应不是合法 JSON") from None
        if (
            not isinstance(decoded, dict)
            or set(decoded) != {"result", "data", "message"}
            or type(decoded["result"]) is not bool
            or not isinstance(decoded["message"], str)
            or not isinstance(decoded["data"], dict)
        ):
            raise HttpProtocolError("HTTP 响应不符合 WebUtils envelope")
        if decoded["result"] is not True:
            raise HttpProtocolError("WebUtils result=false")
        return decoded["data"]

    def create_task(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._request("POST", "tasks/", payload=payload)

    def delete_task(self, task_id: int) -> dict[str, Any]:
        return self._request("DELETE", f"tasks/{task_id}/")

    def ingest(self, payload: Mapping[str, Any], token: str) -> dict[str, Any]:
        return self._request("POST", "ingest/", payload=payload, ingest_token=token)

    def rotate_credential(self, task_id: int, credential_id: int) -> dict[str, Any]:
        return self._request(
            "POST",
            f"tasks/{task_id}/rotate_credential/",
            payload={"credential_id": credential_id},
        )

    def revoke_credential(self, task_id: int, credential_id: int) -> dict[str, Any]:
        return self._request(
            "POST",
            f"tasks/{task_id}/revoke_credential/",
            payload={"credential_id": credential_id},
        )

    def list_tasks(self, name: str, page: int = 1) -> dict[str, Any]:
        query = urlencode({"name": name, "page": page, "page_size": TASK_SCAN_PAGE_SIZE})
        return self._request("GET", f"tasks/?{query}")


@dataclass(frozen=True)
class PlanStep:
    name: str
    method: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class ExecutionPlan:
    run_id: str
    mode: str
    steps: tuple[PlanStep, ...]
    cleanup_order: tuple[str, ...]

    @property
    def resource_names(self) -> tuple[str, ...]:
        names = []
        for step in self.steps:
            name = step.payload.get("name")
            if isinstance(name, str):
                names.append(name)
        return tuple(names)

    def to_safe_dict(self, known_values: Sequence[str] = ()) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "steps": [
                {
                    "name": step.name,
                    "method": step.method,
                    "payload": _redact(step.payload, known_values),
                }
                for step in self.steps
            ],
            "cleanup_order": list(self.cleanup_order),
            "resource_names": list(self.resource_names),
        }


def _quick_task_payload(run_id: str, org_id: int, classification_id: str) -> dict[str, Any]:
    model_name = f"{run_id}_model"
    return {
        "name": f"{run_id}_quick_task",
        "team": [org_id],
        "config": {
            "mode": "quick",
            "cleanup_strategy": "none",
            "identity_keys": ["inst_name"],
        },
        "quick_model": {
            "model_id": model_name.lower(),
            "model_name": model_name,
            "classification_id": classification_id,
            "identity_keys": ["inst_name"],
        },
        "is_enabled": True,
    }


def _standard_task_payload(run_id: str, org_id: int, model_id: str) -> dict[str, Any]:
    return {
        "name": f"{run_id}_standard_task",
        "team": [org_id],
        "config": {
            "mode": "standard",
            "model_id": model_id,
            "cleanup_strategy": "none",
            "identity_keys": ["inst_name"],
        },
        "is_enabled": True,
    }


def _ingest_payload(run_id: str, suffix: str) -> dict[str, Any]:
    return {
        "instances": [
            {
                "inst_name": f"{run_id}_{suffix}",
                "crv_run_id": run_id,
            }
        ],
        "relations": [],
    }


def _relation_ingest_payloads(run_id: str, model_id: str, model_asst_id: str) -> tuple[dict[str, Any], ...]:
    immediate_source = f"{run_id}_immediate_source"
    immediate_target = f"{run_id}_immediate_target"
    pending_source = f"{run_id}_pending_source"
    backfill_target = f"{run_id}_backfill_target"

    def identity(inst_name: str) -> dict[str, Any]:
        return {"model_id": model_id, "identity": {"inst_name": inst_name}}

    return (
        {
            "instances": [
                {"inst_name": immediate_source, "crv_run_id": run_id},
                {"inst_name": immediate_target, "crv_run_id": run_id},
            ],
            "relations": [
                {
                    "source": identity(immediate_source),
                    "target": identity(immediate_target),
                    "asst_id": model_asst_id,
                }
            ],
        },
        {
            "instances": [{"inst_name": pending_source, "crv_run_id": run_id}],
            "relations": [
                {
                    "source": identity(pending_source),
                    "target": identity(backfill_target),
                    "asst_id": model_asst_id,
                }
            ],
        },
        {
            "instances": [{"inst_name": backfill_target, "crv_run_id": run_id}],
            "relations": [],
        },
    )


def build_execution_plan(
    mode: str,
    run_id: str,
    org_id: int = 1,
    classification_id: str = "other",
) -> ExecutionPlan:
    if mode not in {"quick", "standard"}:
        raise ValueError("mode 仅允许 quick/standard")
    quick = _quick_task_payload(run_id, org_id, classification_id)
    model_id = quick["quick_model"]["model_id"]
    model_asst_id = f"{model_id}_crv_rel_{model_id}"
    immediate_payload, pending_payload, backfill_payload = _relation_ingest_payloads(run_id, model_id, model_asst_id)
    steps = [PlanStep("create_quick_task", "POST", quick)]
    if mode == "standard":
        steps.extend(
            [
                PlanStep("delete_seed_task", "DELETE", {}),
                PlanStep("create_standard_task", "POST", {"name": f"{run_id}_standard_task"}),
            ]
        )
    steps.extend(
        [
            PlanStep("ingest_immediate_relation", "POST", immediate_payload),
            PlanStep("ingest_pending_relation", "POST", pending_payload),
            PlanStep("ingest_backfill_target", "POST", backfill_payload),
            PlanStep("rotate_credential", "POST", {}),
            PlanStep("ingest_with_rotated_token", "POST", _ingest_payload(run_id, "after_rotate")),
            PlanStep("revoke_credential", "POST", {}),
            PlanStep("verify_old_token_rejected", "POST", {"instances": [], "relations": []}),
            PlanStep("verify_revoked_token_rejected", "POST", {"instances": [], "relations": []}),
        ]
    )
    return ExecutionPlan(run_id, mode, tuple(steps), ("task", "model_verification"))


class HttpRunner:
    def __init__(
        self,
        *,
        base_url: str,
        allowed_hosts: set[str],
        transport: Transport,
        ledger: ValidationLedger,
        ledger_path: Path,
        execute: bool = False,
        cli_execute: bool = False,
        session_cookie: str | None = None,
        org_id: int | None = None,
        classification_id: str = "other",
        **_ignored: Any,
    ) -> None:
        self.ledger = ledger
        self.ledger_path = ledger_path
        self.org_id = org_id
        self.classification_id = classification_id
        self.write_enabled = execute is True and cli_execute is True and os.environ.get("CRV_ALLOW_WRITE") == "1"
        self.client = SafeHttpClient(
            base_url=base_url,
            allowed_hosts=allowed_hosts,
            transport=transport,
            write_enabled=self.write_enabled,
            session_cookie=session_cookie,
            org_id=org_id,
        )

    def reserve_ledger(self) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.ledger_path.open("x", encoding="utf-8") as stream:
                stream.write(self.ledger.to_json())
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            raise SafetyError("账本路径已存在，拒绝覆盖") from None

    def _persist_ledger(self) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.ledger_path.with_name(f".{self.ledger_path.name}.{secrets.token_hex(8)}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                stream.write(self.ledger.to_json())
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.ledger_path)
        finally:
            temporary.unlink(missing_ok=True)

    def _record_owned(self, kind: str, identifier: Any) -> None:
        self.ledger.record(kind, _owned_identifier(self.ledger.run_id, identifier))
        self._persist_ledger()

    @staticmethod
    def _created_task(data: Mapping[str, Any]) -> tuple[int, int, str, str]:
        task_id = data.get("id")
        credential = data.get("credential")
        config = data.get("config")
        raw_token = data.get("token")
        if (
            type(task_id) is not int
            or task_id <= 0
            or not isinstance(credential, dict)
            or type(credential.get("id")) is not int
            or credential["id"] <= 0
            or not isinstance(config, dict)
            or not isinstance(config.get("model_id"), str)
            or not config["model_id"]
            or not isinstance(raw_token, str)
            or not raw_token
        ):
            raise HttpProtocolError("任务创建响应缺少真实 id/config.model_id/credential/token")
        return task_id, credential["id"], config["model_id"], raw_token

    def _create_task(self, payload: Mapping[str, Any], *, expected_model_id: str) -> tuple[int, int, str, str]:
        created = self.client.create_task(payload)
        task_id, credential_id, model_id, raw_token = self._created_task(created)
        self._record_owned("task", task_id)
        self._record_owned("credential", credential_id)
        if model_id != expected_model_id:
            raise HttpProtocolError("任务创建响应 config.model_id 与请求不一致")
        self.ledger.record("model", model_id)
        self._persist_ledger()
        return task_id, credential_id, model_id, raw_token

    def _ingest_and_record(
        self,
        payload: Mapping[str, Any],
        token: str,
        *,
        expected_instances: int,
        expected_relations: int,
        expected_pending: int,
    ) -> None:
        data = self.client.ingest(payload, token)
        batch_id = data.get("batch_id")
        summary = data.get("summary")
        summary_keys = {
            "instances_received",
            "relations_received",
            "created",
            "updated",
            "deleted",
            "errors",
            "pending_relations",
        }
        if (
            type(batch_id) is not int
            or batch_id <= 0
            or not isinstance(summary, dict)
            or set(summary) != summary_keys
            or any(type(summary[key]) is not int or summary[key] < 0 for key in summary_keys)
            or summary["instances_received"] != expected_instances
            or summary["relations_received"] != expected_relations
            or summary["pending_relations"] != expected_pending
            or summary["errors"] != 0
        ):
            raise HttpProtocolError("ingest 响应 batch/summary 与请求计划不一致")
        self._record_owned("batch", batch_id)

    def _expect_ingest_rejected(self, token: str) -> None:
        try:
            self.client.ingest({"instances": [], "relations": []}, token)
        except HttpProtocolError as exc:
            if str(exc) == "WebUtils result=false" or str(exc) in {"HTTP 401", "HTTP 403"}:
                return
            raise
        raise HttpProtocolError("已吊销 token 仍被 ingest 接受")

    def _execute(self, mode: str) -> None:
        if type(self.org_id) is not int or self.org_id <= 0:
            raise SafetyError("CRV_ORG_ID 必须是正整数")
        seed_payload = _quick_task_payload(self.ledger.run_id, self.org_id, self.classification_id)
        expected_model_id = seed_payload["quick_model"]["model_id"]
        task_id, credential_id, model_id, current_token = self._create_task(seed_payload, expected_model_id=expected_model_id)
        if mode == "standard":
            self.client.delete_task(task_id)
            standard_payload = _standard_task_payload(self.ledger.run_id, self.org_id, model_id)
            task_id, credential_id, _model_id, current_token = self._create_task(standard_payload, expected_model_id=model_id)
        model_asst_id = f"{model_id}_crv_rel_{model_id}"
        immediate_payload, pending_payload, backfill_payload = _relation_ingest_payloads(self.ledger.run_id, model_id, model_asst_id)
        self._ingest_and_record(
            immediate_payload,
            current_token,
            expected_instances=2,
            expected_relations=1,
            expected_pending=0,
        )
        self._ingest_and_record(
            pending_payload,
            current_token,
            expected_instances=1,
            expected_relations=1,
            expected_pending=1,
        )
        self._ingest_and_record(
            backfill_payload,
            current_token,
            expected_instances=1,
            expected_relations=0,
            expected_pending=0,
        )
        rotated = self.client.rotate_credential(task_id, credential_id)
        rotated_credential = rotated.get("credential")
        new_token = rotated.get("token")
        if (
            not isinstance(rotated_credential, dict)
            or rotated_credential.get("id") != credential_id
            or not isinstance(new_token, str)
            or not new_token
        ):
            raise HttpProtocolError("凭据轮换响应形态错误")
        self._ingest_and_record(
            _ingest_payload(self.ledger.run_id, "after_rotate"),
            new_token,
            expected_instances=1,
            expected_relations=0,
            expected_pending=0,
        )
        revoked = self.client.revoke_credential(task_id, credential_id)
        if revoked.get("credential_id") != credential_id or revoked.get("is_enabled") is not False:
            raise HttpProtocolError("凭据吊销响应形态错误")
        self._expect_ingest_rejected(current_token)
        self._expect_ingest_rejected(new_token)

    def run(self, *, mode: str = "quick", token: str | None = None) -> dict[str, Any]:
        del token
        plan = build_execution_plan(
            mode,
            self.ledger.run_id,
            self.org_id if type(self.org_id) is int and self.org_id > 0 else 1,
            self.classification_id,
        )
        result = plan.to_safe_dict((self.client.session_cookie or "", os.environ.get("CRV_TOKEN", "")))
        result.update({"dry_run": not self.write_enabled, "requests_sent": 0})
        if self.write_enabled:
            self.reserve_ledger()
            self._execute(mode)
            result["requests_sent"] = self.client.requests_sent
        return _redact(result, (self.client.session_cookie or "",))

    def _scan_owned_tasks(self) -> dict[int, str]:
        data = self.client.list_tasks(self.ledger.run_id, 1)
        if set(data) != {"count", "next", "previous", "results"}:
            raise HttpProtocolError("任务列表分页结构错误")
        results = data["results"]
        if (
            type(data["count"]) is not int
            or data["count"] < 0
            or not isinstance(results, list)
            or data["count"] != len(results)
            or data["count"] > TASK_SCAN_PAGE_SIZE
            or data["next"] is not None
        ):
            raise HttpProtocolError("任务列表分页结构错误")
        allowed_names = {
            f"{self.ledger.run_id}_quick_task",
            f"{self.ledger.run_id}_standard_task",
        }
        owned: dict[int, str] = {}
        for item in results:
            if not isinstance(item, dict) or type(item.get("id")) is not int or item["id"] <= 0 or not isinstance(item.get("name"), str):
                raise HttpProtocolError("任务列表 item 结构错误")
            if item["name"] not in allowed_names or item["id"] in owned:
                raise HttpProtocolError("任务列表包含名称异常或重复任务")
            owned[item["id"]] = item["name"]
        return owned

    def _ledger_task_names(self) -> dict[int, str]:
        task_ids = [_parse_owned_int(self.ledger.run_id, resource.identifier) for resource in self.ledger.resources if resource.kind == "task"]
        if len(task_ids) > 2 or len(task_ids) != len(set(task_ids)):
            raise SafetyError("账本 task 资源数量或身份异常")
        names = (f"{self.ledger.run_id}_quick_task", f"{self.ledger.run_id}_standard_task")
        return dict(zip(task_ids, names))

    def cleanup(self, token: str | None = None) -> None:
        del token
        self._persist_ledger()
        try:
            ledger_tasks = self._ledger_task_names()
            existing_tasks = self._scan_owned_tasks()
            for task_id, name in existing_tasks.items():
                if ledger_tasks.get(task_id) != name:
                    raise CleanupIncompleteError("任务列表存在不在账本或名称不匹配的本 run 任务")
            for resource in self.ledger.cleanup_plan():
                if resource.kind == "task":
                    task_id = _parse_owned_int(self.ledger.run_id, resource.identifier)
                    if task_id in existing_tasks:
                        self.client.delete_task(task_id)
                elif resource.kind in {"credential", "batch", "model"}:
                    continue
                else:
                    raise SafetyError(f"cleanup 禁止处理不可证明资源: {resource.kind}")
            if self._scan_owned_tasks():
                raise CleanupIncompleteError("清理后仍存在本 run 任务")
            if any(resource.kind == "model" for resource in self.ledger.resources):
                raise CleanupIncompleteError("模型图资源无法由真实 HTTP API 验证，账本已保留交 Task 9")
        except Exception:
            self._persist_ledger()
            raise
        self.ledger_path.unlink(missing_ok=True)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="安全自定义上报 HTTP E2E 驱动")
    parser.add_argument("--base-url", default=os.environ.get("CRV_BASE_URL"))
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--mode", choices=("quick", "standard"), default="quick")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--verify-ledger", action="store_true")
    parser.add_argument("--cleanup-ledger", action="store_true")
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    transport: Transport | None = None,
    **_ignored: Any,
) -> int:
    args = _parse_args(argv)
    if args.verify_ledger or args.cleanup_ledger:
        raise SystemExit("--verify-ledger/--cleanup-ledger 尚未实现，已拒绝执行")
    if not args.base_url:
        raise SystemExit("必须提供 --base-url 或 CRV_BASE_URL")
    allowed_hosts = {host for host in os.environ.get("CRV_ALLOWED_HOSTS", "").split(",") if host}
    try:
        org_id = int(os.environ["CRV_ORG_ID"]) if "CRV_ORG_ID" in os.environ else None
    except ValueError:
        raise SystemExit("CRV_ORG_ID 必须是正整数") from None
    confirmed = os.environ.get("CRV_EXECUTE_CONFIRMED") == "1"
    runner = HttpRunner(
        base_url=args.base_url,
        allowed_hosts=allowed_hosts,
        transport=transport or RequestsTransport(),
        ledger=ValidationLedger.create(),
        ledger_path=args.ledger,
        execute=confirmed,
        cli_execute=args.execute,
        session_cookie=os.environ.get("CRV_SESSION_COOKIE"),
        org_id=org_id,
        classification_id=os.environ.get("CRV_CLASSIFICATION_ID", "other"),
    )
    result = runner.run(mode=args.mode)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
