from __future__ import annotations

import json
import os
import re
import selectors
import signal
import socket
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse
from urllib.request import urlopen


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
_SERVICE_ENV_SCHEMES = {
    "CMDB_SMOKE_NATS_URL": "nats",
    "CMDB_SMOKE_VM_URL": "http",
    "CMDB_SMOKE_FALKOR_URL": "redis",
}


class SmokeConfigurationError(ValueError):
    pass


class SmokeTimeoutError(TimeoutError):
    pass


class SmokeCleanupError(RuntimeError):
    pass


class _DeadlineExpired(BaseException):
    pass


def bounded_command_output(
    command: list[str],
    *,
    timeout: float,
    max_bytes: int,
) -> str:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    output = bytearray()
    deadline = time.monotonic() + timeout
    timed_out = False
    try:
        while process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            if not selector.select(min(remaining, 0.1)):
                continue
            chunk = os.read(
                process.stdout.fileno(),
                min(65536, max_bytes + 1 - len(output)),
            )
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > max_bytes:
                break
        if process.poll() is not None and len(output) <= max_bytes:
            output.extend(process.stdout.read(max_bytes + 1 - len(output)))
    finally:
        selector.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
    if timed_out:
        raise subprocess.TimeoutExpired(command, timeout, output=bytes(output))
    return bytes(output[:max_bytes]).decode("utf-8", errors="replace")


