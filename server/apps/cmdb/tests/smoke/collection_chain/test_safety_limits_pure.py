from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import pytest

from . import runner as smoke


def enabled_env(**overrides: str) -> dict[str, str]:
    return {
        "CMDB_COLLECTION_SMOKE": "1",
        "CMDB_SMOKE_RUN_ID": "cmdb-a1b2c3d4",
        **overrides,
    }


@pytest.mark.parametrize(
    ("service", "port", "scheme"),
    [
        ("nats", 4222, "http"),
        ("victoriametrics", 8428, "redis"),
        ("falkordb", 6379, "nats"),
    ],
)
def test_published_endpoint_rejects_protocol_mismatch(
    tmp_path: Path,
    service: str,
    port: int,
    scheme: str,
) -> None:
    settings = smoke.SmokeSettings.from_env(enabled_env(), artifact_root=tmp_path)
    runner = smoke.CollectionChainSmokeRunner(
        settings,
        execute=lambda command, **_: subprocess.CompletedProcess(
            command, 0, "127.0.0.1:49152", ""
        ),
    )

    with pytest.raises(smoke.SmokeConfigurationError, match="协议"):
        runner.published_endpoint(service, port, scheme=scheme)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CMDB_SMOKE_NATS_URL", "http://127.0.0.1:4222"),
        ("CMDB_SMOKE_VM_URL", "redis://127.0.0.1:8428"),
        ("CMDB_SMOKE_FALKOR_URL", "nats://127.0.0.1:6379"),
    ],
)
def test_environment_service_override_rejects_protocol_mismatch(
    tmp_path: Path,
    name: str,
    value: str,
) -> None:
    with pytest.raises(smoke.SmokeConfigurationError, match="协议"):
        smoke.SmokeSettings.from_env(
            enabled_env(**{name: value}),
            artifact_root=tmp_path,
        )


def test_settings_expose_all_resource_deadlines_and_log_bound(tmp_path: Path) -> None:
    settings = smoke.SmokeSettings.from_env(
        enabled_env(
            CMDB_SMOKE_WORKLOAD_TIMEOUT="2",
            CMDB_SMOKE_CANARY_TIMEOUT="3",
            CMDB_SMOKE_CLEANUP_TIMEOUT="4",
            CMDB_SMOKE_COMMAND_TIMEOUT="5",
            CMDB_SMOKE_LOG_TIMEOUT="6",
            CMDB_SMOKE_LOG_MAX_BYTES="4096",
        ),
        artifact_root=tmp_path,
    )

    assert settings.workload_timeout == 2
    assert settings.canary_timeout == 3
    assert settings.cleanup_timeout == 4
    assert settings.command_timeout == 5
    assert settings.log_timeout == 6
    assert settings.log_max_bytes == 4096


def test_compose为Telegraf保留实测可启动且有上限的内存预算() -> None:
    compose = Path(__file__).with_name("compose.yaml").read_text(encoding="utf-8")
    telegraf = compose.split("  telegraf:", 1)[1].split("\nconfigs:", 1)[0]

    assert "mem_limit: 384m" in telegraf
    assert "mem_limit:" in telegraf


def test_compose真实运行只使用预先拉取并验证的固定镜像() -> None:
    compose = Path(__file__).with_name("compose.yaml").read_text(encoding="utf-8")

    assert compose.count("pull_policy: never") == 4


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CMDB_SMOKE_STARTUP_TIMEOUT", "inf"),
        ("CMDB_SMOKE_WORKLOAD_TIMEOUT", "nan"),
        ("CMDB_SMOKE_CANARY_TIMEOUT", "121"),
        ("CMDB_SMOKE_CLEANUP_TIMEOUT", "121"),
        ("CMDB_SMOKE_COMMAND_TIMEOUT", "61"),
        ("CMDB_SMOKE_LOG_TIMEOUT", "31"),
        ("CMDB_SMOKE_POLL_INTERVAL", "6"),
        ("CMDB_SMOKE_LOG_MAX_BYTES", "10485761"),
        ("CMDB_SMOKE_LEDGER_MAX_RESOURCES", "10001"),
    ],
)
def test_settings_reject_non_finite_or_over_limit_resource_values(
    tmp_path: Path,
    name: str,
    value: str,
) -> None:
    with pytest.raises(smoke.SmokeConfigurationError, match="上限|有限"):
        smoke.SmokeSettings.from_env(
            enabled_env(**{name: value}),
            artifact_root=tmp_path,
        )


