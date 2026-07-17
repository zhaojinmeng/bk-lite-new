import json

import pytest

from validation.custom_reporting.http_runner import (
    CleanupIncompleteError,
    DjangoFalkorLedgerStateBackend,
    HttpProtocolError,
    HttpResponse,
    HttpRunner,
    RequestsTransport,
    SafetyError,
    _redact,
    build_execution_plan,
    main,
)
from validation.custom_reporting.ledger import ValidationLedger


class FakeTransport:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.requests = []

    def request(self, *, method, url, headers, json_body, connect_timeout, read_timeout, max_response_bytes):
        self.requests.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "json_body": json_body,
                "connect_timeout": connect_timeout,
                "read_timeout": read_timeout,
                "max_response_bytes": max_response_bytes,
            }
        )
        if not self.responses:
            raise AssertionError("unexpected request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def response(status=200, payload=None, headers=None):
    body = json.dumps({} if payload is None else payload).encode()
    return HttpResponse(status=status, headers=headers or {}, body=body)


def public_resolver(hostname):
    assert hostname == "cmdb.example.test"
    return ["93.184.216.34"]


def runner(tmp_path, transport, **kwargs):
    return HttpRunner(
        base_url="https://cmdb.example.test/api/v1/cmdb-enterprise/custom-reporting/",
        allowed_hosts={"cmdb.example.test"},
        transport=transport,
        ledger=ValidationLedger.create(now="20260717T080000Z", nonce="a1b2c3"),
        ledger_path=tmp_path / "ledger.json",
        resolver=public_resolver,
        **kwargs,
    )


@pytest.mark.parametrize(
    ("execute", "cli_execute", "allow_write"),
    [
        (False, True, "1"),
        (True, False, "1"),
        (True, True, "0"),
        (True, True, "true"),
        (True, True, None),
    ],
)
def test_execute_requires_all_three_write_gates(tmp_path, monkeypatch, execute, cli_execute, allow_write):
    if allow_write is None:
        monkeypatch.delenv("CRV_ALLOW_WRITE", raising=False)
    else:
        monkeypatch.setenv("CRV_ALLOW_WRITE", allow_write)
    transport = FakeTransport()

    result = runner(tmp_path, transport, execute=execute, cli_execute=cli_execute).run(mode="quick", token="secret")

    assert result["dry_run"] is True
    assert result["requests_sent"] == 0
    assert transport.requests == []


@pytest.mark.parametrize(
    ("base_url", "allowed_hosts"),
    [
        ("ftp://cmdb.example.test/api", {"cmdb.example.test"}),
        ("https://user:password@cmdb.example.test/api", {"cmdb.example.test"}),
        ("https://cmdb.example.test/api", set()),
        ("https://cmdb.example.test/api", {"*"}),
        ("https://cmdb.example.test.evil/api", {"cmdb.example.test"}),
        ("https://evilcmdb.example.test/api", {"cmdb.example.test"}),
    ],
)
def test_initialization_rejects_unsafe_base_url(base_url, allowed_hosts, tmp_path):
    with pytest.raises(SafetyError):
        HttpRunner(
            base_url=base_url,
            allowed_hosts=allowed_hosts,
            transport=FakeTransport(),
            ledger=ValidationLedger.create(now="20260717T080000Z", nonce="a1b2c3"),
            ledger_path=tmp_path / "ledger.json",
        )


def test_resource_names_are_unique_between_runs():
    first = ValidationLedger.create(now="20260717T080000Z", nonce="a1b2c3")
    second = ValidationLedger.create(now="20260717T080000Z", nonce="d4e5f6")

    first_names = set(build_execution_plan("quick", first.run_id).resource_names)
    second_names = set(build_execution_plan("quick", second.run_id).resource_names)

    assert first_names.isdisjoint(second_names)


def test_requests_transport_disables_redirects_and_bounds_stream(monkeypatch):
    calls = []

    class StreamResponse:
        status_code = 200
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def iter_content(self, chunk_size):
            assert chunk_size == 64 * 1024
            yield b"1234"
            yield b"5678"

    class Session:
        trust_env = True

        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return StreamResponse()

    session = Session()
    monkeypatch.setattr("validation.custom_reporting.http_runner.requests.Session", lambda: session)
    transport = RequestsTransport()

    result = transport.request(
        method="POST",
        url="https://cmdb.example.test/resource",
        headers={"Authorization": "secret"},
        json_body={"name": "owned"},
        connect_timeout=3.0,
        read_timeout=10.0,
        max_response_bytes=5,
    )

    assert session.trust_env is False
    assert result.body == b"12345678"
    assert calls[0][2]["allow_redirects"] is False
    assert calls[0][2]["stream"] is True
    assert calls[0][2]["timeout"] == (3.0, 10.0)


class FakeLedgerStateBackend:
    def __init__(self, snapshots, cleanup_result=None):
        self.snapshots = list(snapshots)
        self.cleanup_result = cleanup_result or {"deleted": {}}
        self.calls = []

    def snapshot(self, *, ledger, org_id, expect_present):
        self.calls.append(("snapshot", ledger.run_id, org_id, expect_present))
        result = self.snapshots.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def cleanup(self, *, ledger, org_id):
        self.calls.append(("cleanup", ledger.run_id, org_id))
        if isinstance(self.cleanup_result, Exception):
            raise self.cleanup_result
        return self.cleanup_result


def _state_snapshot(run_id, **counts):
    defaults = {
        "task": 1,
        "credential": 1,
        "batch": 4,
        "model": 1,
        "model_association": 1,
        "instance": 5,
        "edge": 2,
        "pending": 0,
        "review": 0,
        "field_registration": 1,
        "change_record": 7,
    }
    defaults.update(counts)
    return {
        "run_id": run_id,
        "counts": defaults,
        "evidence": {"model_id": f"{run_id}_model".lower(), "identities": [f"{run_id}_immediate_source"]},
    }


def test_verify_ledger_cli_reads_existing_ledger_and_uses_real_state_backend(monkeypatch, tmp_path, capsys):
    ledger = ValidationLedger.create(now="20260717T080000Z", nonce="verify01")
    path = tmp_path / "ledger.json"
    path.write_text(ledger.to_json())
    backend = FakeLedgerStateBackend([_state_snapshot(ledger.run_id)])
    monkeypatch.setenv("CRV_ORG_ID", "7")

    assert main(["--verify-ledger", "--ledger", str(path)], state_backend=backend) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["verified"] is True
    assert output["counts"]["instance"] == 5
    assert backend.calls == [("snapshot", ledger.run_id, 7, True)]
    assert path.exists()


def test_verify_ledger_cli_builds_default_django_falkor_backend(monkeypatch, tmp_path, capsys):
    ledger = ValidationLedger.create(now="20260717T080000Z", nonce="verify02")
    path = tmp_path / "ledger.json"
    path.write_text(ledger.to_json())
    backend = FakeLedgerStateBackend([_state_snapshot(ledger.run_id)])
    monkeypatch.setenv("CRV_ORG_ID", "7")
    monkeypatch.setattr(
        "validation.custom_reporting.http_runner._build_default_state_backend",
        lambda: backend,
        raising=False,
    )

    assert main(["--verify-ledger", "--ledger", str(path)]) == 0

    assert json.loads(capsys.readouterr().out)["verified"] is True
    assert backend.calls == [("snapshot", ledger.run_id, 7, True)]


def test_cleanup_ledger_keeps_ledger_until_backend_reports_zero_residuals(monkeypatch, tmp_path):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    http_runner = real_runner(tmp_path, FakeTransport(), execute=True, cli_execute=True)
    run_id = http_runner.ledger.run_id
    http_runner.ledger.record("model", f"{run_id}_model".lower())
    http_runner._persist_ledger()
    nonzero = _state_snapshot(
        run_id,
        task=0,
        credential=0,
        batch=0,
        model=1,
        model_association=0,
        instance=0,
        edge=0,
        field_registration=0,
        change_record=0,
    )
    backend = FakeLedgerStateBackend(
        [nonzero, nonzero],
        cleanup_result={"deleted": {"model": 1}},
    )
    http_runner.state_backend = backend
    http_runner.client.transport.responses = [
        web_response({"count": 0, "next": None, "previous": None, "results": []}),
        web_response({"count": 0, "next": None, "previous": None, "results": []}),
    ]

    with pytest.raises(CleanupIncompleteError, match="residual"):
        http_runner.cleanup()

    assert (tmp_path / "ledger.json").exists()
    assert backend.calls[-1] == ("snapshot", run_id, 7, False)


def test_cleanup_ledger_deletes_ledger_only_after_all_residuals_are_zero(monkeypatch, tmp_path):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    http_runner = real_runner(tmp_path, FakeTransport(), execute=True, cli_execute=True)
    run_id = http_runner.ledger.run_id
    http_runner.ledger.record("model", f"{run_id}_model".lower())
    http_runner._persist_ledger()
    zero = _state_snapshot(
        run_id,
        task=0,
        credential=0,
        batch=0,
        model=0,
        model_association=0,
        instance=0,
        edge=0,
        field_registration=0,
        change_record=0,
    )
    backend = FakeLedgerStateBackend([zero, zero], cleanup_result={"deleted": {"model": 1}})
    http_runner.state_backend = backend
    http_runner.client.transport.responses = [
        web_response({"count": 0, "next": None, "previous": None, "results": []}),
        web_response({"count": 0, "next": None, "previous": None, "results": []}),
    ]

    result = http_runner.cleanup()

    assert result["residual"]["model"] == 0
    assert not (tmp_path / "ledger.json").exists()
    assert backend.calls == [
        ("snapshot", run_id, 7, False),
        ("cleanup", run_id, 7),
        ("snapshot", run_id, 7, False),
    ]


def test_cleanup_programmatic_dry_run_rejects_before_backend_or_http_write(tmp_path):
    transport = FakeTransport()
    http_runner = real_runner(tmp_path, transport)
    backend = FakeLedgerStateBackend([])
    http_runner.state_backend = backend

    with pytest.raises(SafetyError, match="执行门"):
        http_runner.cleanup()

    assert backend.calls == []
    assert transport.requests == []


def test_cleanup_retry_skips_http_association_delete_when_graph_proves_it_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    http_runner = real_runner(tmp_path, FakeTransport(), execute=True, cli_execute=True)
    run_id = http_runner.ledger.run_id
    model_id = f"{run_id}_model".lower()
    http_runner.ledger.record("model", model_id)
    http_runner.ledger.record("association", f"{model_id}_crv_rel_review01_{model_id}")
    http_runner._persist_ledger()
    zero = _state_snapshot(
        run_id,
        task=0,
        credential=0,
        batch=0,
        model=0,
        model_association=0,
        instance=0,
        edge=0,
        field_registration=0,
        change_record=0,
    )
    backend = FakeLedgerStateBackend([zero, zero])
    http_runner.state_backend = backend
    http_runner.client.transport.responses = [
        web_response({"count": 0, "next": None, "previous": None, "results": []}),
        web_response({"count": 0, "next": None, "previous": None, "results": []}),
    ]

    http_runner.cleanup()

    assert [request["method"] for request in http_runner.client.transport.requests] == ["GET", "GET"]


def test_cleanup_predelete_backend_rejection_sends_no_delete(monkeypatch, tmp_path):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    http_runner = real_runner(tmp_path, FakeTransport(), execute=True, cli_execute=True)
    run_id = http_runner.ledger.run_id
    http_runner.ledger.record("task", f"{run_id}:41")
    http_runner._persist_ledger()
    http_runner.state_backend = FakeLedgerStateBackend([SafetyError("foreign task")])
    http_runner.client.transport.responses = [
        web_response(
            {
                "count": 1,
                "next": None,
                "previous": None,
                "results": [{"id": 41, "name": f"{run_id}_quick_task"}],
            }
        )
    ]

    with pytest.raises(CleanupIncompleteError):
        http_runner.cleanup()

    assert [request["method"] for request in http_runner.client.transport.requests] == ["GET"]


def _present_state(*, mode="quick", collect_task="cr_41"):
    ledger = ValidationLedger.create(now="20260717T080000Z", nonce="verify01")
    run_id = ledger.run_id
    model_id = f"{run_id}_model".lower()
    ledger.record("model", model_id)
    ledger.record("task", f"{run_id}:41")
    ledger.record("credential", f"{run_id}:51")
    for batch_id in (61, 62, 63, 64):
        ledger.record("batch", f"{run_id}:{batch_id}")
    ledger.record("association", f"{model_id}_crv_rel_verify01_{model_id}")
    model_asst_id = f"{model_id}_crv_rel_verify01_{model_id}"
    snapshot = {
        "run_id": run_id,
        "counts": {
            "task": 1,
            "credential": 1,
            "batch": 4,
            "model": 1,
            "model_association": 1,
            "instance": 5,
            "edge": 2,
            "pending": 0,
            "review": 0,
            "field_registration": 1 if mode == "quick" else 0,
            "change_record": 7,
        },
        "evidence": {
            "model_id": model_id,
            "tasks": [{"id": 41, "name": f"{run_id}_{mode}_task", "team": [7], "model_id": model_id}],
            "credentials": [{"id": 51, "task_id": 41, "is_enabled": False, "token_revoked": True}],
            "batch_ids": [61, 62, 63, 64],
            "batches": [{"id": batch_id, "task_id": 41} for batch_id in (61, 62, 63, 64)],
            "model": {
                "_id": 71,
                "model_id": model_id,
                "classification_id": "other",
                "group": [7],
                "is_custom_reporting": True,
            },
            "classification": {"_id": 70, "classification_id": "other"},
            "model_associations": [
                {
                    "_id": 81,
                    "model_asst_id": f"{model_id}_crv_rel_verify01_{model_id}",
                    "src_model_id": model_id,
                    "dst_model_id": model_id,
                }
            ],
            "instances": [
                {
                    "_id": 90 + index,
                    "model_id": model_id,
                    "inst_name": f"{run_id}_{suffix}",
                    "crv_run_id": run_id,
                    "organization": [7],
                    "collect_task": collect_task,
                }
                for index, suffix in enumerate(("immediate_source", "immediate_target", "pending_source", "backfill_target", "after_rotate"))
            ],
            "edges": [
                {
                    "_id": 101,
                    "model_asst_id": model_asst_id,
                    "src_inst_id": 90,
                    "dst_inst_id": 91,
                },
                {
                    "_id": 102,
                    "model_asst_id": model_asst_id,
                    "src_inst_id": 92,
                    "dst_inst_id": 93,
                },
            ],
            "incident_instance_edges": [
                {"_id": 101, "_label": "instance_association", "src_id": 90, "dst_id": 91},
                {"_id": 102, "_label": "instance_association", "src_id": 92, "dst_id": 93},
            ],
            "incident_model_edges": [
                {
                    "_id": 81,
                    "_label": "model_association",
                    "src_id": 71,
                    "dst_id": 71,
                },
                {
                    "_id": 82,
                    "_label": "subordinate_model",
                    "src_id": 70,
                    "dst_id": 71,
                    "classification_model_asst_id": f"other_subordinate_model_{model_id}",
                },
            ],
            "subordinate_edges": [{"_id": 82, "src_id": 70, "dst_id": 71}],
            "field_attr_ids": ["crv_run_id"] if mode == "quick" else [],
        },
    }
    return ledger, snapshot


def test_real_state_backend_rejects_instance_collect_task_not_owned_by_live_task():
    ledger, snapshot = _present_state(collect_task="cr_999")

    with pytest.raises(SafetyError, match="collect_task"):
        DjangoFalkorLedgerStateBackend.validate_present(ledger=ledger, org_id=7, snapshot=snapshot)


def test_real_state_backend_accepts_standard_without_quick_field_registration():
    ledger, snapshot = _present_state(mode="standard")

    DjangoFalkorLedgerStateBackend.validate_present(ledger=ledger, org_id=7, snapshot=snapshot)


def test_real_state_backend_rejects_association_with_foreign_endpoint_model():
    ledger, snapshot = _present_state()
    snapshot["evidence"]["model_associations"][0]["dst_model_id"] = "foreign_model"

    with pytest.raises(SafetyError, match="association"):
        DjangoFalkorLedgerStateBackend.validate_present(ledger=ledger, org_id=7, snapshot=snapshot)


def test_real_state_backend_rejects_two_different_actual_association_ids():
    ledger, snapshot = _present_state()
    model_id = snapshot["evidence"]["model_id"]
    ledger.record("association", f"{model_id}_crv_rel_other_{model_id}")

    with pytest.raises(SafetyError, match="association"):
        DjangoFalkorLedgerStateBackend.validate_present(ledger=ledger, org_id=7, snapshot=snapshot)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda evidence: evidence["edges"][0].update(model_asst_id="foreign_association"),
        lambda evidence: evidence["edges"][0].update(dst_inst_id=93),
    ],
)
def test_real_state_backend_verify_rejects_wrong_relation_association_or_pairing(mutation):
    ledger, snapshot = _present_state()
    mutation(snapshot["evidence"])

    with pytest.raises(SafetyError, match="edge|关系"):
        DjangoFalkorLedgerStateBackend.validate_present(ledger=ledger, org_id=7, snapshot=snapshot)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda evidence: evidence["edges"][0].update(model_asst_id="foreign_association"),
        lambda evidence: evidence["edges"][0].update(dst_inst_id=93),
    ],
)
def test_cleanup_preflight_rejects_wrong_relation_association_or_pairing(mutation):
    ledger, snapshot = _present_state()
    mutation(snapshot["evidence"])

    with pytest.raises(SafetyError, match="edge|关系"):
        DjangoFalkorLedgerStateBackend.validate_cleanup_preflight(ledger=ledger, org_id=7, snapshot=snapshot)