@dataclass(frozen=True)
class SmokeSettings:
    run_id: str
    compose_project: str
    compose_file: Path
    artifact_dir: Path
    startup_timeout: float = 90.0
    poll_interval: float = 0.5
    shutdown_timeout: int = 15
    workload_timeout: float = 120.0
    canary_timeout: float = 30.0
    cleanup_timeout: float = 30.0
    command_timeout: float = 30.0
    log_timeout: float = 10.0
    log_max_bytes: int = 1_048_576

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
            raise SmokeConfigurationError(
                "run_id 或 Compose project 不满足安全所有权格式"
            )
        if project != f"cmdb-collection-{run_id[5:]}":
            raise SmokeConfigurationError(
                "Compose project 必须与 run_id 保持一对一所有权"
            )

        for name, scheme in _SERVICE_ENV_SCHEMES.items():
            value = values.get(name)
            if value:
                cls._validate_service_url(name, value, scheme)

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
            workload_timeout=cls._positive_float(
                values.get("CMDB_SMOKE_WORKLOAD_TIMEOUT", "120"),
                "CMDB_SMOKE_WORKLOAD_TIMEOUT",
            ),
            canary_timeout=cls._positive_float(
                values.get("CMDB_SMOKE_CANARY_TIMEOUT", "30"),
                "CMDB_SMOKE_CANARY_TIMEOUT",
            ),
            cleanup_timeout=cls._positive_float(
                values.get("CMDB_SMOKE_CLEANUP_TIMEOUT", "30"),
                "CMDB_SMOKE_CLEANUP_TIMEOUT",
            ),
            command_timeout=cls._positive_float(
                values.get("CMDB_SMOKE_COMMAND_TIMEOUT", "30"),
                "CMDB_SMOKE_COMMAND_TIMEOUT",
            ),
            log_timeout=cls._positive_float(
                values.get("CMDB_SMOKE_LOG_TIMEOUT", "10"),
                "CMDB_SMOKE_LOG_TIMEOUT",
            ),
            log_max_bytes=cls._positive_int(
                values.get("CMDB_SMOKE_LOG_MAX_BYTES", "1048576"),
                "CMDB_SMOKE_LOG_MAX_BYTES",
            ),
        )

    @staticmethod
    def _validate_service_url(name: str, value: str, expected_scheme: str) -> None:
        parsed = urlparse(value)
        if parsed.scheme != expected_scheme or not parsed.hostname:
            raise SmokeConfigurationError(
                f"{name} 必须使用 {expected_scheme} 协议并包含主机"
            )
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

    @staticmethod
    def _positive_int(value: str, name: str) -> int:
        try:
            result = int(value)
        except ValueError as exc:
            raise SmokeConfigurationError(f"{name} 必须是正整数") from exc
        if result <= 0:
            raise SmokeConfigurationError(f"{name} 必须是正整数")
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
        "nats": (4222, "nats"),
        "victoriametrics": (8428, "http"),
        "falkordb": (6379, "redis"),
    }

    def __init__(
        self,
        settings: SmokeSettings,
        *,
        execute: Execute = subprocess.run,
        monotonic: Callable[[], float] = time.monotonic,
        wait: Callable[[float], Any] | None = None,
        resource_remover: Callable[[str, str], None] | None = None,
        log_capture: Callable[[list[str], float, int], str] | None = None,
        canary_probe: Callable[[SmokeContext], bool] | None = None,
    ) -> None:
        self.settings = settings
        self._execute = execute
        self._monotonic = monotonic
        self._wait = wait or threading.Event().wait
        self._resource_remover = resource_remover or self._missing_resource_remover
        self._log_capture = log_capture
        self._canary_probe = canary_probe or self._probe_pipeline_once
        self.ledger = OwnershipLedger(settings.run_id)

    def run(self, workload: Callable[[SmokeContext], Any]) -> Any:
        try:
            self.settings.artifact_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise SmokeConfigurationError(
                f"证据目录已存在，拒绝复用: {self.settings.artifact_dir}"
            ) from exc
        result: Any = None
        failure: BaseException | None = None
        failure_traceback = None
        context = SmokeContext(self.settings, self.ledger)
        try:
            self._compose("up", "-d", check=True)
            self._wait_until_healthy()
            self._wait_for_canary(context)
            result = self._call_with_deadline(
                lambda: workload(context),
                self.settings.workload_timeout,
                "workload 超时",
            )
        except BaseException as exc:
            failure = exc
            failure_traceback = exc.__traceback__

        cleanup_errors: list[str] = []
        try:
            self._capture_logs()
        except Exception as exc:
            cleanup_errors.append(f"保存 Compose 日志失败: {exc}")

        self.ledger.cleanup(
            lambda kind, identifier: self._call_with_deadline(
                lambda: self._resource_remover(kind, identifier),
                self.settings.cleanup_timeout,
                f"清理 {kind}:{identifier} 超时",
            )
        )
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

        try:
            self._wait_until_project_removed()
        except Exception as exc:
            cleanup_errors.append(f"确认 Compose 残留失败: {exc}")

        if cleanup_errors:
            (self.settings.artifact_dir / "cleanup-errors.log").write_text(
                "\n".join(cleanup_errors) + "\n",
                encoding="utf-8",
            )
        cleanup_failure = (
            SmokeCleanupError("; ".join(cleanup_errors)) if cleanup_errors else None
        )
        if failure is not None:
            if cleanup_failure is not None:
                if isinstance(failure, Exception):
                    raise ExceptionGroup(
                        "smoke 业务与清理均失败",
                        [failure, cleanup_failure],
                    )
                raise BaseExceptionGroup(
                    "smoke 业务与清理均失败",
                    [failure, cleanup_failure],
                )
            raise failure.with_traceback(failure_traceback)
        if cleanup_failure is not None:
            raise cleanup_failure
        return result

    def published_endpoint(
        self,
        service: str,
        container_port: int,
        *,
        scheme: str | None = None,
    ) -> str:
        expected = self._PUBLISHED_PORTS.get(service)
        if expected is None or expected[0] != container_port:
            raise SmokeConfigurationError("只允许查询 smoke 栈声明的服务端口")
        expected_scheme = expected[1]
        if scheme is not None and scheme != expected_scheme:
            raise SmokeConfigurationError(f"{service} 不允许使用 {scheme} 协议")

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
        return f"{expected_scheme}://{rendered_host}:{port}"

    def _wait_for_canary(self, context: SmokeContext) -> None:
        deadline = self._monotonic() + self.settings.canary_timeout
        while self._monotonic() < deadline:
            remaining = deadline - self._monotonic()
            if self._call_with_deadline(
                lambda: self._canary_probe(context),
                max(min(remaining, self.settings.command_timeout), 0.001),
                "canary 探测超时",
            ):
                return
            self._wait(self.settings.poll_interval)
        raise SmokeTimeoutError("NATS→Telegraf→VictoriaMetrics canary 超时")

    def _probe_pipeline_once(self, context: SmokeContext) -> bool:
        nats_url = urlparse(self.published_endpoint("nats", 4222))
        vm_url = self.published_endpoint("victoriametrics", 8428)
        metric = "cmdb_collection_smoke_canary"
        line = (
            f"{metric},run_id={context.settings.run_id} value=1i "
            f"{time.time_ns()}\n"
        ).encode()
        with socket.create_connection(
            (nats_url.hostname or "", nats_url.port or 0),
            timeout=self.settings.command_timeout,
        ) as connection:
            connection.settimeout(self.settings.command_timeout)
            info = connection.makefile("rb").readline()
            if not info.startswith(b"INFO "):
                return False
            subject = f"metrics.{context.settings.run_id}".encode()
            connection.sendall(
                b'CONNECT {"verbose":false,"pedantic":false}\r\n'
                + b"PUB "
                + subject
                + b" "
                + str(len(line)).encode()
                + b"\r\n"
                + line
                + b"\r\nPING\r\n"
            )
            if b"PONG" not in connection.recv(64):
                return False

        query = quote(f'{metric}{{run_id="{context.settings.run_id}"}}')
        with urlopen(
            f"{vm_url}/api/v1/query?query={query}",
            timeout=self.settings.command_timeout,
        ) as response:
            payload = json.loads(response.read(1_048_577))
        result = payload.get("data", {}).get("result", [])
        return payload.get("status") == "success" and bool(result)

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
        command = self._compose_command(
            "logs",
            "--no-color",
            "--timestamps",
            "--tail",
            "10000",
        )
        capture = self._log_capture or (
            lambda current, timeout, maximum: bounded_command_output(
                current,
                timeout=timeout,
                max_bytes=maximum,
            )
        )
        output = capture(
            command,
            self.settings.log_timeout,
            self.settings.log_max_bytes,
        )
        log_file = self.settings.artifact_dir / "compose.log"
        encoded = output.encode("utf-8")[: self.settings.log_max_bytes]
        log_file.write_bytes(encoded)

    @staticmethod
    def _missing_resource_remover(kind: str, identifier: str) -> None:
        raise RuntimeError(f"未配置 {kind}:{identifier} 的精确资源清理器")

    def _compose(
        self,
        *arguments: str,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        command = self._compose_command(*arguments)
        return self._execute(
            command,
            check=check,
            capture_output=True,
            text=True,
            timeout=self.settings.command_timeout,
        )

    def _compose_command(self, *arguments: str) -> list[str]:
        return [
            "docker",
            "compose",
            "--file",
            str(self.settings.compose_file),
            "--project-name",
            self.settings.compose_project,
            *arguments,
        ]

    def _wait_until_project_removed(self) -> None:
        deadline = self._monotonic() + self.settings.cleanup_timeout
        network_name = f"{self.settings.compose_project}_default"
        while self._monotonic() < deadline:
            containers = self._compose("ps", "-a", "--format", "json", check=False)
            network = self._execute(
                ["docker", "network", "inspect", network_name],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.settings.command_timeout,
            )
            if (
                containers.returncode == 0
                and not self._has_compose_records(containers.stdout)
                and self._network_is_absent(network)
            ):
                return
            self._wait(self.settings.poll_interval)
        raise SmokeTimeoutError(
            f"当前 project 仍有容器或网络残留: {self.settings.compose_project}"
        )

    @staticmethod
    def _has_compose_records(output: str) -> bool:
        if not output.strip():
            return False
        try:
            records = json.loads(output)
        except json.JSONDecodeError:
            return any(line.strip() for line in output.splitlines())
        if isinstance(records, list):
            return bool(records)
        return bool(records)

    @staticmethod
    def _network_is_absent(result: subprocess.CompletedProcess[str]) -> bool:
        diagnostic = (result.stderr or "").lower()
        return result.returncode != 0 and (
            "no such network" in diagnostic or "not found" in diagnostic
        )

    @staticmethod
    def _call_with_deadline(
        callback: Callable[[], Any],
        timeout: float,
        message: str,
    ) -> Any:
        if threading.current_thread() is not threading.main_thread():
            raise SmokeConfigurationError("硬截止时间仅允许在主线程执行")
        previous_handler = signal.getsignal(signal.SIGALRM)
        previous_timer = signal.getitimer(signal.ITIMER_REAL)

        def raise_timeout(_: int, __: Any) -> None:
            raise _DeadlineExpired(message)

        signal.signal(signal.SIGALRM, raise_timeout)
        signal.setitimer(signal.ITIMER_REAL, timeout)
        try:
            try:
                return callback()
            except _DeadlineExpired as exc:
                raise SmokeTimeoutError(message) from exc
        finally:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)
            signal.signal(signal.SIGALRM, previous_handler)