def test_ledger_has_a_hard_resource_count_limit() -> None:
    ledger = smoke.OwnershipLedger("cmdb-a1b2c3d4", max_resources=2)
    ledger.record("graph_entity", "node-1")
    ledger.record("graph_entity", "node-2")

    with pytest.raises(smoke.SmokeConfigurationError, match="资源数量"):
        ledger.record("graph_entity", "node-3")


def test_settings_reject_non_posix_or_non_main_thread(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(smoke.os, "name", "nt")
    with pytest.raises(smoke.SmokeConfigurationError, match="POSIX"):
        smoke.SmokeSettings.from_env(enabled_env(), artifact_root=tmp_path)
    monkeypatch.setattr(smoke.os, "name", "posix")

    errors: list[BaseException] = []

    def construct() -> None:
        try:
            smoke.SmokeSettings.from_env(enabled_env(), artifact_root=tmp_path)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=construct)
    thread.start()
    thread.join(timeout=1)

    assert isinstance(errors[0], smoke.SmokeConfigurationError)
    assert "主线程" in str(errors[0])


def test_nested_deadline_restores_outer_timer_without_extending_it() -> None:
    smoke.signal.setitimer(smoke.signal.ITIMER_REAL, 1)
    before = smoke.signal.getitimer(smoke.signal.ITIMER_REAL)[0]
    try:
        smoke.CollectionChainSmokeRunner._call_with_deadline(
            lambda: threading.Event().wait(0.03),
            0.5,
            "inner",
        )
        after = smoke.signal.getitimer(smoke.signal.ITIMER_REAL)[0]
    finally:
        smoke.signal.setitimer(smoke.signal.ITIMER_REAL, 0)

    assert after < before - 0.02


def test_deadline_without_outer_timer_raises_inner_timeout() -> None:
    smoke.signal.setitimer(smoke.signal.ITIMER_REAL, 0)

    with pytest.raises(smoke.SmokeTimeoutError, match="inner"):
        smoke.CollectionChainSmokeRunner._call_with_deadline(
            lambda: threading.Event().wait(1),
            0.01,
            "inner",
        )


def test_shorter_inner_deadline_restores_outer_handler_and_remaining_time() -> None:
    class OuterDeadline(Exception):
        pass

    def outer_handler(_: int, __: object) -> None:
        raise OuterDeadline

    previous_handler = smoke.signal.getsignal(smoke.signal.SIGALRM)
    smoke.signal.signal(smoke.signal.SIGALRM, outer_handler)
    smoke.signal.setitimer(smoke.signal.ITIMER_REAL, 0.5)
    try:
        with pytest.raises(smoke.SmokeTimeoutError, match="inner"):
            smoke.CollectionChainSmokeRunner._call_with_deadline(
                lambda: threading.Event().wait(1),
                0.01,
                "inner",
            )
        remaining = smoke.signal.getitimer(smoke.signal.ITIMER_REAL)[0]
        assert smoke.signal.getsignal(smoke.signal.SIGALRM) is outer_handler
        assert 0 < remaining < 0.5
    finally:
        smoke.signal.setitimer(smoke.signal.ITIMER_REAL, 0)
        smoke.signal.signal(smoke.signal.SIGALRM, previous_handler)


