from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


_RUN_ID_PATTERN = re.compile(r"^cmdb-[a-z0-9]{8,32}$")
_PROJECT_PATTERN = re.compile(r"^cmdb-collection-[a-z0-9]{8,32}$")
_ALLOWED_INTERNAL_HOSTS = {
    "localhost",
    "127.0.0.1",
    "::1",
    "nats",
    "victoriametrics",
    "falkordb",
}
_SERVICE_ENV_NAMES = (
    "CMDB_SMOKE_NATS_URL",
    "CMDB_SMOKE_VM_URL",
    "CMDB_SMOKE_FALKOR_URL",
)


class SmokeConfigurationError(ValueError):
    pass


class SmokeTimeoutError(TimeoutError):
    pass


@dataclass(frozen=True)
class SmokeSettings:
    run_id: str
    compose_project: str
    compose_file: Path
    artifact_dir: Path
    startup_timeout: float = 90.0
    poll_interval: float = 0.5
    shutdown_timeout: int = 15

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        artifact_root: Path | None = None,
    ) -> SmokeSettings:
        values = os.environ if environ is None else environ
        if values.get("CMDB_COLLECTION_SMOKE") != "1":
            raise SmokeConfigurationError(
                "真实采集 smoke 仅在显式设置 CMDB_COLLECTION_SMOKE=1 后运行"
            )

        run_id = values.get("CMDB_SMOKE_RUN_ID") or f"cmdb-{uuid.uuid4().hex[:12]}"
        project = values.get("COMPOSE_PROJECT_NAME") or f"cmdb-collection-{run_id[5:]}"
        if not _RUN_ID_PATTERN.fullmatch(run_id) or not _PROJECT_PATTERN.fullmatch(project):
            raise SmokeConfigurationError("run_id 或 Compose project 不满足安全所有权格式")
        if project != f"cmdb-collection-{run_id[5:]}":
            raise SmokeConfigurationError("Compose project 必须与 run_id 保持一对一所有权")

        for name in _SERVICE_ENV_NAMES:
            value = values.get(name)
            if value:
                cls._validate_service_url(name, value)

        root = artifact_root or Path(
            values.get("CMDB_SMOKE_ARTIFACT_ROOT", "/tmp/cmdb-collection-smoke")
        )
        compose_file = Path(__file__).with_name("compose.yaml").resolve()
        return cls(
            run_id=run_id,
            compose_project=project,
            compose_file=compose_file,
            artifact_dir=(root / run_id).resolve(),
            startup_timeout=cls._positive_float(
                values.get("CMDB_SMOKE_STARTUP_TIMEOUT", "90"),
                "CMDB_SMOKE_STARTUP_TIMEOUT",
            ),
            poll_interval=cls._positive_float(
                values.get("CMDB_SMOKE_POLL_INTERVAL", "0.5"),
                "CMDB_SMOKE_POLL_INTERVAL",
            ),
        )

    @staticmethod
    def _validate_service_url(name: str, value: str) -> None:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https", "nats", "redis"} or not parsed.hostname:
            raise SmokeConfigurationError(f"{name} 必须是带协议和主机的 URL")
        if parsed.hostname.lower() not in _ALLOWED_INTERNAL_HOSTS:
            raise SmokeConfigurationError(f"{name} 只允许本机回环或 Compose 内部地址")

    @staticmethod
    def _positive_float(value: str, name: str) -> float:
        try:
            result = float(value)
        except ValueError as exc:
            raise SmokeConfigurationError(f"{name} 必须是正数") from exc
        if result <= 0:
            raise SmokeConfigurationError(f"{name} 必须是正数")
        return result


