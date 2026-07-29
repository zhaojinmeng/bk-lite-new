from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import runner as smoke


class FragmentedNatsSocket:
    def __init__(self) -> None:
        self.fragments = iter(
            [
                b"IN",
                b'FO {"server_id":"test"}\r\n',
                b"+O",
                b"K\r\nPO",
                b"NG\r\n",
            ]
        )
        self.sent: list[bytes] = []

    def recv(self, _: int) -> bytes:
        return next(self.fragments)

    def sendall(self, payload: bytes) -> None:
        self.sent.append(payload)

    def settimeout(self, _timeout: float) -> None:
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def test_nats_canary_handles_fragmented_info_ack_and_pong() -> None:
    connection = FragmentedNatsSocket()

    smoke.publish_nats_canary(
        connection,
        subject=b"metrics.cmdb-a1b2c3d4",
        payload=b"canary,run_id=cmdb-a1b2c3d4 value=1i 1\n",
    )

    assert b"PUB metrics.cmdb-a1b2c3d4" in connection.sent[0]
    assert connection.sent[0].endswith(b"\r\nPING\r\n")


def test_compose保留生产Influx字段后缀语义() -> None:
    compose = Path(__file__).with_name("compose.yaml").read_text(encoding="utf-8")

    assert "-influxSkipSingleField" not in compose


def test_telegraf配置只使用当前固定版本支持的HTTP输出字段() -> None:
    config = Path(__file__).with_name("telegraf.conf").read_text(encoding="utf-8")

    assert "content_type" not in config


def test_Telegraf必须通过自身健康检查才允许执行canary() -> None:
    compose = Path(__file__).with_name("compose.yaml").read_text(encoding="utf-8")

    assert smoke.CollectionChainSmokeRunner._REQUIRED_HEALTH["telegraf"] == "healthy"
    assert '["CMD-SHELL", "kill -0 1"]' in compose
    assert '["CMD", "telegraf", "--version"]' not in compose


def test_canary轮询禁用VM空结果缓存(tmp_path, monkeypatch) -> None:
    settings = smoke.SmokeSettings.from_env(
        {
            "CMDB_COLLECTION_SMOKE": "1",
            "CMDB_SMOKE_RUN_ID": "cmdb-a1b2c3d4",
        },
        artifact_root=tmp_path,
    )
    connection = FragmentedNatsSocket()
    monkeypatch.setattr(
        smoke.socket,
        "create_connection",
        lambda *_args, **_kwargs: connection,
    )
    requested = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return json.dumps(
                {"status": "success", "data": {"result": [{"value": [1, "1"]}]}}
            ).encode()

    monkeypatch.setattr(
        smoke,
        "urlopen",
        lambda url, **_kwargs: requested.append(url) or Response(),
    )
    runner = smoke.CollectionChainSmokeRunner(
        settings,
        execute=lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            0,
            "127.0.0.1:49152\n",
            "",
        ),
    )

    assert runner._probe_pipeline_once(
        smoke.SmokeContext(settings, runner.ledger)
    )
    assert "nocache=1" in requested[0]
    assert "cmdb_collection_smoke_canary_value" in requested[0]