def test_shorter_outer_deadline_keeps_outer_handler_and_exception() -> None:
    class OuterDeadline(Exception):
        pass

    calls: list[str] = []

    def outer_handler(_: int, __: object) -> None:
        calls.append("outer")
        raise OuterDeadline("outer")

    previous_handler = smoke.signal.getsignal(smoke.signal.SIGALRM)
    smoke.signal.signal(smoke.signal.SIGALRM, outer_handler)
    smoke.signal.setitimer(smoke.signal.ITIMER_REAL, 0.01)
    try:
        with pytest.raises(OuterDeadline, match="outer"):
            smoke.CollectionChainSmokeRunner._call_with_deadline(
                lambda: threading.Event().wait(1),
                0.5,
                "inner",
            )
        assert calls == ["outer"]
    finally:
        smoke.signal.setitimer(smoke.signal.ITIMER_REAL, 0)
        smoke.signal.signal(smoke.signal.SIGALRM, previous_handler)


def test_normal_callback_only_consumes_elapsed_outer_budget() -> None:
    def outer_handler(_: int, __: object) -> None:
        raise AssertionError("outer deadline should not fire")

    previous_handler = smoke.signal.getsignal(smoke.signal.SIGALRM)
    smoke.signal.signal(smoke.signal.SIGALRM, outer_handler)
    smoke.signal.setitimer(smoke.signal.ITIMER_REAL, 0.5)
    before = smoke.signal.getitimer(smoke.signal.ITIMER_REAL)[0]
    try:
        result = smoke.CollectionChainSmokeRunner._call_with_deadline(
            lambda: threading.Event().wait(0.02) or "done",
            1,
            "inner",
        )
        after = smoke.signal.getitimer(smoke.signal.ITIMER_REAL)[0]
        assert result == "done"
        assert after < before - 0.01
        assert smoke.signal.getsignal(smoke.signal.SIGALRM) is outer_handler
    finally:
        smoke.signal.setitimer(smoke.signal.ITIMER_REAL, 0)
        smoke.signal.signal(smoke.signal.SIGALRM, previous_handler)


def test_bounded_command_output_limits_bytes_and_time() -> None:
    output = smoke.bounded_command_output(
        ["python", "-c", "import sys; sys.stdout.write('x' * 100000)"],
        timeout=1,
        max_bytes=1024,
    )
    assert len(output) <= 1024

    with pytest.raises(subprocess.TimeoutExpired):
        smoke.bounded_command_output(
            [
                "python",
                "-c",
                "import threading; threading.Event().wait(5)",
            ],
            timeout=0.01,
            max_bytes=1024,
        )


def test_runner_executes_canary_before_workload(tmp_path: Path) -> None:
    events: list[str] = []
    docker = HealthyDocker()
    settings = smoke.SmokeSettings.from_env(enabled_env(), artifact_root=tmp_path)
    runner = smoke.CollectionChainSmokeRunner(
        settings,
        execute=docker,
        log_capture=lambda *_: "",
        canary_probe=lambda _: events.append("canary") or True,
    )

    runner.run(lambda _: events.append("workload"))

    assert events == ["canary", "workload"]


def test_failed_canary_never_executes_workload_and_still_cleans(tmp_path: Path) -> None:
    events: list[str] = []
    docker = HealthyDocker()
    settings = smoke.SmokeSettings.from_env(
        enabled_env(CMDB_SMOKE_CANARY_TIMEOUT="0.01"),
        artifact_root=tmp_path,
    )
    runner = smoke.CollectionChainSmokeRunner(
        settings,
        execute=docker,
        log_capture=lambda *_: "",
        canary_probe=lambda _: False,
        wait=lambda _: None,
    )

    with pytest.raises(smoke.SmokeTimeoutError, match="canary"):
        runner.run(lambda _: events.append("workload"))

    assert events == []
    assert docker.down_calls == 1


def test_workload_timeout_still_reaches_exact_cleanup(tmp_path: Path) -> None:
    docker = HealthyDocker()
    settings = smoke.SmokeSettings.from_env(
        enabled_env(CMDB_SMOKE_WORKLOAD_TIMEOUT="0.01"),
        artifact_root=tmp_path,
    )
    runner = smoke.CollectionChainSmokeRunner(
        settings,
        execute=docker,
        log_capture=lambda *_: "",
        canary_probe=lambda _: True,
    )

    with pytest.raises(smoke.SmokeTimeoutError, match="workload"):
        runner.run(lambda _: __import__("threading").Event().wait(5))

    assert docker.down_calls == 1
    assert docker.network_inspections