@dataclass
class OwnershipLedger:
    run_id: str
    _resources: list[tuple[str, str, str]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    cleanup_errors: list[str] = field(default_factory=list)

    def record(self, kind: str, identifier: str, *, owner_run_id: str | None = None) -> None:
        if not kind or not identifier:
            raise ValueError("资源类型和标识不能为空")
        self._resources.append((kind, identifier, owner_run_id or self.run_id))

    def cleanup(self, remover: Callable[[str, str], None]) -> None:
        for kind, identifier, owner in reversed(self._resources):
            if owner != self.run_id:
                self.skipped.append((kind, identifier))
                continue
            try:
                remover(kind, identifier)
            except Exception as exc:
                self.cleanup_errors.append(f"{kind}:{identifier}: {exc}")


@dataclass(frozen=True)
class SmokeContext:
    settings: SmokeSettings
    ledger: OwnershipLedger


Execute = Callable[..., subprocess.CompletedProcess[str]]


class CollectionChainSmokeRunner:
    _REQUIRED_HEALTH = {
        "nats": "healthy",
        "victoriametrics": "healthy",
        "falkordb": "healthy",
    }
    _PUBLISHED_PORTS = {
        "nats": 4222,
        "victoriametrics": 8428,
        "falkordb": 6379,
    }

    def __init__(
        self,
        settings: SmokeSettings,
        *,
        execute: Execute = subprocess.run,
        monotonic: Callable[[], float] = time.monotonic,
        wait: Callable[[float], Any] | None = None,
        resource_remover: Callable[[str, str], None] | None = None,
    ) -> None:
        self.settings = settings
        self._execute = execute
        self._monotonic = monotonic
        self._wait = wait or threading.Event().wait
        self._resource_remover = resource_remover or self._missing_resource_remover
        self.ledger = OwnershipLedger(settings.run_id)

    def run(self, workload: Callable[[SmokeContext], Any]) -> Any:
        self.settings.artifact_dir.mkdir(parents=True, exist_ok=False)
        result: Any = None
        failure: BaseException | None = None
        failure_traceback = None
        try:
            self._compose("up", "-d", check=True)
            self._wait_until_healthy()
            result = workload(SmokeContext(self.settings, self.ledger))
        except BaseException as exc:
            failure = exc
            failure_traceback = exc.__traceback__

        cleanup_errors: list[str] = []
        try:
            self._capture_logs()
        except Exception as exc:
            cleanup_errors.append(f"保存 Compose 日志失败: {exc}")

        self.ledger.cleanup(self._resource_remover)
        cleanup_errors.extend(self.ledger.cleanup_errors)
        try:
            down_result = self._compose(
                "down",
                "--remove-orphans",
                "--timeout",
                str(self.settings.shutdown_timeout),
                check=False,
            )
            if down_result.returncode:
                cleanup_errors.append(
                    f"Compose 清理失败({down_result.returncode}): "
                    f"{down_result.stderr or down_result.stdout}"
                )
        except Exception as exc:
            cleanup_errors.append(f"执行 Compose 清理失败: {exc}")

        if cleanup_errors:
            (self.settings.artifact_dir / "cleanup-errors.log").write_text(
                "\n".join(cleanup_errors) + "\n",
                encoding="utf-8",
            )
        if failure is not None:
            raise failure.with_traceback(failure_traceback)
        if cleanup_errors:
            raise RuntimeError("smoke 工作完成但清理失败；详见 cleanup-errors.log")
        return result

    def published_endpoint(
        self,
        service: str,
        container_port: int,
        *,
        scheme: str = "http",
    ) -> str:
        if self._PUBLISHED_PORTS.get(service) != container_port:
            raise SmokeConfigurationError("只允许查询 smoke 栈声明的服务端口")
        if scheme not in {"http", "nats", "redis"}:
            raise SmokeConfigurationError("不支持的 smoke 服务协议")

        result = self._compose("port", service, str(container_port), check=False)
        binding = result.stdout.strip()
        try:
            parsed = urlparse(f"//{binding}")
            host = parsed.hostname
            port = parsed.port
        except ValueError as exc:
            raise SmokeConfigurationError("Docker 未返回安全的回环端口") from exc
        if result.returncode or host not in {"127.0.0.1", "::1"} or port is None:
            raise SmokeConfigurationError("Docker 未返回安全的回环端口")
        rendered_host = f"[{host}]" if ":" in host else host
        return f"{scheme}://{rendered_host}:{port}"

    def _wait_until_healthy(self) -> None:
        deadline = self._monotonic() + self.settings.startup_timeout
        while self._monotonic() < deadline:
            result = self._compose("ps", "--format", "json", check=False)
            if result.returncode == 0 and self._services_are_ready(result.stdout):
                return
            self._wait(self.settings.poll_interval)
        raise SmokeTimeoutError(
            f"Compose 服务未在 {self.settings.startup_timeout:g}s 内达到健康状态"
        )

    def _services_are_ready(self, output: str) -> bool:
        try:
            records = json.loads(output)
        except json.JSONDecodeError:
            try:
                records = [json.loads(line) for line in output.splitlines() if line.strip()]
            except json.JSONDecodeError:
                return False
        if isinstance(records, dict):
            records = [records]
        if not isinstance(records, list):
            return False

        states = {
            str(record.get("Service", "")): str(
                record.get("Health") or record.get("State") or ""
            ).lower()
            for record in records
            if isinstance(record, dict)
        }
        if any(states.get(service) != health for service, health in self._REQUIRED_HEALTH.items()):
            return False
        return states.get("telegraf") in {"running", "healthy"}

    def _capture_logs(self) -> None:
        result = self._compose(
            "logs",
            "--no-color",
            "--timestamps",
            check=False,
        )
        log_file = self.settings.artifact_dir / "compose.log"
        log_file.write_text(
            (result.stdout or "") + (result.stderr or ""),
            encoding="utf-8",
        )

    @staticmethod
    def _missing_resource_remover(kind: str, identifier: str) -> None:
        raise RuntimeError(f"未配置 {kind}:{identifier} 的精确资源清理器")

    def _compose(
        self,
        *arguments: str,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            "docker",
            "compose",
            "--file",
            str(self.settings.compose_file),
            "--project-name",
            self.settings.compose_project,
            *arguments,
        ]
        return self._execute(
            command,
            check=check,
            capture_output=True,
            text=True,
            timeout=self.settings.startup_timeout,
        )
