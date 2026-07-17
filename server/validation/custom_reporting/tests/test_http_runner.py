import json

import pytest

from validation.custom_reporting.http_runner import (
    CleanupIncompleteError,
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


@pytest.mark.parametrize("flag", ["--verify-ledger", "--cleanup-ledger"])
def test_reserved_cli_commands_fail_explicitly(flag, tmp_path):
    with pytest.raises(SystemExit, match="尚未实现"):
        main([flag, "--ledger", str(tmp_path / "ledger.json")])


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
    return HttpRunner(
        base_url="http://127.0.0.1:8011/api/v1/cmdb/api/custom_reporting/",
        allowed_hosts={"127.0.0.1"},
        transport=transport,
        ledger=ValidationLedger.create(now="20260717T080000Z", nonce="review01"),
        ledger_path=tmp_path / "ledger.json",
        session_cookie="sessionid=session-secret",
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
    assert transport.requests[2]["headers"]["Authorization"] == "Bearer issued-token"
    assert transport.requests[6]["headers"]["Authorization"] == "Bearer rotated-token"
    assert transport.requests[8]["json_body"] == {"credential_id": 51}
    serialized = (tmp_path / "ledger.json").read_text()
    assert f"{run_id}:41" in serialized
    assert f"{run_id}:51" in serialized
    assert "issued-token" not in serialized
    assert "rotated-token" not in serialized
    assert "session-secret" not in json.dumps(result)


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