def test_cleanup_ownership_rejects_instance_from_non_ledger_collect_task():
    ledger, snapshot = _present_state(collect_task="cr_999")

    with pytest.raises(SafetyError, match="collect_task"):
        DjangoFalkorLedgerStateBackend.validate_cleanup_ownership(ledger=ledger, org_id=7, snapshot=snapshot)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda snapshot: snapshot["evidence"]["tasks"][0].update(team=[8]), "task"),
        (lambda snapshot: snapshot["evidence"]["tasks"][0].update(model_id="foreign_model"), "task"),
        (lambda snapshot: snapshot["evidence"]["credentials"][0].update(id=999), "credential"),
        (lambda snapshot: snapshot["evidence"]["batches"][0].update(id=999), "batch"),
    ],
)
def test_cleanup_preflight_rejects_foreign_orm_ownership(mutation, message):
    ledger, snapshot = _present_state()
    mutation(snapshot)

    with pytest.raises(SafetyError, match=message):
        DjangoFalkorLedgerStateBackend.validate_cleanup_preflight(ledger=ledger, org_id=7, snapshot=snapshot)


def _without_ledger_batches(ledger):
    partial = ValidationLedger(run_id=ledger.run_id)
    for resource in ledger.resources:
        if resource.kind != "batch":
            partial.record(resource.kind, resource.identifier)
    return partial


