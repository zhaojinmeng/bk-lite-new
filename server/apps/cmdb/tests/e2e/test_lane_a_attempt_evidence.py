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
    assert attempt["cleanup"]["residual_count"] == 0
    resource_identifier = attempt["resource_identifier"]
    assert any(
        resource_identifier in argument
        for argument in attempt["cleanup"]["command"]
    )
    assert any(
        resource_identifier in argument
        for argument in attempt["cleanup"]["residual_query"]
    )
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
