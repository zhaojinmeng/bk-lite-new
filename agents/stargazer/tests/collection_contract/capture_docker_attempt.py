"""捕获可机器复核的 Docker/collector 失败尝试并强制清理。

该工具只用于生成测试证据。命令以 argv 直接执行，不经过 shell。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
EVIDENCE_ROOT = (
    REPOSITORY_ROOT / "server/apps/cmdb/tests/e2e/fixtures"
)
_CONTAINER_ID = re.compile(r"container_id\s*=\s*([0-9a-f]{12,64})")
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(password|secret|token|api[_-]?key)(\s*[:=]\s*)([^\s,;]+)"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _run(
    argv: list[str], *, timeout: int, cwd: Path, env: dict[str, str]
) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout or ""
        stderr = error.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return 124, stdout, stderr + f"\ncommand timed out after {timeout}s"


def _sanitize(value: str) -> str:
    value = value.replace(str(Path.home()), "<HOME>")
    value = value.replace("testpw", "<REDACTED>")
    return _SENSITIVE_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}{match.group(2)}<REDACTED>",
        value,
    )


def _image_digest(image: str) -> str | None:
    result = subprocess.run(
        ["docker", "image", "inspect", image, "--format", "{{index .RepoDigests 0}}"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
    )
    digest = result.stdout.strip()
    return digest if result.returncode == 0 and digest else None


def capture(args: argparse.Namespace) -> Path:
    resource_identifier = f"cmdb-task5-{args.case_id}"
    started_at = _utc_now()
    working_directory = REPOSITORY_ROOT / args.command_cwd
    context = subprocess.run(
        ["docker", "context", "show"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    docker_host = subprocess.run(
        [
            "docker",
            "context",
            "inspect",
            context,
            "--format",
            "{{.Endpoints.docker.Host}}",
        ],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    command_env = os.environ.copy()
    command_env["DOCKER_HOST"] = docker_host
    exit_code, stdout, stderr = _run(
        args.command,
        timeout=args.timeout,
        cwd=working_directory,
        env=command_env,
    )
    finished_at = _utc_now()
    match = _CONTAINER_ID.search(stdout)
    container_id = match.group(1) if match else None
    if container_id:
        resource_identifier = container_id

    cleanup_command = ["docker", "rm", "-f", resource_identifier]
    cleanup = subprocess.run(
        cleanup_command,
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
    )
    residual_query = [
        "docker",
        "ps",
        "-aq",
        "--filter",
        f"id={resource_identifier}",
        "--filter",
        f"name={resource_identifier}",
    ]
    residual = subprocess.run(
        residual_query,
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    residual_ids = [
        item for item in residual.stdout.splitlines() if item.strip()
    ]
    artifact = {
        "case_id": args.case_id,
        "kind": args.kind,
        "started_at": started_at,
        "finished_at": finished_at,
        "command": args.command,
        "working_directory": args.command_cwd,
        "docker_context": context,
        "image": args.image,
        "platform": args.platform,
        "image_digest": _image_digest(args.image),
        "resource_identifier": resource_identifier,
        "container_id": container_id,
        "stdout": _sanitize(stdout),
        "stderr": _sanitize(stderr),
        "exit_code": exit_code,
        "outcome": "failed",
        "failure_stage": args.failure_stage,
        "sanitized": True,
        "cleanup": {
            "command": cleanup_command,
            "exit_code": cleanup.returncode,
            "stdout": _sanitize(cleanup.stdout),
            "stderr": _sanitize(cleanup.stderr),
            "residual_query": residual_query,
            "residual_count": len(residual_ids),
        },
    }
    destination = (
        EVIDENCE_ROOT / args.case_id / "docker_attempt.json"
    )
    destination.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_id")
    parser.add_argument(
        "--kind",
        required=True,
        choices=(
            "image_pull",
            "collector_cli",
            "service_and_collector",
            "network_boundary",
        ),
    )
    parser.add_argument("--image", required=True)
    parser.add_argument("--platform", default="linux/arm64")
    parser.add_argument(
        "--failure-stage",
        required=True,
        choices=(
            "image_pull",
            "container_start",
            "service_install",
            "service_ready",
            "collector",
            "network_protocol",
        ),
    )
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--command-cwd", default=".")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    if not args.command:
        parser.error("必须提供显式命令 argv")
    capture(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