def test_cleanup_preflight_accepts_single_owned_batch_created_before_failed_ingest_response():
    ledger, snapshot = _present_state()
    ledger = _without_ledger_batches(ledger)
    snapshot["counts"]["batch"] = 1
    snapshot["evidence"]["batch_ids"] = [61]
    snapshot["evidence"]["batches"] = [{"id": 61, "task_id": 41}]

    DjangoFalkorLedgerStateBackend.validate_cleanup_preflight(ledger=ledger, org_id=7, snapshot=snapshot)


def test_cleanup_preflight_rejects_unledgered_batch_owned_by_foreign_task():
    ledger, snapshot = _present_state()
    ledger = _without_ledger_batches(ledger)
    snapshot["counts"]["batch"] = 1
    snapshot["evidence"]["batch_ids"] = [61]
    snapshot["evidence"]["batches"] = [{"id": 61, "task_id": 999}]

    with pytest.raises(SafetyError, match="batch"):
        DjangoFalkorLedgerStateBackend.validate_cleanup_preflight(ledger=ledger, org_id=7, snapshot=snapshot)


def test_cleanup_preflight_rejects_when_ledger_batch_is_missing_from_actual_batches():
    ledger, snapshot = _present_state()
    snapshot["counts"]["batch"] = 3
    snapshot["evidence"]["batch_ids"] = [61, 62, 63]
    snapshot["evidence"]["batches"] = [{"id": batch_id, "task_id": 41} for batch_id in (61, 62, 63)]

    with pytest.raises(SafetyError, match="batch"):
        DjangoFalkorLedgerStateBackend.validate_cleanup_preflight(ledger=ledger, org_id=7, snapshot=snapshot)


def test_cleanup_preflight_rejects_more_than_four_owned_batches():
    ledger, snapshot = _present_state()
    ledger = _without_ledger_batches(ledger)
    batch_ids = [61, 62, 63, 64, 65]
    snapshot["counts"]["batch"] = len(batch_ids)
    snapshot["evidence"]["batch_ids"] = batch_ids
    snapshot["evidence"]["batches"] = [{"id": batch_id, "task_id": 41} for batch_id in batch_ids]

    with pytest.raises(SafetyError, match="batch"):
        DjangoFalkorLedgerStateBackend.validate_cleanup_preflight(ledger=ledger, org_id=7, snapshot=snapshot)


@pytest.mark.parametrize("batch_ids", [[61, 61], [-1], [True], ["61"]])
def test_cleanup_preflight_rejects_duplicate_or_invalid_actual_batch_ids(batch_ids):
    ledger, snapshot = _present_state()
    ledger = _without_ledger_batches(ledger)
    snapshot["counts"]["batch"] = len(batch_ids)
    snapshot["evidence"]["batch_ids"] = batch_ids
    snapshot["evidence"]["batches"] = [{"id": batch_id, "task_id": 41} for batch_id in batch_ids]

    with pytest.raises(SafetyError, match="batch"):
        DjangoFalkorLedgerStateBackend.validate_cleanup_preflight(ledger=ledger, org_id=7, snapshot=snapshot)


def test_verify_present_still_requires_all_four_batches_in_ledger():
    ledger, snapshot = _present_state()

    with pytest.raises(SafetyError, match="batch"):
        DjangoFalkorLedgerStateBackend.validate_present(ledger=_without_ledger_batches(ledger), org_id=7, snapshot=snapshot)


def test_cleanup_preflight_allows_retry_only_when_task_children_are_zero():
    ledger, snapshot = _present_state()
    snapshot["counts"].update(task=0, task_scope=0, credential=0, batch=0, pending=0, review=0)
    snapshot["evidence"].update(tasks=[], credentials=[], batch_ids=[], batches=[])

    DjangoFalkorLedgerStateBackend.validate_cleanup_preflight(ledger=ledger, org_id=7, snapshot=snapshot)

    snapshot["counts"]["credential"] = 1
    with pytest.raises(SafetyError, match="子资源"):
        DjangoFalkorLedgerStateBackend.validate_cleanup_preflight(ledger=ledger, org_id=7, snapshot=snapshot)


