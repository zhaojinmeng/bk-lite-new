import json
import re
from dataclasses import FrozenInstanceError

import pytest

from validation.custom_reporting.ledger import ResourceRef, ValidationLedger


def _serialized_ledger(run_id, resources):
    payload = {"run_id": run_id, "resources": resources}
    return json.dumps(payload)


def test_create_uses_128_bit_nonce_and_strict_run_id_format(monkeypatch):
    requested_bytes = []

    def fake_token_hex(byte_count):
        requested_bytes.append(byte_count)
        return "a" * (byte_count * 2)

    monkeypatch.setattr("validation.custom_reporting.ledger.secrets.token_hex", fake_token_hex)

    ledger = ValidationLedger.create(now="20260716T071500Z")

    assert requested_bytes == [16]
    assert ledger.run_id == "crval_20260716T071500Z_" + "a" * 32
    assert re.fullmatch(r"crval_\d{8}T\d{6}Z_[A-Za-z0-9]{6,}", ledger.run_id)


@pytest.mark.parametrize(
    "run_id",
    [
        "",
        None,
        101,
        "crval_20260716T071500Z_short",
        "crval_20261316T071500Z_a1b2c3",
        "crval_20260716T071500Z_a1b2c3/unsafe",
        "other_20260716T071500Z_a1b2c3",
    ],
)
def test_constructor_rejects_invalid_run_id(run_id):
    with pytest.raises(ValueError, match="run_id 格式无效"):
        ValidationLedger(run_id=run_id)


def test_create_reuses_strict_run_id_validation():
    with pytest.raises(ValueError, match="run_id 格式无效"):
        ValidationLedger.create(now="invalid-time", nonce="a1b2c3")


def test_run_id_cannot_be_reassigned_after_construction():
    ledger = ValidationLedger.create(now="20260716T071500Z", nonce="a1b2c3")

    with pytest.raises(FrozenInstanceError):
        ledger.run_id = "crval_20260716T071500Z_production"

    ledger.record("instance", 101)
    assert ledger.cleanup_plan() == [ResourceRef("instance", 101)]


@pytest.mark.parametrize(
    "kind",
    [
        "edge",
        "instance",
        "review",
        "pending",
        "batch",
        "credential",
        "task",
        "association",
        "model",
    ],
)
def test_record_accepts_only_fixed_resource_kinds(kind):
    ledger = ValidationLedger.create(now="20260716T071500Z", nonce="a1b2c3")
    identifier = f"{ledger.run_id}_{kind}" if kind in {"task", "association", "model"} else 101

    ledger.record(kind, identifier)

    assert ResourceRef(kind, identifier) in ledger.cleanup_plan()


def test_record_rejects_unknown_resource_kind():
    ledger = ValidationLedger.create(now="20260716T071500Z", nonce="a1b2c3")

    with pytest.raises(ValueError, match="未知资源类型"):
        ledger.record("unknown", 101)


@pytest.mark.parametrize("kind", ["task", "association", "model"])
def test_named_resources_must_belong_to_current_run(kind):
    ledger = ValidationLedger.create(now="20260716T071500Z", nonce="a1b2c3")

    with pytest.raises(ValueError, match="不属于当前 run_id"):
        ledger.record(kind, "existing-production-resource")

    assert ledger.cleanup_plan() == []


@pytest.mark.parametrize("kind", ["task", "association", "model"])
@pytest.mark.parametrize("identifier_template", ["existing_{run_id}", "{run_id}unsafe"])
def test_named_resources_require_exact_run_id_boundary(kind, identifier_template):
    ledger = ValidationLedger.create(now="20260716T071500Z", nonce="a1b2c3")

    with pytest.raises(ValueError, match="不属于当前 run_id"):
        ledger.record(kind, identifier_template.format(run_id=ledger.run_id))


@pytest.mark.parametrize("kind", ["task", "association", "model"])
def test_named_resources_accept_exact_run_id(kind):
    ledger = ValidationLedger.create(now="20260716T071500Z", nonce="a1b2c3")

    ledger.record(kind, ledger.run_id)

    assert ledger.cleanup_plan() == [ResourceRef(kind, ledger.run_id)]


@pytest.mark.parametrize("identifier", [True, False, {}, [], 1.5, None])
def test_record_rejects_non_integer_or_string_identifier(identifier):
    ledger = ValidationLedger.create(now="20260716T071500Z", nonce="a1b2c3")

    with pytest.raises(ValueError, match="identifier 必须是 int 或 str"):
        ledger.record("instance", identifier)


@pytest.mark.parametrize("identifier", [101, True])
def test_named_resources_require_string_identifier(identifier):
    ledger = ValidationLedger.create(now="20260716T071500Z", nonce="a1b2c3")

    with pytest.raises(ValueError, match="名称型资源 identifier 必须是 str"):
        ledger.record("task", identifier)


