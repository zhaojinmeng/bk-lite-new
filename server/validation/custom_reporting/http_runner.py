import argparse
import ipaddress
import json
import os
import socket
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urlencode, urljoin, urlsplit

import requests

from validation.custom_reporting.ledger import ResourceRef, ValidationLedger

CONNECT_TIMEOUT = 3.0
READ_TIMEOUT = 10.0
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_REDIRECTS = 3
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
SECRET_KEYS = frozenset({"authorization", "cookie", "password", "secret", "token", "access_token"})
DELETE_PATHS = {
    "edge": "relations/{identifier}/",
    "instance": "instances/{identifier}/",
    "review": "reviews/{identifier}/",
    "pending": "pending/{identifier}/",
    "batch": "batches/{identifier}/",
    "credential": "credentials/{identifier}/",
    "task": "tasks/{identifier}/",
    "association": "associations/{identifier}/",
    "model": "models/{identifier}/",
}


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
    """Small, bounded requests adapter. Redirects are handled by SafeHttpClient."""

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


def _default_resolver(hostname: str) -> list[str]:
    return sorted({entry[4][0] for entry in socket.getaddrinfo(hostname, None)})


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): "***" if str(key).lower() in SECRET_KEYS else _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _normalize_allowed_hosts(allowed_hosts: set[str]) -> frozenset[str]:
    normalized = frozenset(host.strip().lower().rstrip(".") for host in allowed_hosts if host.strip())
    if not normalized or any("*" in host for host in normalized):
        raise SafetyError("CRV_ALLOWED_HOSTS 必须是非空精确 hostname 列表，且禁止通配符")
    return normalized


def _validate_url(url: str, allowed_hosts: frozenset[str], resolver: Callable[[str], Sequence[str]]) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise SafetyError("URL scheme 仅允许 http/https")
    if parsed.username is not None or parsed.password is not None:
        raise SafetyError("URL 禁止 userinfo")
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if hostname not in allowed_hosts:
        raise SafetyError("URL host 不在 CRV_ALLOWED_HOSTS 精确允许列表")
    try:
        addresses = list(resolver(hostname))
    except Exception:
        raise SafetyError("URL host DNS 解析失败") from None
    if not addresses:
        raise SafetyError("URL host DNS 解析结果为空")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            raise SafetyError("URL host DNS 返回非法地址") from None
        if not ip.is_global:
            raise SafetyError("URL host DNS 指向非公网地址")


class SafeHttpClient:
    def __init__(
        self,
        *,
        base_url: str,
        allowed_hosts: set[str],
        transport: Transport,
        write_enabled: bool,
        resolver: Callable[[str], Sequence[str]] = _default_resolver,
    ) -> None:
        self.allowed_hosts = _normalize_allowed_hosts(allowed_hosts)
        self.resolver = resolver
        self.base_url = base_url.rstrip("/") + "/"
        _validate_url(self.base_url, self.allowed_hosts, self.resolver)
        self.transport = transport
        self.write_enabled = write_enabled
        self.requests_sent = 0

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
        token: str | None = None,
        accepted_statuses: frozenset[int] | None = None,
    ) -> dict[str, Any]:
        if not self.write_enabled:
            raise SafetyError("网络写请求被三重执行门拒绝")
        url = urljoin(self.base_url, path)
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        for redirect_count in range(MAX_REDIRECTS + 1):
            _validate_url(url, self.allowed_hosts, self.resolver)
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
                if redirect_count == MAX_REDIRECTS:
                    raise HttpProtocolError("HTTP 重定向次数超过上限")
                location = next(
                    (value for key, value in response.headers.items() if key.lower() == "location"),
                    None,
                )
                if not location:
                    raise HttpProtocolError("HTTP 重定向缺少 Location")
                url = urljoin(url, location)
                _validate_url(url, self.allowed_hosts, self.resolver)
                continue
            accepted = accepted_statuses or frozenset(range(200, 300))
            if response.status not in accepted:
                raise HttpProtocolError(f"HTTP {response.status}")
            if not response.body:
                return {}
            try:
                decoded = json.loads(response.body)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise HttpProtocolError("HTTP 响应不是合法 JSON") from None
            if not isinstance(decoded, dict):
                raise HttpProtocolError("HTTP 响应必须是 JSON object")
            return _redact(decoded)
        raise HttpProtocolError("unreachable")

    def create_model(self, name: str, token: str | None = None) -> dict[str, Any]:
        return self._request("POST", "models/", payload={"name": name}, token=token)

    def create_task(self, name: str, model_id: str, token: str | None = None) -> dict[str, Any]:
        return self._request("POST", "tasks/", payload={"name": name, "model_id": model_id}, token=token)

    def register_fields(self, task_id: str, token: str | None = None) -> dict[str, Any]:
        return self._request("POST", f"tasks/{quote(task_id, safe='')}/fields/", payload={"fields": ["name", "ip"]}, token=token)

    def ingest(self, task_id: str, token: str | None = None) -> dict[str, Any]:
        return self._request("POST", f"tasks/{quote(task_id, safe='')}/ingest/", payload={"instances": [{"name": task_id}]}, token=token)

    def create_relation(self, task_id: str, token: str | None = None) -> dict[str, Any]:
        return self._request("POST", "relations/", payload={"task_id": task_id}, token=token)

    def rotate_token(self, task_id: str, token: str | None = None) -> dict[str, Any]:
        return self._request("POST", f"tasks/{quote(task_id, safe='')}/token/rotate/", payload={}, token=token)

    def revoke_token(self, task_id: str, token: str | None = None) -> dict[str, Any]:
        return self._request("POST", f"tasks/{quote(task_id, safe='')}/token/revoke/", payload={}, token=token)

    def delete(self, resource: ResourceRef, token: str | None = None) -> dict[str, Any]:
        template = DELETE_PATHS.get(resource.kind)
        if template is None:
            raise SafetyError(f"未知 cleanup kind: {resource.kind}")
        identifier = quote(str(resource.identifier), safe="")
        return self._request(
            "DELETE",
            template.format(identifier=identifier),
            token=token,
            accepted_statuses=frozenset({200, 202, 204, 404}),
        )

    def scan_residuals(self, run_id: str, identifiers: Sequence[str | int], token: str | None = None) -> dict[str, Any]:
        query = urlencode({"run_id": run_id, "ids": ",".join(str(identifier) for identifier in identifiers)})
        return self._request("GET", f"residuals/?{query}", token=token)