@pytest.mark.parametrize("kind", ["instance", "model"])
def test_cleanup_preflight_rejects_foreign_incident_edges(kind):
    ledger, snapshot = _present_state()
    if kind == "instance":
        snapshot["evidence"]["incident_instance_edges"].append({"_id": 999, "_label": "foreign_edge", "src_id": 90, "dst_id": 777})
    else:
        snapshot["evidence"]["incident_model_edges"].append({"_id": 999, "_label": "foreign_edge", "src_id": 71, "dst_id": 777})

    with pytest.raises(SafetyError, match="incident"):
        DjangoFalkorLedgerStateBackend.validate_cleanup_preflight(ledger=ledger, org_id=7, snapshot=snapshot)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda evidence: evidence["incident_instance_edges"][0].update(dst_id=777),
        lambda evidence: evidence["incident_model_edges"][0].update(dst_id=777),
        lambda evidence: evidence["incident_model_edges"][1].update(src_id=777),
        lambda evidence: evidence["incident_model_edges"][1].update(classification_model_asst_id="forged"),
        lambda evidence: evidence["classification"].update(classification_id="foreign"),
    ],
)
def test_cleanup_preflight_rejects_forged_actual_edge_endpoints_and_subordinate_contract(mutation):
    ledger, snapshot = _present_state()
    mutation(snapshot["evidence"])

    with pytest.raises(SafetyError, match="incident|subordinate|classification|edge|端点"):
        DjangoFalkorLedgerStateBackend.validate_cleanup_preflight(ledger=ledger, org_id=7, snapshot=snapshot)


def test_cleanup_preflight_accepts_partial_retry_after_subordinate_deleted():
    ledger, snapshot = _present_state()
    snapshot["counts"].update(task=0, task_scope=0, credential=0, batch=0, pending=0, review=0, model_association=0, instance=0, edge=0)
    snapshot["evidence"].update(
        tasks=[],
        credentials=[],
        batch_ids=[],
        batches=[],
        model_associations=[],
        instances=[],
        edges=[],
        incident_instance_edges=[],
        incident_model_edges=[],
    )

    DjangoFalkorLedgerStateBackend.validate_cleanup_preflight(ledger=ledger, org_id=7, snapshot=snapshot)


def test_cleanup_preflight_accepts_partial_retry_with_one_exact_relation_left():
    ledger, snapshot = _present_state()
    snapshot["counts"]["edge"] = 1
    snapshot["evidence"]["edges"] = snapshot["evidence"]["edges"][:1]
    snapshot["evidence"]["incident_instance_edges"] = snapshot["evidence"]["incident_instance_edges"][:1]

    DjangoFalkorLedgerStateBackend.validate_cleanup_preflight(ledger=ledger, org_id=7, snapshot=snapshot)


def test_cleanup_entity_delete_is_parameterized_non_detach_and_concurrency_fail_closed():
    class RecordingGraph:
        def __init__(self):
            self.calls = []

        def _execute_query(self, query, params):
            self.calls.append((query, params))
            raise RuntimeError("node still has relationships")

        def batch_delete_entity(self, *_args, **_kwargs):
            raise AssertionError("禁止使用 DETACH DELETE helper")

    graph = RecordingGraph()

    with pytest.raises(RuntimeError, match="relationships"):
        DjangoFalkorLedgerStateBackend._delete_entities_without_detach(graph, "instance", {91, 90})

    assert graph.calls == [("MATCH (n:instance) WHERE ID(n) IN $node_ids DELETE n", {"node_ids": [90, 91]})]


def test_cleanup_entity_delete_accepts_zero_based_falkordb_node_id():
    class RecordingGraph:
        def __init__(self):
            self.calls = []

        def _execute_query(self, query, params):
            self.calls.append((query, params))

    graph = RecordingGraph()

    DjangoFalkorLedgerStateBackend._delete_entities_without_detach(graph, "model", {1, 0})

    assert graph.calls == [("MATCH (n:model) WHERE ID(n) IN $node_ids DELETE n", {"node_ids": [0, 1]})]


def test_incident_edges_uses_explicit_query_direction_when_decoder_omits_edge_nodes():
    class Node:
        def __init__(self, node_id):
            self.id = node_id

    class Edge:
        id = 101
        relation = "instance_association"
        properties = {"src_inst_id": 90, "dst_inst_id": 91}
        src_node = None
        dest_node = None

    class Path:
        _edges = [Edge()]
        _nodes = [Node(91), Node(90)]

    class Graph:
        def _execute_query(self, query, params):
            assert query == ("MATCH p=(a)-[n]-(b) WHERE ID(a) IN $node_ids " "RETURN p, ID(startNode(n)), ID(endNode(n))")
            assert params == {"node_ids": [90, 91]}
            return [[Path(), 90, 91], [Path(), 90, 91]]

    assert DjangoFalkorLedgerStateBackend._incident_edges(Graph(), {90, 91}) == [
        {
            "_id": 101,
            "_label": "instance_association",
            "src_id": 90,
            "dst_id": 91,
            "model_asst_id": None,
            "src_model_id": None,
            "dst_model_id": None,
            "src_inst_id": 90,
            "dst_inst_id": 91,
            "classification_model_asst_id": None,
        }
    ]


def test_incident_edges_accepts_zero_based_falkordb_edge_and_node_ids():
    class Node:
        def __init__(self, node_id):
            self.id = node_id

    class Edge:
        id = 0
        relation = "subordinate_model"
        properties = {"classification_model_asst_id": "other_subordinate_model_crv_model"}
        src_node = None
        dest_node = None

    class Path:
        _edges = [Edge()]
        _nodes = [Node(1), Node(0)]

    class Graph:
        def _execute_query(self, query, params):
            assert query == ("MATCH p=(a)-[n]-(b) WHERE ID(a) IN $node_ids " "RETURN p, ID(startNode(n)), ID(endNode(n))")
            assert params == {"node_ids": [0, 1]}
            return [[Path(), 0, 1]]

    assert DjangoFalkorLedgerStateBackend._incident_edges(Graph(), {0, 1}) == [
        {
            "_id": 0,
            "_label": "subordinate_model",
            "src_id": 0,
            "dst_id": 1,
            "model_asst_id": None,
            "src_model_id": None,
            "dst_model_id": None,
            "src_inst_id": None,
            "dst_inst_id": None,
            "classification_model_asst_id": "other_subordinate_model_crv_model",
        }
    ]


@pytest.mark.parametrize(
    "record",
    [
        pytest.param([], id="empty-record"),
        pytest.param([object(), 0], id="two-columns"),
        pytest.param([object(), 0, 1, 2], id="four-columns"),
        pytest.param([object(), 0, 1], id="not-a-path"),
    ],
)
def test_incident_edges_rejects_malformed_three_column_query_records(record):
    class Graph:
        def _execute_query(self, *_args, **_kwargs):
            return [record]

    with pytest.raises(SafetyError, match="incident edge 查询结构非法"):
        DjangoFalkorLedgerStateBackend._incident_edges(Graph(), {0})


@pytest.mark.parametrize("edge_count,node_count", [(0, 2), (2, 2), (1, 0), (1, 1), (1, 3)])
def test_incident_edges_requires_one_edge_and_two_path_nodes(edge_count, node_count):
    class Edge:
        id = 0
        relation = "subordinate_model"
        properties = {}

    class Path:
        _edges = [Edge() for _ in range(edge_count)]
        _nodes = [object() for _ in range(node_count)]

    class Graph:
        def _execute_query(self, *_args, **_kwargs):
            return [[Path(), 0, 1]]

    with pytest.raises(SafetyError, match="incident edge 查询结构非法"):
        DjangoFalkorLedgerStateBackend._incident_edges(Graph(), {0})


@pytest.mark.parametrize("src_id,dst_id", [(-1, 1), (True, 1), ("0", 1), (0, -1), (0, False), (0, "1")])
def test_incident_edges_rejects_invalid_explicit_query_endpoint_ids(src_id, dst_id):
    class Node:
        def __init__(self, node_id):
            self.id = node_id

    class Edge:
        id = 0
        relation = "subordinate_model"
        properties = {}

    class Path:
        _edges = [Edge()]
        _nodes = [Node(1), Node(0)]

    class Graph:
        def _execute_query(self, *_args, **_kwargs):
            return [[Path(), src_id, dst_id]]

    with pytest.raises(SafetyError, match="incident edge id 非法"):
        DjangoFalkorLedgerStateBackend._incident_edges(Graph(), {0})


