import ast
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from core import nats_utils
from semantics import (
    assert_timestamp_propagation,
    parse_line_protocol,
    parse_prometheus,
)
from tasks.utils import nats_helper

PROMETHEUS_TWO_LINES = """# TYPE host_info gauge
host_info{model_id="host",inst_name="node-a"} 1 1700000000123
host_info{model_id="host",inst_name="node-b"} 1 1700000000456
"""


@pytest.mark.asyncio
async def test_publish_boundary_uses_real_subject_conversion_and_all_lines(
    monkeypatch,
):
    calls = []

    async def capture_lines(subject, lines):
        calls.append((subject, list(lines)))
        return len(lines)

    monkeypatch.setenv("NATS_METRIC_TOPIC", "metrics")
    monkeypatch.setattr(nats_helper, "nats_publish_lines", capture_lines)
    params = {
        "monitor_type": "host",
        "plugin_name": "host_info",
        "model_id": "host",
        "tags": {
            "agent_id": "agent-contract",
            "instance_id": "cmdb_1001",
            "instance_type": "host",
            "collect_type": "discovery",
            "config_type": "job",
        },
    }

    count = await nats_helper.publish_metrics_to_nats(
        {}, PROMETHEUS_TWO_LINES, params, task_id="1001"
    )

    assert count == 2
    assert len(calls) == 1
    subject, payload_lines = calls[0]
    assert subject == "metrics.host"
    assert parse_line_protocol(payload_lines) == parse_line_protocol(
        nats_helper.convert_prometheus_to_influx(PROMETHEUS_TWO_LINES, params)
    )
    assert_timestamp_propagation(
        parse_prometheus(PROMETHEUS_TWO_LINES), parse_line_protocol(payload_lines),
    )


@pytest.mark.asyncio
async def test_nats_transport_publishes_each_line_as_its_own_bytes_and_flushes_once(
    monkeypatch,
):
    published = []

    class FakeNatsConnection:
        async def publish(self, subject, payload):
            published.append((subject, payload))

        async def flush(self):
            published.append(("flush", None))

    monkeypatch.setattr(
        nats_utils, "get_shared_nats", AsyncMock(return_value=FakeNatsConnection()),
    )
    lines = [
        "host_info,host=one gauge=1i 1700000000123000000",
        "host_info,host=two gauge=1i 1700000000456000000",
    ]

    count = await nats_utils.nats_publish_lines("metrics.host", lines)

    assert count == 2
    assert published == [
        ("metrics.host", lines[0].encode("utf-8")),
        ("metrics.host", lines[1].encode("utf-8")),
        ("flush", None),
    ]


@pytest.mark.asyncio
async def test_empty_metrics_deliver_zero_messages(monkeypatch):
    publish_lines = AsyncMock()
    monkeypatch.setattr(nats_helper, "nats_publish_lines", publish_lines)

    count = await nats_helper.publish_metrics_to_nats(
        {}, "\n# no samples\n", {"monitor_type": "host"}, task_id="1002"
    )

    assert count == 0
    publish_lines.assert_not_awaited()


@pytest.mark.asyncio
async def test_zero_delivery_failure_retries_then_succeeds(monkeypatch):
    attempts = 0

    async def fail_before_delivery_then_succeed(subject, lines):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise nats_utils.NatsLinesPublishError(
                subject=subject,
                attempted_count_before_failure=0,
                delivery_detected=False,
                error=ConnectionError("disconnected before publish"),
            )
        return len(lines)

    monkeypatch.setenv("NATS_METRICS_PUBLISH_RETRIES", "2")
    monkeypatch.setattr(
        nats_helper, "nats_publish_lines", fail_before_delivery_then_succeed,
    )
    monkeypatch.setattr(nats_helper.asyncio, "sleep", AsyncMock())

    count = await nats_helper.publish_metrics_to_nats(
        {}, PROMETHEUS_TWO_LINES, {"monitor_type": "host"}, task_id="1003"
    )

    assert count == 2
    assert attempts == 2


@pytest.mark.asyncio
async def test_partial_delivery_aborts_without_republishing_confirmed_lines(
    monkeypatch,
):
    publish_lines = AsyncMock(return_value=1)
    monkeypatch.setenv("NATS_METRICS_PUBLISH_RETRIES", "2")
    monkeypatch.setattr(nats_helper, "nats_publish_lines", publish_lines)

    with pytest.raises(nats_helper.MetricsPublishError) as error:
        await nats_helper.publish_metrics_to_nats(
            {}, PROMETHEUS_TWO_LINES, {"monitor_type": "host"}, task_id="1004"
        )

    assert error.value.success_count == 1
    assert error.value.delivery_detected is True
    assert error.value.attempts == 1
    assert publish_lines.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["second_publish", "flush"])
async def test_real_transport_failure_after_delivery_is_not_retried(
    monkeypatch, failure_point,
):
    published = []
    flush_count = 0

    class FailingNatsConnection:
        async def publish(self, subject, payload):
            published.append((subject, payload))
            if failure_point == "second_publish" and len(published) == 2:
                raise ConnectionError("second publish failed")

        async def flush(self):
            nonlocal flush_count
            flush_count += 1
            if failure_point == "flush":
                raise ConnectionError("flush failed")

    monkeypatch.setenv("NATS_METRICS_PUBLISH_RETRIES", "2")
    monkeypatch.setattr(
        nats_utils, "get_shared_nats", AsyncMock(return_value=FailingNatsConnection()),
    )

    with pytest.raises(nats_helper.MetricsPublishError) as error:
        await nats_helper.publish_metrics_to_nats(
            {}, PROMETHEUS_TWO_LINES, {"monitor_type": "host"}, task_id="1005"
        )

    assert error.value.delivery_detected is True
    assert error.value.attempts == 1
    assert len(published) == 2
    assert flush_count == (1 if failure_point == "flush" else 0)


def test_lane_a_contract_does_not_call_lane_b_legacy_vm_fixture_helper():
    forbidden_helper = "step2_" + "push_to_vm"
    contract_dir = Path(__file__).parent
    lane_a_paths = [contract_dir / "conftest.py", *contract_dir.glob("test_lane_a*.py")]

    for lane_a_path in lane_a_paths:
        lane_a_tree = ast.parse(lane_a_path.read_text(encoding="utf-8"))
        forbidden_calls = [
            node
            for node in ast.walk(lane_a_tree)
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == forbidden_helper)
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == forbidden_helper
                )
            )
        ]
        assert not forbidden_calls, f"Lane A cannot call legacy helper: {lane_a_path}"


def test_vm_response_builder_is_explicitly_scoped_to_lane_b_legacy_fixtures():
    repository_root = Path(__file__).resolve().parents[4]
    pipeline_path = repository_root / "server/apps/cmdb/tests/e2e/pipeline.py"
    pipeline_tree = ast.parse(pipeline_path.read_text(encoding="utf-8"))
    helper = next(
        node
        for node in pipeline_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "step2_push_to_vm"
    )

    assert "Lane B legacy" in (ast.get_docstring(helper) or "")
