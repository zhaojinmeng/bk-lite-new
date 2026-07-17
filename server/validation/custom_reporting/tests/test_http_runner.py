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
    transport = FakeTransport(
        [
            web_response(
                {
                    "id": 41,
                    "config": {"model_id": "owned_model"},
                    "credential": {"id": 51},
                    "token": "issued-token",
                }
            ),
            web_response({"batch_id": 61, "summary": {}}),
            web_response({"credential": {"id": 51}, "token": "rotated-token"}),
            web_response({"batch_id": 62, "summary": {}}),
            web_response({"credential_id": 51, "is_enabled": False}),
        ]
    )
    result = real_runner(tmp_path, transport, execute=True, cli_execute=True).run(mode="quick")

    assert result["requests_sent"] == 5
    assert [request["url"].split("custom_reporting/", 1)[1] for request in transport.requests] == [
        "tasks/",
        "ingest/",
        "tasks/41/rotate_credential/",
        "ingest/",
        "tasks/41/revoke_credential/",
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
    assert transport.requests[1]["headers"]["Authorization"] == "Bearer issued-token"
    assert transport.requests[3]["headers"]["Authorization"] == "Bearer rotated-token"
    assert transport.requests[4]["json_body"] == {"credential_id": 51}
    serialized = (tmp_path / "ledger.json").read_text()
    assert f"{run_id}:41" in serialized
    assert f"{run_id}:51" in serialized
    assert "issued-token" not in serialized
    assert "rotated-token" not in serialized
    assert "session-secret" not in json.dumps(result)


def test_review_contract_standard_uses_seed_response_model_and_real_task_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("CRV_ALLOW_WRITE", "1")
    transport = FakeTransport(
        [
            web_response(
                {
                    "id": 71,
                    "config": {"model_id": "server-returned-model"},
                    "credential": {"id": 81},
                    "token": "seed-token",
                }
            ),
            web_response({}),
            web_response(
                {
                    "id": 72,
                    "config": {"model_id": "server-returned-model"},
                    "credential": {"id": 82},
                    "token": "standard-token",
                }
            ),
            web_response({"batch_id": 91, "summary": {}}),
            web_response({"credential": {"id": 82}, "token": "rotated-standard-token"}),
            web_response({"batch_id": 92, "summary": {}}),
            web_response({"credential_id": 82, "is_enabled": False}),
        ]
    )
    real_runner(tmp_path, transport, execute=True, cli_execute=True).run(mode="standard")
    assert transport.requests[1]["method"] == "DELETE"
    assert transport.requests[1]["url"].endswith("tasks/71/")
    standard = transport.requests[2]["json_body"]
    assert standard["config"] == {
        "mode": "standard",
        "model_id": "server-returned-model",
        "cleanup_strategy": "none",
        "identity_keys": ["inst_name"],
    }


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
        web_response({}),
        web_response({"count": 0, "next": None, "previous": None, "results": []}),
    ]
    with pytest.raises(CleanupIncompleteError, match="模型"):
        http_runner.cleanup()
    requests = http_runner.client.transport.requests
    assert requests[0]["url"].endswith("tasks/41/")
    assert "tasks/?" in requests[1]["url"]
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
