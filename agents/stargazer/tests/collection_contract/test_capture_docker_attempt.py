from types import SimpleNamespace

from capture_docker_attempt import (
    build_residual_query,
    cleanup_docker_resource,
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
    assert cleanup["residual_count"] == 1
    assert calls[1] == [
        "docker",
        "ps",
        "-aq",
        "--filter",
        "id=abc123def456",
    ]
