import json
from datetime import datetime
from pathlib import Path

import jsonschema
import pytest

E2E_ROOT = Path(__file__).parent
ATTEMPT_CASES = {
    "hbase",
    "keepalived",
    "openresty",
    "rocketmq",
    "spark",
    "mssql",
    "network",
    "oracle",
}
ATTEMPT_SCHEMA = json.loads(
    (E2E_ROOT / "schemas/docker_attempt.schema.json").read_text(
        encoding="utf-8"
    )
)


def _load_attempt(case_id):
    path = E2E_ROOT / "fixtures" / case_id / "docker_attempt.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case_id", sorted(ATTEMPT_CASES))
def test_降级对象保留机器可审的真实Docker尝试(case_id):
    attempt = _load_attempt(case_id)

    jsonschema.Draft202012Validator(
        ATTEMPT_SCHEMA,
        format_checker=jsonschema.FormatChecker(),
    ).validate(attempt)
    assert attempt["case_id"] == case_id
    assert attempt["exit_code"] != 0
    assert attempt["cleanup"]["exit_code"] == 0
    assert attempt["cleanup"]["residual_exit_code"] == 0
    assert attempt["cleanup"]["residual_count"] == 0
    assert attempt["cleanup"]["residual_count"] == len(
        [
            item
            for item in attempt["cleanup"]["residual_stdout"].splitlines()
            if item.strip()
        ]
    )
    assert "No such container" not in attempt["cleanup"]["stderr"]
    assert "No such container" not in attempt["cleanup"]["residual_stderr"]
    assert attempt["cleanup"]["stderr"] == ""
    assert attempt["cleanup"]["residual_stderr"] == ""
    resource_identifier = attempt["resource_identifier"]
    assert any(
        resource_identifier in argument
        for argument in attempt["cleanup"]["command"]
    )
    if attempt["container_id"]:
        assert attempt["cleanup"]["residual_query"] == [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"id={attempt['container_id']}",
        ]
    else:
        assert attempt["cleanup"]["residual_query"] == [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"name=^/{resource_identifier}$",
        ]
    if attempt["cleanup"]["action"] == "verify_absent":
        assert (
            attempt["cleanup"]["command"]
            == attempt["cleanup"]["residual_query"]
        )
        assert attempt["cleanup"]["stdout"] == ""
    else:
        assert attempt["cleanup"]["action"] == "remove_container"
        assert attempt["cleanup"]["command"] == [
            "docker",
            "rm",
            "-f",
            resource_identifier,
        ]
    started_at = datetime.fromisoformat(
        attempt["started_at"].replace("Z", "+00:00")
    )
    finished_at = datetime.fromisoformat(
        attempt["finished_at"].replace("Z", "+00:00")
    )
    assert started_at.utcoffset().total_seconds() == 0
    assert finished_at.utcoffset().total_seconds() == 0
    assert finished_at >= started_at
    assert attempt["stdout"] or attempt["stderr"]


def test_attempt_schema拒绝成功退出伪装成降级证据():
    attempt = {
        "case_id": "invalid",
        "kind": "image_pull",
        "started_at": "2026-07-28T01:00:00Z",
        "finished_at": "2026-07-28T01:00:01Z",
        "command": ["docker", "pull", "invalid.example/image:tag"],
        "working_directory": ".",
        "docker_context": "desktop-linux",
        "image": "invalid.example/image:tag",
        "platform": "linux/arm64",
        "image_digest": None,
        "resource_identifier": "cmdb-task5-invalid",
        "container_id": None,
        "stdout": "unexpected success",
        "stderr": "",
        "exit_code": 0,
        "outcome": "failed",
        "failure_stage": "image_pull",
        "sanitized": True,
        "cleanup": {
            "command": ["docker", "rm", "-f", "cmdb-task5-invalid"],
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "residual_query": [
                "docker",
                "ps",
                "-aq",
                "--filter",
                "name=cmdb-task5-invalid",
            ],
            "residual_count": 0,
        },
    }

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(ATTEMPT_SCHEMA).validate(attempt)


def test_attempt_schema拒绝cleanup失败冒充已清理():
    attempt = _load_attempt("hbase")
    attempt["cleanup"]["exit_code"] = 1

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(ATTEMPT_SCHEMA).validate(attempt)


def test_attempt_schema拒绝残留查询失败冒充零残留():
    attempt = _load_attempt("hbase")
    attempt["cleanup"].update(
        residual_exit_code=1,
        residual_stderr="Cannot connect to the Docker daemon",
    )

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(ATTEMPT_SCHEMA).validate(attempt)


@pytest.mark.parametrize(
    "missing_field",
    ["residual_exit_code", "residual_stdout", "residual_stderr"],
)
def test_attempt_schema要求残留查询完整原始结果(missing_field):
    attempt = _load_attempt("hbase")
    attempt["cleanup"].pop(missing_field, None)

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(ATTEMPT_SCHEMA).validate(attempt)


@pytest.mark.parametrize(
    ("action", "command"),
    [
        (
            "verify_absent",
            ["docker", "rm", "-f", "cmdb-task5-hbase"],
        ),
        (
            "remove_container",
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                "name=^/cmdb-task5-hbase$",
            ],
        ),
    ],
)
def test_attempt_schema按cleanup_action约束真实命令形态(action, command):
    attempt = _load_attempt("hbase")
    attempt["cleanup"].update(action=action, command=command)

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(ATTEMPT_SCHEMA).validate(attempt)


def test_Network_attempt必须从声明镜像的真实Docker命令开始():
    attempt = _load_attempt("network")

    assert attempt["command"][:2] in (
        ["docker", "pull"],
        ["docker", "run"],
    )
    assert attempt["image"] in attempt["command"]
    assert attempt["platform"] in attempt["command"]
    assert attempt["failure_stage"] in {
        "image_pull",
        "container_start",
        "network_protocol",
    }