@dataclass(frozen=True)
class PlanStep:
    name: str
    method: str
    resource_kind: str | None
    resource_name: str | None
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class ExecutionPlan:
    run_id: str
    mode: str
    steps: tuple[PlanStep, ...]
    cleanup_order: tuple[str, ...]

    @property
    def resource_names(self) -> tuple[str, ...]:
        return tuple(step.resource_name for step in self.steps if step.resource_name is not None)

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "steps": [{"name": step.name, "method": step.method, "payload": _redact(step.payload)} for step in self.steps],
            "cleanup_order": list(self.cleanup_order),
            "resource_names": list(self.resource_names),
        }


def build_execution_plan(mode: str, run_id: str) -> ExecutionPlan:
    if mode not in {"quick", "standard"}:
        raise ValueError("mode 仅允许 quick/standard")
    model = f"{run_id}_model"
    seed_task = f"{run_id}_seed_task"
    standard_task = f"{run_id}_standard_task"
    steps = [
        PlanStep("create_seed_model", "POST", "model", model, {"name": model}),
        PlanStep("create_seed_task", "POST", "task", seed_task, {"name": seed_task, "model_id": model}),
    ]
    active_task = seed_task
    if mode == "standard":
        steps.extend(
            [
                PlanStep("delete_seed_task", "DELETE", None, seed_task, {"task_id": seed_task}),
                PlanStep(
                    "create_standard_task",
                    "POST",
                    "task",
                    standard_task,
                    {"name": standard_task, "model_id": model},
                ),
            ]
        )
        active_task = standard_task
    steps.extend(
        [
            PlanStep("register_fields", "POST", None, None, {"task_id": active_task}),
            PlanStep("ingest", "POST", "instance", None, {"task_id": active_task}),
            PlanStep("create_relation", "POST", "edge", None, {"task_id": active_task}),
            PlanStep("rotate_token", "POST", "credential", None, {"task_id": active_task}),
            PlanStep("revoke_token", "POST", None, None, {"task_id": active_task}),
        ]
    )
    return ExecutionPlan(run_id, mode, tuple(steps), ("edge", "instance", "credential", "task", "model"))


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
        resolver: Callable[[str], Sequence[str]] = _default_resolver,
    ) -> None:
        self.ledger = ledger
        self.ledger_path = ledger_path
        self.write_enabled = execute is True and cli_execute is True and os.environ.get("CRV_ALLOW_WRITE") == "1"
        self.client = SafeHttpClient(
            base_url=base_url,
            allowed_hosts=allowed_hosts,
            transport=transport,
            write_enabled=self.write_enabled,
            resolver=resolver,
        )

    def _persist_ledger(self) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.ledger_path.with_name(f".{self.ledger_path.name}.tmp")
        temporary.write_text(self.ledger.to_json())
        temporary.replace(self.ledger_path)

    def _record_response(self, kind: str, response: Mapping[str, Any], fallback: str | None = None) -> str | int:
        identifier = response.get("id", fallback)
        if type(identifier) not in (int, str):
            raise HttpProtocolError("创建响应缺少合法 id")
        self.ledger.record(kind, identifier)
        self._persist_ledger()
        return identifier

    def create_seed_model(self, token: str | None = None) -> dict[str, Any]:
        name = f"{self.ledger.run_id}_model"
        result = self.client.create_model(name, token=token)
        self._record_response("model", result, name)
        return result

    def _execute_plan(self, plan: ExecutionPlan, token: str | None) -> None:
        model = f"{self.ledger.run_id}_model"
        active_task = f"{self.ledger.run_id}_seed_task"
        for step in plan.steps:
            if step.name == "create_seed_model":
                self.create_seed_model(token)
            elif step.name in {"create_seed_task", "create_standard_task"}:
                result = self.client.create_task(step.resource_name or "", model, token)
                active_task = step.resource_name or ""
                self._record_response("task", result, active_task)
            elif step.name == "delete_seed_task":
                self.client.delete(ResourceRef("task", step.resource_name or ""), token)
            elif step.name == "register_fields":
                self.client.register_fields(active_task, token)
            elif step.name == "ingest":
                result = self.client.ingest(active_task, token)
                self._record_response("instance", result)
            elif step.name == "create_relation":
                result = self.client.create_relation(active_task, token)
                self._record_response("edge", result)
            elif step.name == "rotate_token":
                result = self.client.rotate_token(active_task, token)
                self._record_response("credential", result)
            elif step.name == "revoke_token":
                self.client.revoke_token(active_task, token)

    def run(self, *, mode: str = "quick", token: str | None = None) -> dict[str, Any]:
        plan = build_execution_plan(mode, self.ledger.run_id)
        result = plan.to_safe_dict()
        result.update({"dry_run": not self.write_enabled, "requests_sent": 0})
        if self.write_enabled:
            if self.ledger_path.exists():
                raise SafetyError("账本路径已存在，拒绝覆盖")
            self._execute_plan(plan, token)
            result["requests_sent"] = self.client.requests_sent
        return result

    def cleanup(self, token: str | None = None) -> None:
        self._persist_ledger()
        try:
            cleanup_plan = self.ledger.cleanup_plan()
            for resource in cleanup_plan:
                self.client.delete(resource, token)
            residuals = self.client.scan_residuals(
                self.ledger.run_id,
                [resource.identifier for resource in cleanup_plan],
                token,
            )
            if residuals.get("items"):
                raise CleanupIncompleteError("清理后仍存在本 run 残留")
        except Exception as exc:
            self._persist_ledger()
            if isinstance(exc, CleanupIncompleteError):
                raise
            raise CleanupIncompleteError("清理未完成，账本已保留") from None
        self.ledger_path.unlink(missing_ok=True)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="安全自定义上报 HTTP E2E 驱动")
    parser.add_argument("--base-url", default=os.environ.get("CRV_BASE_URL"))
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--mode", choices=("quick", "standard"), default="quick")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True)
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--verify-ledger", action="store_true")
    parser.add_argument("--cleanup-ledger", action="store_true")
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    transport: Transport | None = None,
    resolver: Callable[[str], Sequence[str]] = _default_resolver,
) -> int:
    args = _parse_args(argv)
    if args.verify_ledger or args.cleanup_ledger:
        raise SystemExit("--verify-ledger/--cleanup-ledger 尚未实现，已拒绝执行")
    if not args.base_url:
        raise SystemExit("必须提供 --base-url 或 CRV_BASE_URL")
    allowed_hosts = {host for host in os.environ.get("CRV_ALLOWED_HOSTS", "").split(",") if host}
    ledger = ValidationLedger.create()
    http_runner = HttpRunner(
        base_url=args.base_url,
        allowed_hosts=allowed_hosts,
        transport=transport or RequestsTransport(),
        ledger=ledger,
        ledger_path=args.ledger,
        execute=args.execute,
        cli_execute=args.execute,
        resolver=resolver,
    )
    result = http_runner.run(mode=args.mode, token=os.environ.get("CRV_TOKEN"))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
