from typing import Any
from urllib.parse import urljoin

import requests

from apps.operation_analysis.services.datasource_preview.base import BaseConnectorExecutor, ConnectorError, PreviewResult
from apps.operation_analysis.services.datasource_preview.network_guard import validate_preview_url
from apps.operation_analysis.services.datasource_preview.schema import infer_fields

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 5


def extract_response_path(payload: Any, response_path: str | None) -> Any:
    if not response_path:
        return payload

    current = payload
    for part in response_path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        raise ConnectorError(f"响应路径不存在: {response_path}", code="rest_response_path_missing", status_code=400)
    return current


def normalize_rest_items(payload: Any) -> tuple[list[dict[str, Any]], int]:
    if isinstance(payload, list):
        items = payload
        count = len(items)
    elif isinstance(payload, dict) and isinstance(payload.get("items"), list):
        items = payload["items"]
        count = int(payload.get("count") or len(items))
    else:
        raise ConnectorError("REST API 响应必须是对象数组或包含 items 数组的对象", code="rest_response_not_list", status_code=400)

    normalized = []
    for item in items:
        normalized.append(item if isinstance(item, dict) else {"value": item})
    return normalized, count


class RestApiConnectorExecutor(BaseConnectorExecutor):
    source_type = "rest_api"

    def __init__(self, http_client=None):
        self.http_client = http_client or requests

    def test_connection(self, connection_config: dict[str, Any]) -> None:
        self.preview(connection_config, {"limit": 1}, limit=1)

    def preview(
        self,
        connection_config: dict[str, Any],
        query_config: dict[str, Any],
        limit: int = 100,
    ) -> PreviewResult:
        url = connection_config.get("url")
        url = validate_preview_url(url)

        method = str(connection_config.get("method") or "GET").upper()
        if method not in {"GET", "POST"}:
            raise ConnectorError("REST API 预览仅支持 GET/POST", code="rest_method_not_supported", status_code=400)

        timeout = min(int(connection_config.get("timeout") or 10), 30)
        headers = connection_config.get("headers") if isinstance(connection_config.get("headers"), dict) else {}
        params = query_config.get("params") if isinstance(query_config.get("params"), dict) else {}
        body = query_config.get("body") if isinstance(query_config.get("body"), dict) else None

        try:
            response = self._request_with_safe_redirects(method, url, headers, params, body, timeout)
            content_length = int(response.headers.get("content-length") or 0)
            if content_length > MAX_RESPONSE_BYTES:
                raise ConnectorError("REST API 响应体过大", code="rest_response_too_large", status_code=400)
            response.raise_for_status()
            payload = response.json()
        except ConnectorError:
            raise
        except Exception as exc:
            raise ConnectorError(f"REST API 请求失败: {exc}", code="rest_request_failed", status_code=502)

        selected = extract_response_path(payload, query_config.get("response_path"))
        items, count = normalize_rest_items(selected)
        limited_items = items[:limit]
        return PreviewResult(items=limited_items, count=count, fields=infer_fields(limited_items))

    def _request_with_safe_redirects(
        self,
        method: str,
        url: str,
        headers: dict[str, Any],
        params: dict[str, Any],
        body: dict[str, Any] | None,
        timeout: int,
    ):
        current_url = url
        for _ in range(MAX_REDIRECTS + 1):
            response = self.http_client.request(
                method=method,
                url=current_url,
                headers=headers,
                params=params,
                json=body if method == "POST" else None,
                timeout=timeout,
                allow_redirects=False,
            )
            status_code = getattr(response, "status_code", 200)
            if status_code not in {301, 302, 303, 307, 308}:
                return response

            location = response.headers.get("location") or response.headers.get("Location")
            if not location:
                return response
            current_url = validate_preview_url(urljoin(current_url, location))

        raise ConnectorError("REST API 重定向次数过多", code="rest_redirect_too_many", status_code=400)
