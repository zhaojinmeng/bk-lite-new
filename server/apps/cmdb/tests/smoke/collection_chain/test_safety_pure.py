from __future__ import annotations

import subprocess
from itertools import chain, repeat
from pathlib import Path

import pytest

from . import runner as smoke_runner
from .runner import (
    CollectionChainSmokeRunner,
    OwnershipLedger,
    SmokeConfigurationError,
    SmokeSettings,
    SmokeTimeoutError,
)


@pytest.fixture(autouse=True)
def offline_runtime_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        CollectionChainSmokeRunner,
        "_probe_pipeline_once",
        lambda *_: True,
    )
    monkeypatch.setattr(
        smoke_runner,
        "bounded_command_output",
        lambda *_args, **_kwargs: "diagnostic output",
    )


def enabled_env(**overrides: str) -> dict[str, str]:
    return {"CMDB_COLLECTION_SMOKE": "1", **overrides}


def test_smoke_requires_explicit_opt_in() -> None:
    with pytest.raises(SmokeConfigurationError, match="CMDB_COLLECTION_SMOKE=1"):
        SmokeSettings.from_env({})


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CMDB_SMOKE_NATS_URL", "nats://10.0.0.8:4222"),
        ("CMDB_SMOKE_VM_URL", "http://vm.example.com"),
        ("CMDB_SMOKE_FALKOR_URL", "redis://production-db:6379"),
    ],
)
def test_smoke_rejects_external_service_addresses(name: str, value: str) -> None:
    with pytest.raises(SmokeConfigurationError, match="本机回环"):
        SmokeSettings.from_env(enabled_env(**{name: value}))


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CMDB_SMOKE_RUN_ID", "../other-run"),
        ("COMPOSE_PROJECT_NAME", "production"),
        ("COMPOSE_PROJECT_NAME", "cmdb_collection_smoke"),
    ],
)
def test_smoke_rejects_unsafe_ownership_names(name: str, value: str) -> None:
    with pytest.raises(SmokeConfigurationError, match="所有权"):
        SmokeSettings.from_env(enabled_env(**{name: value}))


def test_ledger_never_removes_resources_owned_by_another_run() -> None:
    ledger = OwnershipLedger("cmdb-a1b2c3d4")
    ledger.record("graph_entity", "node-1", owner_run_id="cmdb-other1")

    removed: list[tuple[str, str]] = []
    ledger.cleanup(lambda kind, identifier: removed.append((kind, identifier)))

    assert removed == []
    assert ledger.skipped == [("graph_entity", "node-1")]


