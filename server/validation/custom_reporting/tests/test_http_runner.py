import json

import pytest

from validation.custom_reporting.http_runner import (
    CleanupIncompleteError,
    HttpProtocolError,
    HttpResponse,
    HttpRunner,
    RequestsTransport,
    SafetyError,
    build_execution_plan,
    main,
)
from validation.custom_reporting.ledger import ResourceRef, ValidationLedger


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


def test_default_run_is_dry_run_and_renders_secret_free_namespaced_plan(tmp_path):
    transport = FakeTransport()
    result = runner(tmp_path, transport).run(mode="quick", token="super-secret")

    assert result["dry_run"] is True
    assert result["requests_sent"] == 0
    assert result["run_id"] == "crval_20260717T080000Z_a1b2c3"
    assert [step["name"] for step in result["steps"]] == [
        "create_seed_model",
        "create_seed_task",
        "register_fields",
        "ingest",
        "create_relation",
        "rotate_token",
        "revoke_token",
    ]
    assert result["cleanup_order"] == ["edge", "instance", "credential", "task", "model"]
    assert all(result["run_id"] in name for name in result["resource_names"])
    assert "super-secret" not in json.dumps(result)
    assert transport.requests == []


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


def test_redirect_target_is_revalidated_before_following(tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    transport = FakeTransport(
        [
            HttpResponse(302, {"Location": "https://metadata.internal/latest"}, b""),
        ]
    )

    with pytest.raises(SafetyError, match="host"):
        runner(tmp_path, transport, execute=True, cli_execute=True).client.create_model("owned-model")

    assert len(transport.requests) == 1


def test_redirect_on_same_allowed_host_is_explicit_and_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    transport = FakeTransport(
        [
            HttpResponse(307, {"Location": "/safe-target"}, b""),
            response(payload={"id": "created"}),
        ]
    )

    result = runner(tmp_path, transport, execute=True, cli_execute=True).client.create_model("owned-model")

    assert result == {"id": "created"}
    assert [request["url"] for request in transport.requests] == [
        "https://cmdb.example.test/api/v1/cmdb-enterprise/custom-reporting/models/",
        "https://cmdb.example.test/safe-target",
    ]


def test_transport_receives_bounded_timeouts_and_response_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    transport = FakeTransport([response(payload={"id": "created"})])

    runner(tmp_path, transport, execute=True, cli_execute=True).client.create_model("owned-model")

    request = transport.requests[0]
    assert request["connect_timeout"] == 3.0
    assert request["read_timeout"] == 10.0
    assert request["max_response_bytes"] == 1024 * 1024


@pytest.mark.parametrize(
    ("bad_response", "message"),
    [
        (response(status=503, payload={"token": "leaked-secret"}), "HTTP 503"),
        (HttpResponse(200, {}, b"not-json"), "JSON"),
        (HttpResponse(200, {}, b"x" * (1024 * 1024 + 1)), "响应体"),
        (response(payload=[]), "JSON object"),
    ],
)
def test_protocol_failure_is_redacted_and_stops_followup_writes(tmp_path, monkeypatch, bad_response, message):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    transport = FakeTransport([bad_response, response(payload={"id": 2})])

    with pytest.raises(HttpProtocolError, match=message) as exc_info:
        runner(tmp_path, transport, execute=True, cli_execute=True).client.create_model("owned-model", token="request-secret")

    rendered = str(exc_info.value)
    assert "request-secret" not in rendered
    assert "leaked-secret" not in rendered
    assert len(transport.requests) == 1


def test_authorization_and_tokens_are_redacted_from_results_plan_and_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    transport = FakeTransport([response(payload={"id": "model-id", "token": "response-secret"})])
    http_runner = runner(tmp_path, transport, execute=True, cli_execute=True)

    result = http_runner.client.create_model("owned-model", token="request-secret")

    assert result == {"id": "model-id", "token": "***"}
    rendered_request = json.dumps(transport.requests[0])
    assert "request-secret" in rendered_request
    persisted = (tmp_path / "ledger.json").read_text() if (tmp_path / "ledger.json").exists() else ""
    assert "request-secret" not in persisted
    assert "response-secret" not in json.dumps(result)
    assert "response-secret" not in persisted


def test_successful_creations_are_immediately_persisted_and_recoverable(tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    transport = FakeTransport([response(payload={"id": "crval_20260717T080000Z_a1b2c3_model"})])
    http_runner = runner(tmp_path, transport, execute=True, cli_execute=True)

    http_runner.create_seed_model()

    restored = ValidationLedger.from_json((tmp_path / "ledger.json").read_text())
    assert restored.cleanup_plan() == [ResourceRef("model", "crval_20260717T080000Z_a1b2c3_model")]


def test_execute_refuses_to_overwrite_an_existing_ledger_before_network(tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text("do-not-overwrite")
    transport = FakeTransport()
    http_runner = runner(tmp_path, transport, execute=True, cli_execute=True)

    with pytest.raises(SafetyError, match="账本路径已存在"):
        http_runner.run(mode="quick", token="secret")

    assert ledger_path.read_text() == "do-not-overwrite"
    assert transport.requests == []


def test_resource_names_are_unique_between_runs():
    first = ValidationLedger.create(now="20260717T080000Z", nonce="a1b2c3")
    second = ValidationLedger.create(now="20260717T080000Z", nonce="d4e5f6")

    first_names = set(build_execution_plan("quick", first.run_id).resource_names)
    second_names = set(build_execution_plan("quick", second.run_id).resource_names)

    assert first_names.isdisjoint(second_names)


def test_standard_plan_uses_only_own_quick_seed_and_deletes_seed_task_first():
    run_id = "crval_20260717T080000Z_a1b2c3"
    plan = build_execution_plan("standard", run_id)

    assert plan.steps[0].name == "create_seed_model"
    assert plan.steps[1].name == "create_seed_task"
    assert plan.steps[2].name == "delete_seed_task"
    standard = next(step for step in plan.steps if step.name == "create_standard_task")
    assert standard.payload["model_id"] == f"{run_id}_model"
    assert "existing" not in json.dumps(plan.to_safe_dict())


def test_standard_execute_runs_full_client_workflow_and_records_each_created_resource(tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    run_id = "crval_20260717T080000Z_a1b2c3"
    transport = FakeTransport(
        [
            response(payload={"id": f"{run_id}_model"}),
            response(payload={"id": f"{run_id}_seed_task"}),
            response(payload={}),
            response(payload={"id": f"{run_id}_standard_task"}),
            response(payload={}),
            response(payload={"id": 101}),
            response(payload={"id": 202}),
            response(payload={"id": 303, "token": "rotated-secret"}),
            response(payload={}),
        ]
    )
    http_runner = runner(tmp_path, transport, execute=True, cli_execute=True)

    result = http_runner.run(mode="standard", token="request-secret")

    assert result["dry_run"] is False
    assert result["requests_sent"] == 9
    assert [resource.kind for resource in http_runner.ledger.resources] == [
        "model",
        "task",
        "task",
        "instance",
        "edge",
        "credential",
    ]
    assert transport.requests[2]["method"] == "DELETE"
    assert f"{run_id}_seed_task" in transport.requests[2]["url"]
    assert transport.requests[3]["json_body"]["model_id"] == f"{run_id}_model"
    assert "rotated-secret" not in json.dumps(result)
    assert "request-secret" not in (tmp_path / "ledger.json").read_text()


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


@pytest.mark.parametrize(
    "resolver",
    [lambda _hostname: ["127.0.0.1"], lambda _hostname: [], lambda _hostname: ["not-an-ip"]],
)
def test_dns_validation_rejects_private_empty_and_invalid_answers(tmp_path, resolver):
    with pytest.raises(SafetyError, match="DNS"):
        HttpRunner(
            base_url="https://cmdb.example.test/api/",
            allowed_hosts={"cmdb.example.test"},
            transport=FakeTransport(),
            ledger=ValidationLedger.create(now="20260717T080000Z", nonce="a1b2c3"),
            ledger_path=tmp_path / "ledger.json",
            resolver=resolver,
        )


def test_cleanup_uses_only_ledger_plan_and_scans_only_run_owned_identifiers(tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    ledger = ValidationLedger.create(now="20260717T080000Z", nonce="a1b2c3")
    ledger.record("model", f"{ledger.run_id}_model")
    ledger.record("task", f"{ledger.run_id}_task")
    ledger.record("instance", 101)
    transport = FakeTransport(
        [
            response(payload={}),
            response(payload={}),
            response(payload={}),
            response(payload={"items": []}),
        ]
    )
    http_runner = HttpRunner(
        base_url="https://cmdb.example.test/api/",
        allowed_hosts={"cmdb.example.test"},
        transport=transport,
        ledger=ledger,
        ledger_path=tmp_path / "ledger.json",
        execute=True,
        cli_execute=True,
        resolver=public_resolver,
    )

    http_runner.cleanup()

    urls = [request["url"] for request in transport.requests]
    assert urls[:3] == [
        "https://cmdb.example.test/api/instances/101/",
        f"https://cmdb.example.test/api/tasks/{ledger.run_id}_task/",
        f"https://cmdb.example.test/api/models/{ledger.run_id}_model/",
    ]
    assert ledger.run_id in urls[3]
    assert "existing" not in json.dumps(transport.requests)
    assert not (tmp_path / "ledger.json").exists()


def test_cleanup_failure_retains_ledger_and_raises_incomplete(tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    ledger = ValidationLedger.create(now="20260717T080000Z", nonce="a1b2c3")
    ledger.record("model", f"{ledger.run_id}_model")
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(ledger.to_json())
    transport = FakeTransport([response(status=500)])
    http_runner = HttpRunner(
        base_url="https://cmdb.example.test/api/",
        allowed_hosts={"cmdb.example.test"},
        transport=transport,
        ledger=ledger,
        ledger_path=ledger_path,
        execute=True,
        cli_execute=True,
        resolver=public_resolver,
    )

    with pytest.raises(CleanupIncompleteError):
        http_runner.cleanup()

    assert ledger_path.exists()
    assert ValidationLedger.from_json(ledger_path.read_text()).resources == ledger.resources


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
