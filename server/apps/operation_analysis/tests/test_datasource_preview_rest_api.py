import pytest

from apps.operation_analysis.services.datasource_preview.base import ConnectorError
from apps.operation_analysis.services.datasource_preview.rest_api import RestApiConnectorExecutor, extract_response_path, normalize_rest_items


PUBLIC_ADDRINFO = [(None, None, None, None, ("93.184.216.34", 443))]


def test_extract_response_path_reads_nested_list():
    payload = {"data": {"items": [{"name": "a"}]}}
    assert extract_response_path(payload, "data.items") == [{"name": "a"}]


def test_normalize_rest_items_accepts_list_and_items_dict():
    assert normalize_rest_items([{"a": 1}]) == ([{"a": 1}], 1)
    assert normalize_rest_items({"items": [{"a": 1}], "count": 5}) == ([{"a": 1}], 5)


def test_normalize_rest_items_rejects_scalar():
    with pytest.raises(ConnectorError) as exc:
        normalize_rest_items({"ok": True})

    assert exc.value.code == "rest_response_not_list"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    monkeypatch.setattr("apps.core.utils.ssrf_validator.socket.getaddrinfo", lambda *args, **kwargs: PUBLIC_ADDRINFO)


def test_rest_preview_uses_http_client_and_infers_fields():
    calls = []

    class FakeResponse:
        headers = {"content-length": "64"}

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"items": [{"date": "2026-06-01", "users": 120}], "count": 1}}

    class FakeClient:
        def request(self, **kwargs):
            calls.append(kwargs)
            return FakeResponse()

    executor = RestApiConnectorExecutor(http_client=FakeClient())
    result = executor.preview(
        {
            "url": "https://example.com/orders",
            "method": "GET",
            "headers": {"Authorization": "Bearer x"},
            "timeout": 3,
        },
        {"response_path": "data", "limit": 100},
        limit=100,
    )

    assert calls[0]["method"] == "GET"
    assert calls[0]["url"] == "https://example.com/orders"
    assert result.as_dict() == {
        "items": [{"date": "2026-06-01", "users": 120}],
        "count": 1,
        "fields": [
            {"key": "date", "title": "date", "value_type": "datetime"},
            {"key": "users", "title": "users", "value_type": "number"},
        ],
    }


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8000/admin",
        "http://localhost:8000/admin",
        "http://10.0.0.5/api",
        "http://172.16.0.5/api",
        "http://192.168.1.1/api",
        "file:///etc/passwd",
    ],
)
def test_rest_preview_rejects_forbidden_targets_before_request(url):
    class FakeClient:
        def request(self, **kwargs):
            raise AssertionError("禁止目标不应发起 HTTP 请求")

    with pytest.raises(ConnectorError) as exc:
        RestApiConnectorExecutor(http_client=FakeClient()).preview({"url": url}, {}, limit=1)

    assert exc.value.code == "rest_url_forbidden"


def test_rest_preview_rejects_redirect_to_forbidden_target():
    calls = []

    class RedirectResponse:
        status_code = 302
        headers = {"location": "http://127.0.0.1/admin"}

        def raise_for_status(self):
            return None

        def json(self):
            return [{"ok": True}]

    class FakeClient:
        def request(self, **kwargs):
            calls.append(kwargs)
            return RedirectResponse()

    with pytest.raises(ConnectorError) as exc:
        RestApiConnectorExecutor(http_client=FakeClient()).preview({"url": "https://example.com/orders"}, {}, limit=1)

    assert exc.value.code == "rest_url_forbidden"
    assert calls[0]["allow_redirects"] is False
