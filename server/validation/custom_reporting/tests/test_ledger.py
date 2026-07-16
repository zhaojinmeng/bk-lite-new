import json

import pytest

from validation.custom_reporting.ledger import ResourceRef, ValidationLedger


def test_create_generates_unique_run_ids():
    first = ValidationLedger.create()
    second = ValidationLedger.create()

    assert first.run_id.startswith("crval_")
    assert second.run_id.startswith("crval_")
    assert first.run_id != second.run_id


@pytest.mark.parametrize(
    "kind", ["edge", "instance", "review", "pending", "batch", "credential", "task", "association", "model",],
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
