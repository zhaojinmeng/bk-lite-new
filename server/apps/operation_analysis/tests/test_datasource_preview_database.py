import pytest

from apps.operation_analysis.services.datasource_preview.base import ConnectorError
from apps.operation_analysis.services.datasource_preview.database import (
    DatabaseConnectorExecutor,
    build_database_url,
    build_preview_sql,
    ensure_select_sql,
    normalize_db_rows,
)


PUBLIC_ADDRINFO = [(None, None, None, None, ("93.184.216.34", 3306))]


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    monkeypatch.setattr("apps.core.utils.ssrf_validator.socket.getaddrinfo", lambda *args, **kwargs: PUBLIC_ADDRINFO)


def test_ensure_select_sql_accepts_single_select():
    assert ensure_select_sql(" select id, name from users ") == "select id, name from users"


@pytest.mark.parametrize("sql", ["delete from users", "select 1; select 2", "update users set name='x'"])
def test_ensure_select_sql_rejects_dangerous_sql(sql):
    with pytest.raises(ConnectorError):
        ensure_select_sql(sql)


def test_build_preview_sql_from_mysql_table_name_quotes_identifier():
    assert build_preview_sql({"table": "orders"}, 100, "mysql") == "SELECT * FROM `orders` LIMIT 100"


def test_build_preview_sql_from_postgresql_table_name_quotes_identifier():
    assert build_preview_sql({"table": "orders"}, 100, "postgresql") == 'SELECT * FROM "orders" LIMIT 100'


def test_build_preview_sql_adds_limit_to_select():
    assert build_preview_sql({"sql": "select id from orders"}, 20) == "select id from orders LIMIT 20"


def test_build_database_url_for_mysql_uses_utf8mb4_charset():
    url = build_database_url(
        "mysql",
        {
            "host": "db.example.com",
            "port": 3306,
            "database": "ops",
            "username": "root",
            "password": "secret",
        },
    )

    assert url == "mysql+pymysql://root:secret@db.example.com:3306/ops?charset=utf8mb4"


def test_build_database_url_escapes_credentials():
    url = build_database_url(
        "postgresql",
        {
            "host": "db.example.com",
            "port": 5432,
            "database": "ops",
            "username": "ops user",
            "password": "pa:ss@word",
        },
    )

    assert url == "postgresql+psycopg2://ops+user:pa%3Ass%40word@db.example.com:5432/ops"


def test_normalize_db_rows_converts_row_mappings():
    rows = [{"id": 1, "name": "a"}]
    assert normalize_db_rows(rows) == [{"id": 1, "name": "a"}]


@pytest.mark.parametrize(
    "host",
    [
        "169.254.169.254",
        "127.0.0.1",
        "localhost",
        "10.0.0.5",
        "172.16.0.5",
        "192.168.1.1",
    ],
)
def test_build_database_url_rejects_forbidden_hosts(host):
    with pytest.raises(ConnectorError) as exc:
        build_database_url(
            "mysql",
            {
                "host": host,
                "port": 3306,
                "database": "ops",
                "username": "root",
                "password": "secret",
            },
        )

    assert exc.value.code == "db_host_forbidden"


def test_database_preview_rejects_resolved_private_host_before_engine(monkeypatch):
    monkeypatch.setattr(
        "apps.core.utils.ssrf_validator.socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("10.0.0.5", 3306))],
    )

    def engine_factory(*args, **kwargs):
        raise AssertionError("禁止目标不应创建数据库连接")

    with pytest.raises(ConnectorError) as exc:
        DatabaseConnectorExecutor("mysql", engine_factory=engine_factory).preview(
            {
                "host": "db.internal.example.com",
                "port": 3306,
                "database": "ops",
                "username": "root",
                "password": "secret",
            },
            {"table": "orders"},
            limit=1,
        )

    assert exc.value.code == "db_host_forbidden"


def test_database_preview_error_message_does_not_include_password():
    def engine_factory(database_url, **kwargs):
        raise RuntimeError(f"cannot connect with {database_url}")

    with pytest.raises(ConnectorError) as exc:
        DatabaseConnectorExecutor("mysql", engine_factory=engine_factory).preview(
            {
                "host": "db.example.com",
                "port": 3306,
                "database": "ops",
                "username": "root",
                "password": "super-secret",
            },
            {"table": "orders"},
            limit=1,
        )

    assert exc.value.code == "db_preview_failed"
    assert "super-secret" not in exc.value.message