@pytest.mark.parametrize("invalid_id", [-1, True, "0"])
def test_graph_internal_ids_still_reject_negative_bool_and_non_integer(invalid_id):
    class Graph:
        def _execute_query(self, *_args, **_kwargs):
            raise AssertionError("非法 graph id 不得到达查询")

    with pytest.raises(SafetyError, match="incident edge node id 非法"):
        DjangoFalkorLedgerStateBackend._incident_edges(Graph(), {invalid_id})
    with pytest.raises(SafetyError, match="cleanup entity node id 非法"):
        DjangoFalkorLedgerStateBackend._delete_entities_without_detach(Graph(), "instance", {invalid_id})


def test_cleanup_preflight_accepts_task_absent_graph_present_without_association_ledger():
    original, snapshot = _present_state()
    ledger = ValidationLedger(run_id=original.run_id)
    model_id = snapshot["evidence"]["model_id"]
    ledger.record("model", model_id)
    ledger.record("task", f"{ledger.run_id}:41")
    snapshot["counts"].update(
        task=0,
        task_scope=0,
        credential=0,
        batch=0,
        model_association=0,
        instance=0,
        edge=0,
        pending=0,
        review=0,
    )
    snapshot["evidence"].update(
        tasks=[],
        credentials=[],
        batch_ids=[],
        batches=[],
        model_associations=[],
        instances=[],
        edges=[],
        incident_instance_edges=[],
        incident_model_edges=[
            {
                "_id": 82,
                "_label": "subordinate_model",
                "src_id": 70,
                "dst_id": 71,
                "classification_model_asst_id": f"other_subordinate_model_{model_id}",
            }
        ],
    )

    DjangoFalkorLedgerStateBackend.validate_cleanup_preflight(ledger=ledger, org_id=7, snapshot=snapshot)

    snapshot["evidence"]["model_associations"] = [
        {
            "_id": 999,
            "model_asst_id": "foreign",
            "src_model_id": model_id,
            "dst_model_id": model_id,
        }
    ]
    with pytest.raises(SafetyError, match="association"):
        DjangoFalkorLedgerStateBackend.validate_cleanup_preflight(ledger=ledger, org_id=7, snapshot=snapshot)


