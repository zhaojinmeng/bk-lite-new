from __future__ import annotations

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


def test_nats_canary_handles_fragmented_info_ack_and_pong() -> None:
    connection = FragmentedNatsSocket()

    smoke.publish_nats_canary(
        connection,
        subject=b"metrics.cmdb-a1b2c3d4",
        payload=b"canary,run_id=cmdb-a1b2c3d4 value=1i 1\n",
    )

    assert b"PUB metrics.cmdb-a1b2c3d4" in connection.sent[0]
    assert connection.sent[0].endswith(b"\r\nPING\r\n")


def test_compose_enables_vm_single_field_measurement_mapping() -> None:
    compose = Path(__file__).with_name("compose.yaml").read_text(encoding="utf-8")

    assert "-influxSkipSingleField" in compose