def test_constructor_cannot_inject_resources():
    run_id = "crval_20260716T071500Z_a1b2c3"

    with pytest.raises(TypeError, match="_resources"):
        ValidationLedger(
            run_id=run_id,
            _resources=[ResourceRef("task", "existing-production-task")],
        )


def test_cleanup_plan_uses_dependency_safe_order_and_only_recorded_resources():
    ledger = ValidationLedger.create(now="20260716T071500Z", nonce="a1b2c3")
    recorded = [
        ResourceRef("model", f"{ledger.run_id}_model"),
        ResourceRef("task", f"{ledger.run_id}_task"),
        ResourceRef("instance", 101),
        ResourceRef("edge", 202),
        ResourceRef("credential", 303),
    ]
    for resource in recorded:
        ledger.record(resource.kind, resource.identifier)

    assert ledger.cleanup_plan() == [
        ResourceRef("edge", 202),
        ResourceRef("instance", 101),
        ResourceRef("credential", 303),
        ResourceRef("task", f"{ledger.run_id}_task"),
        ResourceRef("model", f"{ledger.run_id}_model"),
    ]


def test_duplicate_records_are_idempotent():
    ledger = ValidationLedger.create(now="20260716T071500Z", nonce="a1b2c3")

    ledger.record("instance", 101)
    ledger.record("instance", 101)

    assert ledger.cleanup_plan() == [ResourceRef("instance", 101)]


def test_cleanup_plan_reverses_record_order_within_same_kind():
    ledger = ValidationLedger.create(now="20260716T071500Z", nonce="a1b2c3")
    ledger.record("instance", 101)
    ledger.record("instance", 102)

    assert ledger.cleanup_plan() == [
        ResourceRef("instance", 102),
        ResourceRef("instance", 101),
    ]


def test_json_round_trip_preserves_resources_and_cleanup_plan():
    ledger = ValidationLedger.create(now="20260716T071500Z", nonce="a1b2c3")
    ledger.record("task", f"{ledger.run_id}_task")
    ledger.record("instance", 101)
    serialized = ledger.to_json()

    restored = ValidationLedger.from_json(serialized)

    assert json.loads(serialized)["run_id"] == ledger.run_id
    assert restored.run_id == ledger.run_id
    assert restored.resources == ledger.resources
    assert restored.cleanup_plan() == ledger.cleanup_plan()


@pytest.mark.parametrize("run_id", ["", None, 101, "invalid-run-id"])
def test_json_restore_rejects_invalid_run_id(run_id):
    serialized = _serialized_ledger(
        run_id,
        [{"kind": "task", "identifier": "existing-production-task"}],
    )

    with pytest.raises(ValueError, match="run_id 格式无效"):
        ValidationLedger.from_json(serialized)


def test_json_restore_rejects_unknown_resource_kind():
    serialized = _serialized_ledger(
        "crval_20260716T071500Z_a1b2c3",
        [{"kind": "unknown", "identifier": 101}],
    )

    with pytest.raises(ValueError, match="未知资源类型"):
        ValidationLedger.from_json(serialized)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"run_id": "crval_20260716T071500Z_a1b2c3", "resources": {}},
        {"run_id": "crval_20260716T071500Z_a1b2c3", "resources": [[]]},
        {"run_id": "crval_20260716T071500Z_a1b2c3", "resources": [{"kind": "instance"}]},
    ],
)
def test_json_restore_rejects_malformed_structure(payload):
    with pytest.raises(ValueError, match="账本 JSON 结构无效"):
        ValidationLedger.from_json(json.dumps(payload))


@pytest.mark.parametrize("identifier", [True, {}, []])
def test_json_restore_rejects_invalid_identifier(identifier):
    serialized = _serialized_ledger(
        "crval_20260716T071500Z_a1b2c3",
        [{"kind": "instance", "identifier": identifier}],
    )

    with pytest.raises(ValueError, match="identifier 必须是 int 或 str"):
        ValidationLedger.from_json(serialized)


def test_task_ledger_accepts_legacy_name_and_owned_real_id():
    ledger = ValidationLedger.create(now="20260716T071500Z", nonce="a1b2c3")
    ledger.record("task", f"{ledger.run_id}_legacy_task")
    ledger.record("task", f"{ledger.run_id}:101")
    assert ledger.resources[-1] == ResourceRef("task", f"{ledger.run_id}:101")


@pytest.mark.parametrize(
    "identifier",
    [
        "{run_id}:0",
        "{run_id}:01",
        "{run_id}:-1",
        "{run_id}:1.0",
        "{run_id}:１２",
        "other:101",
    ],
)
def test_task_ledger_rejects_malformed_owned_real_id(identifier):
    ledger = ValidationLedger.create(now="20260716T071500Z", nonce="a1b2c3")
    with pytest.raises(ValueError):
        ledger.record("task", identifier.format(run_id=ledger.run_id))
