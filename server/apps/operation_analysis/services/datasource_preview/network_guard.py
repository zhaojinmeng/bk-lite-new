import ipaddress
import socket
from urllib.parse import urlparse

from apps.core.utils.ssrf_validator import SSRFError, SSRFValidator
from apps.operation_analysis.services.datasource_preview.base import ConnectorError


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    for network in [*SSRFValidator.CLOUD_METADATA_NETWORKS, *SSRFValidator.BLOCKED_NETWORKS]:
        try:
            if ip in network:
                return True
        except TypeError:
            continue
    return False


def _validate_resolved_host(hostname: str, port: int | None) -> None:
    try:
        addr_infos = socket.getaddrinfo(hostname, port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SSRFError(f"无法解析主机名 {hostname}: {exc}")

    if not addr_infos:
        raise SSRFError(f"主机名 {hostname} 无法解析")

    for info in addr_infos:
        ip_text = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            raise SSRFError(f"目标地址被禁止: {ip_text}")


def _validate_hostname(hostname: str, port: int | None = None) -> None:
    normalized = hostname.strip().lower()
    if normalized in {"localhost", *SSRFValidator.CLOUD_METADATA_HOSTS}:
        raise SSRFError(f"禁止访问目标主机: {normalized}")

    try:
        ip = ipaddress.ip_address(normalized)
    except ValueError:
        _validate_resolved_host(normalized, port)
        return

    if _is_blocked_ip(ip):
        raise SSRFError(f"目标地址被禁止: {ip}")


def validate_preview_url(url: str, *, code: str = "rest_url_forbidden") -> str:
    if not url or not str(url).strip():
        raise ConnectorError("REST API URL 不能为空", code="rest_url_required", status_code=400)

    normalized_url = str(url).strip()
    parsed = urlparse(normalized_url)
    scheme = parsed.scheme.lower()
    if scheme not in SSRFValidator.ALLOWED_SCHEMES:
        raise ConnectorError(f"REST API URL 不允许的协议: {scheme}", code=code, status_code=400)
    if not parsed.netloc or not parsed.hostname:
        raise ConnectorError("REST API URL 必须包含有效主机名", code=code, status_code=400)
    try:
        parsed_port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError:
        raise ConnectorError("REST API URL 端口不合法", code=code, status_code=400)

    try:
        _validate_hostname(parsed.hostname, parsed_port)
    except SSRFError as exc:
        raise ConnectorError(f"REST API URL 目标不允许访问: {exc}", code=code, status_code=400)
    return normalized_url


def validate_preview_host(host: str, port: int | str | None = None, *, code: str = "db_host_forbidden") -> str:
    if host is None or not str(host).strip():
        raise ConnectorError("数据库连接信息不完整", code="db_config_incomplete", status_code=400)

    host_text = str(host).strip()
    if "://" in host_text:
        host_text = urlparse(host_text).hostname or host_text

    try:
        parsed_port = int(port) if port not in (None, "") else None
    except (TypeError, ValueError):
        raise ConnectorError("数据库端口不合法", code="db_port_invalid", status_code=400)

    try:
        _validate_hostname(host_text, parsed_port)
    except SSRFError as exc:
        raise ConnectorError(f"数据库主机不允许访问: {exc}", code=code, status_code=400)
    return host_text
