from __future__ import annotations

import subprocess
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
        leave_container: bool = False,
        network_error: str = "not found",
    ) -> None:
        self.fail_up = fail_up
        self.fail_down = fail_down
        self.leave_container = leave_container
        self.network_error = network_error
        self.commands: list[list[str]] = []
        self.down_calls = 0
        self.network_inspections: list[str] = []

    def __call__(
        self,
        command: list[str],
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if command[:3] == ["docker", "network", "inspect"]:
            self.network_inspections.append(command[-1])
            return subprocess.CompletedProcess(command, 1, "", self.network_error)
        if command[-2:] == ["up", "-d"] and self.fail_up:
            raise subprocess.CalledProcessError(1, command, stderr="up failed")
        if "down" in command:
            self.down_calls += 1
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
                    '{"Service":"telegraf","State":"running"}]'
                ),
                "",
            )
        if "port" in command:
            return subprocess.CompletedProcess(command, 0, "127.0.0.1:49152", "")
        return subprocess.CompletedProcess(command, 0, "", "")