def test_runner_times_out_and_still_cleans_up(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def execute(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[:3] == ["docker", "network", "inspect"]:
            return subprocess.CompletedProcess(command, 1, "", "not found")
        if command[-3:] == ["ps", "--format", "json"]:
            return subprocess.CompletedProcess(command, 0, "[]", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    settings = SmokeSettings.from_env(
        enabled_env(
            CMDB_SMOKE_RUN_ID="cmdb-a1b2c3d4",
            CMDB_SMOKE_STARTUP_TIMEOUT="1",
        ),
        artifact_root=tmp_path,
    )
    runner = CollectionChainSmokeRunner(
        settings,
        execute=execute,
        monotonic=iter(chain([0.0, 2.0, 3.0, 4.0], repeat(100.0))).__next__,
        wait=lambda _: None,
    )

    with pytest.raises(SmokeTimeoutError, match="健康"):
        runner.run(lambda _: None)

    down = next(command for command in commands if "down" in command)
    assert down[-4:-1] == ["down", "--remove-orphans", "--timeout"]
    assert "-v" not in down
    assert list(tmp_path.glob("cmdb-a1b2c3d4/*.log"))


def test_workload_failure_preserves_evidence_and_cleans_exact_project(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def execute(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[:3] == ["docker", "network", "inspect"]:
            return subprocess.CompletedProcess(command, 1, "", "not found")
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
        if "logs" in command:
            return subprocess.CompletedProcess(command, 0, "diagnostic output", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    settings = SmokeSettings.from_env(
        enabled_env(CMDB_SMOKE_RUN_ID="cmdb-a1b2c3d4"),
        artifact_root=tmp_path,
    )
    runner = CollectionChainSmokeRunner(settings, execute=execute)

    with pytest.raises(RuntimeError, match="boom"):
        runner.run(lambda _: (_ for _ in ()).throw(RuntimeError("boom")))

    down = next(command for command in commands if "down" in command)
    assert settings.compose_project in down
    assert down[-4:-1] == ["down", "--remove-orphans", "--timeout"]
    assert "diagnostic output" in next(tmp_path.glob("cmdb-a1b2c3d4/*.log")).read_text()


def test_compose_commands_never_use_wide_prune_or_down_volumes(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def execute(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[:3] == ["docker", "network", "inspect"]:
            return subprocess.CompletedProcess(command, 1, "", "not found")
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
        return subprocess.CompletedProcess(command, 0, "", "")

    settings = SmokeSettings.from_env(
        enabled_env(CMDB_SMOKE_RUN_ID="cmdb-a1b2c3d4"),
        artifact_root=tmp_path,
    )
    CollectionChainSmokeRunner(settings, execute=execute).run(lambda _: None)

    flattened = [" ".join(command) for command in commands]
    assert not any("prune" in command for command in flattened)
    assert not any("down -v" in command for command in flattened)
    assert all(settings.compose_project in command for command in flattened)


def test_runner_only_removes_ledger_resources_owned_by_current_run(tmp_path: Path) -> None:
    removed: list[tuple[str, str]] = []

    def execute(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["docker", "network", "inspect"]:
            return subprocess.CompletedProcess(command, 1, "", "not found")
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
        return subprocess.CompletedProcess(command, 0, "", "")

    settings = SmokeSettings.from_env(
        enabled_env(CMDB_SMOKE_RUN_ID="cmdb-a1b2c3d4"),
        artifact_root=tmp_path,
    )
    runner = CollectionChainSmokeRunner(
        settings,
        execute=execute,
        resource_remover=lambda kind, identifier: removed.append((kind, identifier)),
    )

    def workload(context: object) -> None:
        context.ledger.record("graph_entity", "owned-node")
        context.ledger.record(
            "graph_entity",
            "foreign-node",
            owner_run_id="cmdb-deadbeef",
        )

    runner.run(workload)

    assert removed == [("graph_entity", "owned-node")]
    assert runner.ledger.skipped == [("graph_entity", "foreign-node")]


def test_log_capture_failure_cannot_prevent_compose_cleanup(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def execute(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[:3] == ["docker", "network", "inspect"]:
            return subprocess.CompletedProcess(command, 1, "", "not found")
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
        if "logs" in command:
            raise subprocess.TimeoutExpired(command, 1)
        return subprocess.CompletedProcess(command, 0, "", "")

    settings = SmokeSettings.from_env(
        enabled_env(CMDB_SMOKE_RUN_ID="cmdb-a1b2c3d4"),
        artifact_root=tmp_path,
    )

    with pytest.raises(ExceptionGroup) as captured:
        CollectionChainSmokeRunner(
            settings,
            execute=execute,
            log_capture=lambda *_: (_ for _ in ()).throw(
                subprocess.TimeoutExpired(["docker", "compose", "logs"], 1)
            ),
        ).run(
            lambda _: (_ for _ in ()).throw(RuntimeError("boom"))
        )

    assert isinstance(captured.value.exceptions[0], RuntimeError)
    assert isinstance(captured.value.exceptions[1], smoke_runner.SmokeCleanupError)
    down = next(command for command in commands if "down" in command)
    assert down[-4:-1] == ["down", "--remove-orphans", "--timeout"]
    assert next(tmp_path.glob("cmdb-a1b2c3d4/cleanup-errors.log")).is_file()


def test_published_endpoint_is_resolved_from_random_loopback_port(tmp_path: Path) -> None:
    def execute(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if "port" in command:
            return subprocess.CompletedProcess(command, 0, "127.0.0.1:49152\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    settings = SmokeSettings.from_env(
        enabled_env(CMDB_SMOKE_RUN_ID="cmdb-a1b2c3d4"),
        artifact_root=tmp_path,
    )

    endpoint = CollectionChainSmokeRunner(
        settings,
        execute=execute,
    ).published_endpoint("nats", 4222, scheme="nats")

    assert endpoint == "nats://127.0.0.1:49152"


@pytest.mark.parametrize("binding", ["0.0.0.0:49152", "10.0.0.8:49152", "bad-output"])
def test_published_endpoint_rejects_non_loopback_binding(
    tmp_path: Path,
    binding: str,
) -> None:
    def execute(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, binding, "")

    settings = SmokeSettings.from_env(
        enabled_env(CMDB_SMOKE_RUN_ID="cmdb-a1b2c3d4"),
        artifact_root=tmp_path,
    )

    with pytest.raises(SmokeConfigurationError, match="回环端口"):
        CollectionChainSmokeRunner(
            settings,
            execute=execute,
        ).published_endpoint("victoriametrics", 8428)