def test_remover_timeout_is_reported_and_compose_is_still_removed(tmp_path: Path) -> None:
    docker = HealthyDocker()
    settings = smoke.SmokeSettings.from_env(
        enabled_env(CMDB_SMOKE_CLEANUP_TIMEOUT="0.01"),
        artifact_root=tmp_path,
    )
    runner = smoke.CollectionChainSmokeRunner(
        settings,
        execute=docker,
        log_capture=lambda *_: "",
        canary_probe=lambda _: True,
        resource_remover=lambda *_: __import__("threading").Event().wait(5),
    )

    def workload(context: smoke.SmokeContext) -> None:
        context.ledger.record("graph_entity", "node-1")

    with pytest.raises(smoke.SmokeCleanupError, match="node-1"):
        runner.run(workload)

    assert docker.down_calls == 1


def test_cleanup_uses_one_budget_and_retries_down_with_remaining_timeout(
    tmp_path: Path,
) -> None:
    docker = HealthyDocker(fail_down_times=1)
    settings = smoke.SmokeSettings.from_env(
        enabled_env(
            CMDB_SMOKE_CLEANUP_TIMEOUT="0.2",
            CMDB_SMOKE_COMMAND_TIMEOUT="0.1",
        ),
        artifact_root=tmp_path,
    )
    runner = smoke.CollectionChainSmokeRunner(
        settings,
        execute=docker,
        log_capture=lambda *_: "",
        canary_probe=lambda _: True,
        wait=lambda _: None,
    )

    runner.run(lambda _: None)

    assert docker.down_calls == 2
    cleanup_timeouts = [
        timeout
        for command, timeout in docker.command_timeouts
        if "down" in command or command[-4:] == ["ps", "-a", "--format", "json"]
    ]
    assert cleanup_timeouts
    assert all(0 < timeout <= settings.command_timeout for timeout in cleanup_timeouts)


def test_many_removers_cannot_multiply_global_cleanup_budget(tmp_path: Path) -> None:
    docker = HealthyDocker()
    settings = smoke.SmokeSettings.from_env(
        enabled_env(
            CMDB_SMOKE_CLEANUP_TIMEOUT="0.03",
            CMDB_SMOKE_COMMAND_TIMEOUT="0.02",
        ),
        artifact_root=tmp_path,
    )
    runner = smoke.CollectionChainSmokeRunner(
        settings,
        execute=docker,
        log_capture=lambda *_: "",
        canary_probe=lambda _: True,
        resource_remover=lambda *_: threading.Event().wait(1),
        wait=lambda _: None,
    )

    def workload(context: smoke.SmokeContext) -> None:
        for index in range(5):
            context.ledger.record("graph_entity", f"node-{index}")

    started = time.monotonic()
    with pytest.raises(smoke.SmokeCleanupError):
        runner.run(workload)
    elapsed = time.monotonic() - started

    assert elapsed < 0.15


def test_existing_artifact_directory_is_rejected_before_docker(tmp_path: Path) -> None:
    docker = HealthyDocker()
    (tmp_path / "cmdb-a1b2c3d4").mkdir()
    settings = smoke.SmokeSettings.from_env(enabled_env(), artifact_root=tmp_path)

    with pytest.raises(smoke.SmokeConfigurationError, match="已存在"):
        smoke.CollectionChainSmokeRunner(
            settings,
            execute=docker,
            log_capture=lambda *_: "",
            canary_probe=lambda _: True,
        ).run(lambda _: None)

    assert docker.commands == []


