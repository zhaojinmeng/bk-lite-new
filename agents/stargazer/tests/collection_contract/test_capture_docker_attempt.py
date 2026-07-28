import json
from types import SimpleNamespace

import pytest

from capture_docker_attempt import (
    build_residual_query,
    cleanup_docker_resource,
    ensure_cleanup_complete,
    repair_cleanup_artifact,
)


def test_有container_id时残留查询只按精确ID不与name做AND():
    query = build_residual_query(
        container_id="abc123def456",
        resource_identifier="abc123def456",
    )

    assert query == [
        "docker",
        "ps",
        "-aq",
        "--filter",
        "id=abc123def456",
    ]


def test_无container_id时残留查询只按精确name():
    query = build_residual_query(
        container_id=None,
        resource_identifier="cmdb-task5-network",
    )

    assert query == [
        "docker",
        "ps",
        "-aq",
        "--filter",
        "name=^/cmdb-task5-network$",
    ]


def test_rm失败且ID仍存在时不会把残留误报为零(tmp_path):
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        if argv[1:3] == ["rm", "-f"]:
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="remove failed",
            )
        return SimpleNamespace(
            returncode=0,
            stdout="abc123def456\n",
            stderr="",
        )

    cleanup = cleanup_docker_resource(
        container_id="abc123def456",
        resource_identifier="abc123def456",
        cwd=tmp_path,
        runner=runner,
    )

    assert cleanup["exit_code"] == 1
    assert cleanup["action"] == "remove_container"
    assert cleanup["residual_count"] == 1
    assert cleanup["residual_exit_code"] == 0
    assert cleanup["residual_stdout"] == "abc123def456\n"
    assert cleanup["residual_stderr"] == ""
    assert calls[0] == [
        "docker",
        "ps",
        "-aq",
        "--filter",
        "id=abc123def456",
    ]
    assert calls[2] == [
        "docker",
        "ps",
        "-aq",
        "--filter",
        "id=abc123def456",
    ]


def test_容器已不存在时使用两次真实精确查询证明缺席(tmp_path):
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    cleanup = cleanup_docker_resource(
        container_id="abc123def456",
        resource_identifier="abc123def456",
        cwd=tmp_path,
        runner=runner,
    )

    query = [
        "docker",
        "ps",
        "-aq",
        "--filter",
        "id=abc123def456",
    ]
    assert cleanup == {
        "action": "verify_absent",
        "command": query,
        "exit_code": 0,
        "stdout": "",
        "stderr": "",
        "residual_query": query,
        "residual_exit_code": 0,
        "residual_stdout": "",
        "residual_stderr": "",
        "residual_count": 0,
    }
    assert calls == [query, query]


def test_残留查询daemon错误即使stdout为空也判定证据失败(tmp_path):
    calls = 0

    def runner(argv, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="Cannot connect to the Docker daemon",
        )

    cleanup = cleanup_docker_resource(
        container_id=None,
        resource_identifier="cmdb-task5-network",
        cwd=tmp_path,
        runner=runner,
    )

    assert cleanup["residual_exit_code"] == 1
    assert cleanup["residual_stdout"] == ""
    assert "Docker daemon" in cleanup["residual_stderr"]
    with pytest.raises(RuntimeError, match="Docker 清理不完整"):
        ensure_cleanup_complete("network", cleanup)


def test_首次缺席查询context错误也不能进入verify_absent成功态(tmp_path):
    def runner(argv, **kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="context deadline exceeded",
        )

    cleanup = cleanup_docker_resource(
        container_id=None,
        resource_identifier="cmdb-task5-network",
        cwd=tmp_path,
        runner=runner,
    )

    assert cleanup["action"] == "verify_absent"
    assert cleanup["exit_code"] == 1
    assert cleanup["residual_exit_code"] == 1
    with pytest.raises(RuntimeError, match="Docker 清理不完整"):
        ensure_cleanup_complete("network", cleanup)


def test_rm的No_such_container原始失败不能规范成成功(tmp_path):
    calls = 0

    def runner(argv, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(
                returncode=0,
                stdout="abc123def456\n",
                stderr="",
            )
        if calls == 2:
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="Error response from daemon: No such container",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    cleanup = cleanup_docker_resource(
        container_id="abc123def456",
        resource_identifier="abc123def456",
        cwd=tmp_path,
        runner=runner,
    )

    assert cleanup["action"] == "remove_container"
    assert cleanup["exit_code"] == 1
    assert "No such container" in cleanup["stderr"]
    with pytest.raises(RuntimeError, match="Docker 清理不完整"):
        ensure_cleanup_complete("historical", cleanup)


def test_修复工具只替换cleanup且保存两次真实缺席查询(tmp_path):
    artifact_path = tmp_path / "docker_attempt.json"
    original = {
        "case_id": "network",
        "command": ["docker", "pull", "example/image:tag"],
        "started_at": "2026-07-28T01:00:00Z",
        "finished_at": "2026-07-28T01:01:00Z",
        "exit_code": 124,
        "stdout": "pulling",
        "stderr": "timeout",
        "container_id": None,
        "resource_identifier": "cmdb-task5-network",
        "cleanup": {"legacy": True},
    }
    artifact_path.write_text(json.dumps(original), encoding="utf-8")
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    repaired = repair_cleanup_artifact(
        artifact_path,
        cwd=tmp_path,
        runner=runner,
    )
    persisted = json.loads(artifact_path.read_text(encoding="utf-8"))

    assert {
        key: value for key, value in persisted.items() if key != "cleanup"
    } == {
        key: value for key, value in original.items() if key != "cleanup"
    }
    assert persisted == repaired
    assert repaired["cleanup"]["action"] == "verify_absent"
    assert repaired["cleanup"]["residual_exit_code"] == 0
    assert calls == [
        repaired["cleanup"]["command"],
        repaired["cleanup"]["residual_query"],
    ]