def test_cli_defaults_to_dry_run_without_network(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CRV_ALLOWED_HOSTS", "cmdb.example.test")

    exit_code = main(
        [
            "--base-url",
            "https://cmdb.example.test/api/",
            "--ledger",
            str(tmp_path / "ledger.json"),
        ],
        transport=FakeTransport(),
        resolver=public_resolver,
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert '"dry_run": true' in output


# Security review regressions: these tests intentionally assert the real overlay
# contract instead of the provisional endpoints used by the first Task 8 draft.


def web_response(data=None, *, result=True, message=""):
    return response(payload={"result": result, "data": {} if data is None else data, "message": message})


def ingest_response(batch_id, *, instances_received, relations_received, pending_relations):
    return web_response(
        {
            "batch_id": batch_id,
            "summary": {
                "instances_received": instances_received,
                "relations_received": relations_received,
                "created": instances_received,
                "updated": 0,
                "deleted": 0,
                "errors": 0,
                "pending_relations": pending_relations,
            },
        }
    )


def association_response(model_id, asst_id, edge_id=251):
    return web_response(
        {
            "_id": edge_id,
            "src_model_id": model_id,
            "dst_model_id": model_id,
            "asst_id": asst_id,
            "model_asst_id": f"{model_id}_{asst_id}_{model_id}",
        }
    )


def real_runner(tmp_path, transport, **kwargs):
    management_api_secret = kwargs.pop("management_api_secret", "management-secret")
    return HttpRunner(
        base_url="http://127.0.0.1:8011/api/v1/cmdb/api/custom_reporting/",
        allowed_hosts={"127.0.0.1"},
        transport=transport,
        ledger=ValidationLedger.create(now="20260717T080000Z", nonce="review01"),
        ledger_path=tmp_path / "ledger.json",
        session_cookie="sessionid=session-secret",
        management_api_secret=management_api_secret,
        org_id=7,
        **kwargs,
    )


def test_review_contract_dry_run_loopback_literal_does_not_resolve_or_send(tmp_path):
    transport = FakeTransport()
    result = real_runner(tmp_path, transport).run(mode="quick")
    assert result["dry_run"] is True
    assert result["requests_sent"] == 0
    assert transport.requests == []


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:8011/api/v1/cmdb/api/custom_reporting/",
        "http://127.0.0.1.evil:8011/api/v1/cmdb/api/custom_reporting/",
        "http://93.184.216.34/api/v1/cmdb/api/custom_reporting/",
        "http://10.0.0.1/api/v1/cmdb/api/custom_reporting/",
    ],
)
def test_review_contract_execute_rejects_non_loopback_ip_literal(base_url, tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    with pytest.raises(SafetyError):
        HttpRunner(
            base_url=base_url,
            allowed_hosts={base_url.split("//", 1)[1].split(":", 1)[0].split("/", 1)[0]},
            transport=FakeTransport(),
            ledger=ValidationLedger.create(now="20260717T080000Z", nonce="review01"),
            ledger_path=tmp_path / "ledger.json",
            session_cookie="sessionid=x",
            org_id=7,
            execute=True,
            cli_execute=True,
        )


def test_review_contract_any_redirect_is_rejected_without_replay(tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    transport = FakeTransport([HttpResponse(307, {"Location": "/other"}, b"")])
    client = real_runner(tmp_path, transport, execute=True, cli_execute=True).client
    with pytest.raises(HttpProtocolError, match="重定向"):
        client.list_tasks("owned")
    assert len(transport.requests) == 1


def test_execute_management_request_requires_api_secret_before_network(tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    transport = FakeTransport([web_response({})])
    client = real_runner(
        tmp_path,
        transport,
        execute=True,
        cli_execute=True,
        management_api_secret=None,
    ).client

    with pytest.raises(SafetyError, match="CRV_MANAGEMENT_API_SECRET"):
        client.list_tasks("owned")

    assert transport.requests == []


@pytest.mark.parametrize("bad_secret", [123, "secret\r\nInjected: value"])
def test_execute_management_request_rejects_invalid_api_secret_before_network(tmp_path, monkeypatch, bad_secret):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    transport = FakeTransport([web_response({})])
    client = real_runner(
        tmp_path,
        transport,
        execute=True,
        cli_execute=True,
        management_api_secret=bad_secret,
    ).client

    with pytest.raises(SafetyError, match="CRV_MANAGEMENT_API_SECRET"):
        client.list_tasks("owned")

    assert transport.requests == []


@pytest.mark.parametrize(
    "payload",
    [
        {"id": 1},
        {"result": False, "data": {}, "message": "denied"},
        {"result": True, "message": "missing data"},
        {"result": "true", "data": {}, "message": ""},
        {"result": True, "data": [], "message": ""},
    ],
)
def test_review_contract_strict_webutils_envelope(payload, tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    transport = FakeTransport([response(payload=payload)])
    client = real_runner(tmp_path, transport, execute=True, cli_execute=True).client
    with pytest.raises(HttpProtocolError):
        client.list_tasks("owned")


def test_review_contract_real_task_payload_cookie_org_and_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    run_id = "crval_20260717T080000Z_review01"
    model_id = f"{run_id}_model".lower()
    asst_id = "crv_rel_review01"
    transport = FakeTransport(
        [
            web_response(
                {
                    "id": 41,
                    "config": {"model_id": model_id},
                    "credential": {"id": 51},
                    "token": "issued-token",
                }
            ),
            association_response(model_id, asst_id),
            ingest_response(61, instances_received=2, relations_received=1, pending_relations=0),
            ingest_response(62, instances_received=1, relations_received=1, pending_relations=1),
            ingest_response(63, instances_received=1, relations_received=0, pending_relations=0),
            web_response({"credential": {"id": 51}, "token": "rotated-token"}),
            ingest_response(64, instances_received=1, relations_received=0, pending_relations=0),
            web_response(result=False, message="token revoked"),
            web_response({"credential_id": 51, "is_enabled": False}),
            web_response(result=False, message="token revoked"),
        ]
    )
    result = real_runner(tmp_path, transport, execute=True, cli_execute=True).run(mode="quick")

    assert result["requests_sent"] == 10
    assert [request["url"].split("/api/v1/cmdb/api/", 1)[1] for request in transport.requests] == [
        "custom_reporting/tasks/",
        "model/association/",
        "custom_reporting/ingest/",
        "custom_reporting/ingest/",
        "custom_reporting/ingest/",
        "custom_reporting/tasks/41/rotate_credential/",
        "custom_reporting/ingest/",
        "custom_reporting/ingest/",
        "custom_reporting/tasks/41/revoke_credential/",
        "custom_reporting/ingest/",
    ]
    create = transport.requests[0]
    assert create["json_body"] == {
        "name": f"{run_id}_quick_task",
        "team": [7],
        "config": {
            "mode": "quick",
            "cleanup_strategy": "none",
            "identity_keys": ["inst_name"],
        },
        "quick_model": {
            "model_id": f"{run_id}_model".lower(),
            "model_name": f"{run_id}_model",
            "classification_id": "other",
            "identity_keys": ["inst_name"],
        },
        "is_enabled": True,
    }
    assert "sessionid=session-secret" in create["headers"]["Cookie"]
    assert "current_team=7" in create["headers"]["Cookie"]
    management_requests = [request for request in transport.requests if "ingest/" not in request["url"]]
    assert all(request["headers"]["Api-Authorization"] == "management-secret" for request in management_requests)
    assert all("Authorization" not in request["headers"] for request in management_requests)
    ingest_requests = [request for request in transport.requests if "ingest/" in request["url"]]
    assert all("Api-Authorization" not in request["headers"] for request in ingest_requests)
    assert transport.requests[2]["headers"]["Authorization"] == "Bearer issued-token"
    assert transport.requests[6]["headers"]["Authorization"] == "Bearer rotated-token"
    assert transport.requests[8]["json_body"] == {"credential_id": 51}
    serialized = (tmp_path / "ledger.json").read_text()
    assert f"{run_id}:41" in serialized
    assert f"{run_id}:51" in serialized
    assert "issued-token" not in serialized
    assert "rotated-token" not in serialized
    assert "management-secret" not in serialized
    assert "session-secret" not in json.dumps(result)
    assert "management-secret" not in json.dumps(result)


def test_cli_injects_management_api_secret_and_redacts_it_recursively(monkeypatch, tmp_path, capsys):
    secret = "management-api-secret-value"
    captured = {}

    class CapturingRunner:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self, *, mode):
            return {"nested": [{"safe": f"prefix-{captured['management_api_secret']}-suffix"}]}

    monkeypatch.setenv("CRV_ALLOWED_HOSTS", "127.0.0.1")
    monkeypatch.setenv("CRV_MANAGEMENT_API_SECRET", secret)
    monkeypatch.setattr("validation.custom_reporting.http_runner.HttpRunner", CapturingRunner)

    assert (
        main(
            [
                "--dry-run",
                "--base-url",
                "http://127.0.0.1:8011/api/v1/cmdb/api/custom_reporting/",
                "--ledger",
                str(tmp_path / "ledger.json"),
            ]
        )
        == 0
    )

    assert captured["management_api_secret"] == secret
    assert secret not in capsys.readouterr().out


def test_review_contract_standard_uses_seed_response_model_and_real_task_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    run_id = "crval_20260717T080000Z_review01"
    model_id = f"{run_id}_model".lower()
    asst_id = "crv_rel_review01"
    transport = FakeTransport(
        [
            web_response(
                {
                    "id": 71,
                    "config": {"model_id": model_id},
                    "credential": {"id": 81},
                    "token": "seed-token",
                }
            ),
            web_response({}),
            web_response(
                {
                    "id": 72,
                    "config": {"model_id": model_id},
                    "credential": {"id": 82},
                    "token": "standard-token",
                }
            ),
            association_response(model_id, asst_id),
            ingest_response(91, instances_received=2, relations_received=1, pending_relations=0),
            ingest_response(92, instances_received=1, relations_received=1, pending_relations=1),
            ingest_response(93, instances_received=1, relations_received=0, pending_relations=0),
            web_response({"credential": {"id": 82}, "token": "rotated-standard-token"}),
            ingest_response(94, instances_received=1, relations_received=0, pending_relations=0),
            web_response(result=False, message="token revoked"),
            web_response({"credential_id": 82, "is_enabled": False}),
            web_response(result=False, message="token revoked"),
        ]
    )
    real_runner(tmp_path, transport, execute=True, cli_execute=True).run(mode="standard")
    assert transport.requests[1]["method"] == "DELETE"
    assert transport.requests[1]["url"].endswith("tasks/71/")
    standard = transport.requests[2]["json_body"]
    assert standard["config"] == {
        "mode": "standard",
        "model_id": model_id,
        "cleanup_strategy": "none",
        "identity_keys": ["inst_name"],
    }
    assert transport.requests[3]["url"].endswith("/api/v1/cmdb/api/model/association/")


def test_review_contract_execute_reserves_valid_ledger_before_first_post(tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")

    class InspectingTransport(FakeTransport):
        def request(self, **kwargs):
            restored = ValidationLedger.from_json((tmp_path / "ledger.json").read_text())
            assert restored.run_id.endswith("review01")
            return super().request(**kwargs)

    transport = InspectingTransport([web_response({"id": 1})])
    with pytest.raises(HttpProtocolError):
        real_runner(tmp_path, transport, execute=True, cli_execute=True).run(mode="quick")


def test_review_contract_concurrent_ledger_reservation_allows_only_one_writer(tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    path = tmp_path / "ledger.json"
    first = real_runner(tmp_path, FakeTransport(), execute=True, cli_execute=True)
    second = real_runner(tmp_path, FakeTransport(), execute=True, cli_execute=True)
    first.reserve_ledger()
    with pytest.raises(SafetyError, match="账本路径已存在"):
        second.reserve_ledger()
    assert ValidationLedger.from_json(path.read_text()).run_id.endswith("review01")


def test_review_contract_cli_requires_independent_confirmation(monkeypatch, tmp_path):
    monkeypatch.setenv("CRV_ALLOWED_HOSTS", "127.0.0.1")
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    monkeypatch.setenv("CRV_SESSION_COOKIE", "sessionid=x")
    monkeypatch.setenv("CRV_ORG_ID", "7")
    monkeypatch.delenv("CRV_EXECUTE_CONFIRMED", raising=False)
    result = main(
        [
            "--execute",
            "--base-url",
            "http://127.0.0.1:8011/api/v1/cmdb/api/custom_reporting/",
            "--ledger",
            str(tmp_path / "ledger.json"),
        ],
        transport=FakeTransport(),
    )
    assert result == 0


def test_review_contract_cleanup_uses_real_task_delete_and_task_list_only(tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    http_runner = real_runner(tmp_path, FakeTransport(), execute=True, cli_execute=True)
    run_id = http_runner.ledger.run_id
    http_runner.ledger.record("task", f"{run_id}:41")
    http_runner.ledger.record("model", f"{run_id}_model")
    http_runner._persist_ledger()
    http_runner.client.transport.responses = [
        web_response(
            {
                "count": 1,
                "next": None,
                "previous": None,
                "results": [{"id": 41, "name": f"{run_id}_quick_task"}],
            }
        ),
        web_response({}),
        web_response({"count": 0, "next": None, "previous": None, "results": []}),
    ]
    with pytest.raises(CleanupIncompleteError, match="模型"):
        http_runner.cleanup()
    requests = http_runner.client.transport.requests
    assert "tasks/?" in requests[0]["url"]
    assert requests[1]["url"].endswith("tasks/41/")
    assert "tasks/?" in requests[2]["url"]
    assert all("residuals" not in request["url"] for request in requests)
    assert (tmp_path / "ledger.json").exists()


def test_review_contract_secret_substrings_and_known_values_are_recursively_redacted():
    secret = "plain-value"
    rendered = _redact(
        {
            "nested": [
                {"refresh_token": secret},
                {"client_secret": secret},
                {"api_token": secret},
                {"cookie_header": secret},
                {"authentication": secret},
                {"safe": f"prefix-{secret}-suffix"},
            ]
        },
        (secret,),
    )
    assert secret not in json.dumps(rendered)


@pytest.mark.parametrize(
    "bad_response",
    [
        HttpResponse(500, {}, b"{}"),
        HttpResponse(200, {}, b"not-json"),
        HttpResponse(200, {}, b"x" * (1024 * 1024 + 1)),
        RuntimeError("contains secret"),
    ],
)
def test_review_contract_protocol_failures_are_generic_and_bounded(bad_response, tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    client = real_runner(tmp_path, FakeTransport([bad_response]), execute=True, cli_execute=True).client
    with pytest.raises(HttpProtocolError) as exc_info:
        client.list_tasks("owned")
    assert "secret" not in str(exc_info.value)


def test_second_review_quick_rejects_response_model_mismatch_before_ingest(tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    transport = FakeTransport(
        [
            web_response(
                {
                    "id": 101,
                    "config": {"model_id": "server-substituted-model"},
                    "credential": {"id": 201},
                    "token": "issued-token",
                }
            )
        ]
    )

    with pytest.raises(HttpProtocolError, match="model_id"):
        real_runner(tmp_path, transport, execute=True, cli_execute=True).run(mode="quick")

    assert len(transport.requests) == 1
    assert transport.requests[0]["url"].endswith("tasks/")


def test_second_review_standard_rejects_response_model_mismatch_before_ingest(tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    run_id = "crval_20260717T080000Z_review01"
    expected_model_id = f"{run_id}_model".lower()
    transport = FakeTransport(
        [
            web_response(
                {
                    "id": 101,
                    "config": {"model_id": expected_model_id},
                    "credential": {"id": 201},
                    "token": "seed-token",
                }
            ),
            web_response({}),
            web_response(
                {
                    "id": 102,
                    "config": {"model_id": "wrong-standard-model"},
                    "credential": {"id": 202},
                    "token": "standard-token",
                }
            ),
        ]
    )

    with pytest.raises(HttpProtocolError, match="model_id"):
        real_runner(tmp_path, transport, execute=True, cli_execute=True).run(mode="standard")

    assert [request["method"] for request in transport.requests] == ["POST", "DELETE", "POST"]
    assert all(not request["url"].endswith("ingest/") for request in transport.requests)


def test_second_review_ledger_records_validated_model_id_without_run_id_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    run_id = "crval_20260717T080000Z_review01"
    expected_model_id = f"{run_id}_model".lower()
    transport = FakeTransport(
        [
            web_response(
                {
                    "id": 101,
                    "config": {"model_id": expected_model_id},
                    "credential": {"id": 201},
                    "token": "issued-token",
                }
            ),
            RuntimeError("stop after model ownership is recorded"),
        ]
    )
    http_runner = real_runner(tmp_path, transport, execute=True, cli_execute=True)

    with pytest.raises(HttpProtocolError):
        http_runner.run(mode="quick")

    model_identifiers = [resource.identifier for resource in http_runner.ledger.resources if resource.kind == "model"]
    assert model_identifiers == [expected_model_id]


def test_second_review_cleanup_lists_first_and_rejects_owned_task_missing_from_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    http_runner = real_runner(tmp_path, FakeTransport(), execute=True, cli_execute=True)
    run_id = http_runner.ledger.run_id
    http_runner.ledger.record("task", f"{run_id}:41")
    http_runner.client.transport.responses = [
        web_response(
            {
                "count": 2,
                "next": None,
                "previous": None,
                "results": [
                    {"id": 41, "name": f"{run_id}_quick_task"},
                    {"id": 42, "name": f"{run_id}_standard_task"},
                ],
            }
        )
    ]

    with pytest.raises(CleanupIncompleteError):
        http_runner.cleanup()

    assert [request["method"] for request in http_runner.client.transport.requests] == ["GET"]


@pytest.mark.parametrize(
    "page",
    [
        {"count": 2, "next": None, "previous": None, "results": []},
        {"count": 1, "next": "/page/2", "previous": None, "results": [{"id": 41, "name": "owned"}]},
        {"count": 201, "next": None, "previous": None, "results": []},
    ],
)
def test_second_review_task_list_pagination_is_fail_closed(page, tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    http_runner = real_runner(tmp_path, FakeTransport([web_response(page)]), execute=True, cli_execute=True)
    with pytest.raises(HttpProtocolError, match="分页"):
        http_runner._scan_owned_tasks()


def test_second_review_plan_contains_real_ingest_relation_sequence():
    run_id = "crval_20260717T080000Z_review01"
    model_id = f"{run_id}_model".lower()
    model_asst_id = f"{model_id}_crv_rel_review01_{model_id}"
    plan = build_execution_plan("quick", run_id)
    association_step = next(step for step in plan.steps if step.name == "create_model_association")
    relation_steps = [step for step in plan.steps if "relation" in step.name or "backfill" in step.name]

    assert plan.steps.index(association_step) < plan.steps.index(relation_steps[0])
    assert association_step.payload == {
        "src_model_id": model_id,
        "dst_model_id": model_id,
        "asst_id": "crv_rel_review01",
    }
    assert plan.cleanup_order == ("task", "association", "model_verification")
    assert [step.name for step in relation_steps] == [
        "ingest_immediate_relation",
        "ingest_pending_relation",
        "ingest_backfill_target",
    ]
    assert all(step.method == "POST" for step in relation_steps)
    assert relation_steps[0].payload["relations"] == [
        {
            "source": {"model_id": model_id, "identity": {"inst_name": f"{run_id}_immediate_source"}},
            "target": {"model_id": model_id, "identity": {"inst_name": f"{run_id}_immediate_target"}},
            "asst_id": model_asst_id,
        }
    ]
    assert relation_steps[1].payload["relations"][0]["target"]["identity"] == {"inst_name": f"{run_id}_backfill_target"}
    assert relation_steps[2].payload == {
        "instances": [{"inst_name": f"{run_id}_backfill_target", "crv_run_id": run_id}],
        "relations": [],
    }


def test_second_review_runner_executes_relation_ingests_and_records_each_batch(tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    run_id = "crval_20260717T080000Z_review01"
    model_id = f"{run_id}_model".lower()
    asst_id = "crv_rel_review01"
    transport = FakeTransport(
        [
            web_response(
                {
                    "id": 101,
                    "config": {"model_id": model_id},
                    "credential": {"id": 201},
                    "token": "issued-token",
                }
            ),
            association_response(model_id, asst_id),
            ingest_response(301, instances_received=2, relations_received=1, pending_relations=0),
            ingest_response(302, instances_received=1, relations_received=1, pending_relations=1),
            ingest_response(303, instances_received=1, relations_received=0, pending_relations=0),
            web_response({"credential": {"id": 201}, "token": "rotated-token"}),
            ingest_response(304, instances_received=1, relations_received=0, pending_relations=0),
            web_response(result=False, message="上报令牌无效或已作废"),
            web_response({"credential_id": 201, "is_enabled": False}),
            web_response(result=False, message="上报令牌无效或已作废"),
        ]
    )
    http_runner = real_runner(tmp_path, transport, execute=True, cli_execute=True)

    http_runner.run(mode="quick")

    paths = [request["url"].split("/api/v1/cmdb/api/", 1)[1] for request in transport.requests]
    assert paths == [
        "custom_reporting/tasks/",
        "model/association/",
        "custom_reporting/ingest/",
        "custom_reporting/ingest/",
        "custom_reporting/ingest/",
        "custom_reporting/tasks/101/rotate_credential/",
        "custom_reporting/ingest/",
        "custom_reporting/ingest/",
        "custom_reporting/tasks/101/revoke_credential/",
        "custom_reporting/ingest/",
    ]
    planned_relations = [step.payload for step in build_execution_plan("quick", run_id).steps if "relation" in step.name or "backfill" in step.name]
    assert [request["json_body"] for request in transport.requests[2:5]] == planned_relations
    assert [resource.identifier for resource in http_runner.ledger.resources if resource.kind == "batch"] == [
        f"{run_id}:{batch_id}" for batch_id in (301, 302, 303, 304)
    ]
    assert transport.requests[-3]["headers"]["Authorization"] == "Bearer issued-token"
    assert transport.requests[-1]["headers"]["Authorization"] == "Bearer rotated-token"
    assert transport.requests[-3]["json_body"] == {"instances": [], "relations": []}
    assert transport.requests[-1]["json_body"] == {"instances": [], "relations": []}


def test_third_review_records_model_intent_before_quick_task_post_and_keeps_it_on_mismatch(tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    expected_model_id = "crval_20260717t080000z_review01_model"

    class InspectingTransport(FakeTransport):
        def request(self, **kwargs):
            restored = ValidationLedger.from_json((tmp_path / "ledger.json").read_text())
            assert [(item.kind, item.identifier) for item in restored.resources] == [("model", expected_model_id)]
            return super().request(**kwargs)

    transport = InspectingTransport(
        [
            web_response(
                {
                    "id": 101,
                    "config": {"model_id": "server-substituted-model"},
                    "credential": {"id": 201},
                    "token": "issued-token",
                }
            )
        ]
    )

    with pytest.raises(HttpProtocolError, match="model_id"):
        real_runner(tmp_path, transport, execute=True, cli_execute=True).run(mode="quick")

    restored = ValidationLedger.from_json((tmp_path / "ledger.json").read_text())
    assert ("model", expected_model_id) in [(item.kind, item.identifier) for item in restored.resources]


def test_third_review_creates_real_self_association_before_relation_ingest(tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    run_id = "crval_20260717T080000Z_review01"
    model_id = f"{run_id}_model".lower()
    asst_id = f"crv_rel_{run_id.rsplit('_', 1)[-1]}"
    model_asst_id = f"{model_id}_{asst_id}_{model_id}"
    transport = FakeTransport(
        [
            web_response(
                {
                    "id": 101,
                    "config": {"model_id": model_id},
                    "credential": {"id": 201},
                    "token": "issued-token",
                }
            ),
            association_response(model_id, asst_id),
            ingest_response(301, instances_received=2, relations_received=1, pending_relations=0),
            ingest_response(302, instances_received=1, relations_received=1, pending_relations=1),
            ingest_response(303, instances_received=1, relations_received=0, pending_relations=0),
            web_response({"credential": {"id": 201}, "token": "rotated-token"}),
            ingest_response(304, instances_received=1, relations_received=0, pending_relations=0),
            web_response(result=False, message="上报令牌无效或已作废"),
            web_response({"credential_id": 201, "is_enabled": False}),
            web_response(result=False, message="上报令牌无效或已作废"),
        ]
    )
    http_runner = real_runner(tmp_path, transport, execute=True, cli_execute=True)

    http_runner.run(mode="quick")

    association_request = transport.requests[1]
    assert association_request["method"] == "POST"
    assert association_request["url"].endswith("/api/v1/cmdb/api/model/association/")
    assert association_request["json_body"] == {
        "src_model_id": model_id,
        "dst_model_id": model_id,
        "asst_id": asst_id,
    }
    relation_requests = transport.requests[2:5]
    assert all(relation["asst_id"] == model_asst_id for request in relation_requests[:2] for relation in request["json_body"]["relations"])
    associations = [item.identifier for item in http_runner.ledger.resources if item.kind == "association"]
    assert associations == [
        f"{run_id}_association_intent_{model_asst_id}",
        model_asst_id,
    ]


def test_third_review_rejects_generic_result_false_for_token_invalidation(tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    http_runner = real_runner(
        tmp_path,
        FakeTransport([web_response(result=False, message="payload validation failed")]),
        execute=True,
        cli_execute=True,
    )

    with pytest.raises(HttpProtocolError, match="token"):
        http_runner._expect_ingest_rejected("old-token")


def test_third_review_cleanup_wraps_protocol_failures_and_preserves_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    http_runner = real_runner(
        tmp_path,
        FakeTransport(
            [
                web_response(
                    {
                        "count": 1,
                        "next": "/page/2",
                        "previous": None,
                        "results": [
                            {
                                "id": 41,
                                "name": "crval_20260717T080000Z_review01_quick_task",
                            }
                        ],
                    }
                )
            ]
        ),
        execute=True,
        cli_execute=True,
    )
    http_runner.ledger.record("task", f"{http_runner.ledger.run_id}:41")

    with pytest.raises(CleanupIncompleteError, match="清理未完整完成") as exc_info:
        http_runner.cleanup()

    assert "分页" not in str(exc_info.value)
    assert (tmp_path / "ledger.json").exists()


def test_third_review_cleanup_deletes_only_real_ledger_association_id(tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    http_runner = real_runner(tmp_path, FakeTransport(), execute=True, cli_execute=True)
    run_id = http_runner.ledger.run_id
    model_id = f"{run_id}_model".lower()
    model_asst_id = f"{model_id}_crv_rel_review01_{model_id}"
    http_runner.ledger.record("task", f"{run_id}:41")
    http_runner.ledger.record("model", model_id)
    http_runner.ledger.record("association", f"{run_id}_association_intent_{model_asst_id}")
    http_runner.ledger.record("association", model_asst_id)
    http_runner.client.transport.responses = [
        web_response(
            {
                "count": 1,
                "next": None,
                "previous": None,
                "results": [{"id": 41, "name": f"{run_id}_quick_task"}],
            }
        ),
        web_response({}),
        web_response({}),
        web_response({"count": 0, "next": None, "previous": None, "results": []}),
    ]

    with pytest.raises(CleanupIncompleteError, match="模型"):
        http_runner.cleanup()

    deletes = [request for request in http_runner.client.transport.requests if request["method"] == "DELETE"]
    assert [request["url"] for request in deletes] == [
        "http://127.0.0.1:8011/api/v1/cmdb/api/custom_reporting/tasks/41/",
        f"http://127.0.0.1:8011/api/v1/cmdb/api/model/association/{model_asst_id}/",
    ]
    assert (tmp_path / "ledger.json").exists()


def test_third_review_cleanup_wraps_delete_failure_and_preserves_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    http_runner = real_runner(tmp_path, FakeTransport(), execute=True, cli_execute=True)
    run_id = http_runner.ledger.run_id
    http_runner.ledger.record("task", f"{run_id}:41")
    http_runner.client.transport.responses = [
        web_response(
            {
                "count": 1,
                "next": None,
                "previous": None,
                "results": [{"id": 41, "name": f"{run_id}_quick_task"}],
            }
        ),
        HttpResponse(500, {}, b"secret-delete-failure"),
    ]

    with pytest.raises(CleanupIncompleteError, match="清理未完整完成") as exc_info:
        http_runner.cleanup()

    assert "secret" not in str(exc_info.value)
    assert (tmp_path / "ledger.json").exists()


def test_third_review_cleanup_wraps_ledger_ownership_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    http_runner = real_runner(tmp_path, FakeTransport(), execute=True, cli_execute=True)
    http_runner.ledger.record("task", f"{http_runner.ledger.run_id}_legacy_task")

    with pytest.raises(CleanupIncompleteError, match="清理未完整完成"):
        http_runner.cleanup()

    assert http_runner.client.transport.requests == []
    assert (tmp_path / "ledger.json").exists()