def test_up_failure_and_cleanup_failure_are_both_visible(tmp_path: Path) -> None:
    docker = HealthyDocker(fail_up=True, fail_down=True)
    settings = smoke.SmokeSettings.from_env(
        enabled_env(CMDB_SMOKE_CLEANUP_TIMEOUT="0.01"),
        artifact_root=tmp_path,
    )
    runner = smoke.CollectionChainSmokeRunner(
        settings,
        execute=docker,
        log_capture=lambda *_: "",
        canary_probe=lambda _: True,
    )

    with pytest.raises(ExceptionGroup) as captured:
        runner.run(lambda _: None)

    rendered = str(captured.value)
    assert captured.value.exceptions[0].stderr == "up failed"
    assert "清理" in rendered


def test_residual_container_timeout_is_a_cleanup_error(tmp_path: Path) -> None:
    docker = HealthyDocker(leave_container=True)
    settings = smoke.SmokeSettings.from_env(
        enabled_env(CMDB_SMOKE_CLEANUP_TIMEOUT="0.01"),
        artifact_root=tmp_path,
    )
    runner = smoke.CollectionChainSmokeRunner(
        settings,
        execute=docker,
        log_capture=lambda *_: "",
        canary_probe=lambda _: True,
        wait=lambda _: None,
    )

    with pytest.raises(smoke.SmokeCleanupError, match="残留"):
        runner.run(lambda _: None)


def test_network_inspect_error_is_not_mistaken_for_absent_network(
    tmp_path: Path,
) -> None:
    docker = HealthyDocker(network_error="permission denied")
    settings = smoke.SmokeSettings.from_env(
        enabled_env(CMDB_SMOKE_CLEANUP_TIMEOUT="0.01"),
        artifact_root=tmp_path,
    )
    runner = smoke.CollectionChainSmokeRunner(
        settings,
        execute=docker,
        log_capture=lambda *_: "",
        canary_probe=lambda _: True,
        wait=lambda _: None,
    )

    with pytest.raises(smoke.SmokeCleanupError, match="残留"):
        runner.run(lambda _: None)


class HealthyDocker:
    def __init__(
        self,
        *,
        fail_up: bool = False,
        fail_down: bool = False,
        fail_down_times: int = 0,
        leave_container: bool = False,
        network_error: str = "not found",
    ) -> None:
        self.fail_up = fail_up
        self.fail_down = fail_down
        self.fail_down_times = fail_down_times
        self.leave_container = leave_container
        self.network_error = network_error
        self.commands: list[list[str]] = []
        self.down_calls = 0
        self.network_inspections: list[str] = []
        self.command_timeouts: list[tuple[list[str], float]] = []

    def __call__(
        self,
        command: list[str],
        **options: object,
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        self.command_timeouts.append((command, float(options.get("timeout", 0))))
        if command[:3] == ["docker", "network", "inspect"]:
            self.network_inspections.append(command[-1])
            return subprocess.CompletedProcess(command, 1, "", self.network_error)
        if command[-2:] == ["up", "-d"] and self.fail_up:
            raise subprocess.CalledProcessError(1, command, stderr="up failed")
        if "down" in command:
            self.down_calls += 1
            if self.down_calls <= self.fail_down_times:
                return subprocess.CompletedProcess(command, 1, "", "transient down failure")
            return subprocess.CompletedProcess(
                command,
                1 if self.fail_down else 0,
                "",
                "down failed" if self.fail_down else "",
            )
        if command[-4:] == ["ps", "-a", "--format", "json"]:
            output = '[{"Service":"nats","State":"running"}]' if self.leave_container else "[]"
            return subprocess.CompletedProcess(command, 0, output, "")
        if command[-3:] == ["ps", "--format", "json"]:
            return subprocess.CompletedProcess(
                command,
                0,
                (
                    '[{"Service":"nats","Health":"healthy"},'
                    '{"Service":"victoriametrics","Health":"healthy"},'
                    '{"Service":"falkordb","Health":"healthy"},'
                    '{"Service":"telegraf","Health":"healthy"}]'
                ),
                "",
            )
        if "port" in command:
            return subprocess.CompletedProcess(command, 0, "127.0.0.1:49152", "")
        return subprocess.CompletedProcess(command, 0, "", "")
