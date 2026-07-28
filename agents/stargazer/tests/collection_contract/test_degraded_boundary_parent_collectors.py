import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
import yaml
from conftest import REPOSITORY_ROOT
from plugins import base_utils, script_executor
from plugins.base_utils import convert_to_prometheus_format
from service.collection_service import CollectionService
from tasks.utils.nats_helper import convert_prometheus_to_influx


STARGAZER_ROOT = REPOSITORY_ROOT / "agents" / "stargazer"
SSH_CASES = ("hbase", "keepalived", "openresty", "rocketmq", "spark")
FIXED_TIME = 1_700_000_000.123
DB_MULTI_RECORD_NA = (
    "MSSQLInfo._exec_sql 与 OracleInfo._exec_sql 是实例属性聚合查询，生产接口明确"
    "调用 cursor.fetchone()；它们不枚举资源，也没有分页协议。"
)


def _run_publish_pipeline(model_id, result, monkeypatch):
    monkeypatch.setattr(base_utils.time, "time", lambda: FIXED_TIME)
    normalized = CollectionService(
        {
            "plugin_name": f"{model_id}_info",
            "model_id": model_id,
            "host": "192.0.2.90",
        }
    )._process_result(deepcopy(result))
    prometheus = convert_to_prometheus_format(normalized)
    lines = convert_prometheus_to_influx(
        prometheus,
        {
            "monitor_type": model_id,
            "plugin_name": f"{model_id}_info",
            "model_id": model_id,
            "tags": {
                "agent_id": "agent-contract",
                "instance_id": f"cmdb-{model_id}",
                "instance_type": model_id,
                "collect_type": "discovery",
                "config_type": "production-contract",
            },
        },
    )
    return normalized, prometheus, lines


def _ssh_script(case_id):
    config = yaml.safe_load(
        (
            STARGAZER_ROOT / "plugins" / "inputs" / case_id / "plugin.yml"
        ).read_text(encoding="utf-8")
    )
    executor = config["executors"][config["default_executor"]]
    return executor["scripts"][executor["default_script"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id", SSH_CASES)
@pytest.mark.parametrize(
    ("scenario", "boundary_result", "expected_success", "expected_count"),
    (
        (
            "normal_non_empty",
            json.dumps(
                [
                    {
                        "inst_name": "service.example.invalid",
                        "version": "1.0",
                        "contract_zero": 0,
                        "contract_false": False,
                        "contract_empty": "",
                    }
                ]
            ),
            True,
            1,
        ),
        ("empty", "[]", True, 0),
        (
            "missing_optional_field",
            json.dumps([{"inst_name": "service.example.invalid"}]),
            True,
            1,
        ),
        (
            "multi_record",
            json.dumps(
                [
                    {"inst_name": "service-a.example.invalid", "contract_zero": 0},
                    {"inst_name": "service-b.example.invalid", "contract_zero": 0},
                ]
            ),
            True,
            2,
        ),
        (
            "authentication_or_protocol_error",
            None,
            False,
            None,
        ),
    ),
)
async def test_SSH降级对象只Mock命令边界并运行真实父collector五态(
    case_id,
    scenario,
    boundary_result,
    expected_success,
    expected_count,
    monkeypatch,
):
    async def nats_boundary(subject, payload, timeout):
        request = json.loads(payload)
        assert subject == "ssh.execute.node-contract"
        assert request["args"][0]["command"].strip()
        assert request["args"][0]["connection_test"] is True
        assert timeout > 0
        if scenario == "authentication_or_protocol_error":
            return {
                "success": False,
                "error": f"{case_id} SSH authentication rejected",
                "result": "",
            }
        return {"success": True, "result": boundary_result}

    monkeypatch.setattr(script_executor, "nats_request", nats_boundary)
    collector = script_executor.SSHPlugin(
        {
            "node_id": "node-contract",
            "host": "192.0.2.90",
            "script_path": _ssh_script(case_id),
            "model_id": case_id,
        }
    )

    result = await collector.list_all_resources()

    assert result["success"] is expected_success, scenario
    if not expected_success:
        assert f"{case_id} SSH authentication rejected" in result["result"][
            "cmdb_collect_error"
        ]
        return

    records = result["result"].get(case_id, [])
    assert len(records) == expected_count
    if records:
        normalized, prometheus, lines = _run_publish_pipeline(
            case_id, result, monkeypatch
        )
        assert len(normalized[case_id]) == expected_count
        assert f"{case_id}_info" in prometheus
        assert len(lines) == expected_count
    if scenario == "normal_non_empty":
        labels = normalized[case_id][0]
        assert labels["contract_zero"] == 0
        assert labels["contract_false"] is False
        assert labels["contract_empty"] == ""
        assert 'contract_zero="0"' in prometheus
        assert 'contract_false="False"' in prometheus
        assert 'contract_empty=""' in prometheus
        assert "contract_zero=0" in lines[0]
        assert "contract_false=False" in lines[0]
        assert "contract_empty=" not in lines[0]


class _MSSQLCursor:
    def __init__(self, values):
        self.values = values
        self.description = ()
        self.current = None
        self.closed = False
        self.fetchall_called = False

    def execute(self, query):
        self.current = self.values[query]
        self.description = ((self.current[0],),)

    def fetchone(self):
        return (self.current[1],) if self.current[1] is not None else None

    def fetchall(self):
        self.fetchall_called = True
        raise AssertionError("实例级collector不得枚举第二行")

    def close(self):
        self.closed = True


class _MSSQLConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


def _mssql_values(*, empty=False, missing_optional=False):
    from plugins.inputs.mssql.mssql_info import MSSQLInfo

    values = {
        MSSQLInfo.SQL_QUERIES["version"]: ("version", None if empty else "16.0"),
        MSSQLInfo.SQL_QUERIES["max_conn"]: ("max_conn", None if empty else 100),
        MSSQLInfo.SQL_QUERIES["max_mem"]: ("max_mem_mb", None if empty else 2048),
        MSSQLInfo.SQL_QUERIES["order_rule"]: (
            "order_rule",
            None if empty or missing_optional else "Latin1_General_CI_AS",
        ),
        MSSQLInfo.SQL_QUERIES["fill_factor"]: (
            "fill_factor",
            None if empty else 0,
        ),
        MSSQLInfo.SQL_QUERIES["boot_account"]: (
            "boot_account",
            None if empty or missing_optional else "",
        ),
    }
    return values


@pytest.mark.parametrize(
    "scenario",
    (
        "normal_non_empty",
        "empty",
        "missing_optional_field",
        "multi_record_not_applicable",
        "authentication_or_protocol_error",
    ),
)
def test_MSSQL只Mockpyodbc边界并运行真实父collector五态(scenario, monkeypatch):
    from plugins.inputs.mssql import mssql_info

    cursor = _MSSQLCursor(
        _mssql_values(
            empty=scenario == "empty",
            missing_optional=scenario == "missing_optional_field",
        )
    )
    connection = _MSSQLConnection(cursor)

    def connect_boundary(*args, **kwargs):
        if scenario == "authentication_or_protocol_error":
            raise mssql_info.pyodbc.Error("MSSQL authentication rejected")
        return connection

    monkeypatch.setattr(mssql_info.pyodbc, "connect", connect_boundary)
    collector = mssql_info.MSSQLInfo(
        {
            "host": "192.0.2.91",
            "port": 1433,
            "user": "contract-user",
            "password": "contract-password",
            "database": "contract_db",
        }
    )

    result = collector.list_all_resources()

    if scenario == "authentication_or_protocol_error":
        assert result["success"] is False
        assert "MSSQL authentication rejected" in result["result"][
            "cmdb_collect_error"
        ]
        return

    assert result["success"] is True
    record = result["result"]["mssql"][0]
    assert record["fill_factor"] == "0"
    if scenario in ("empty", "missing_optional_field"):
        assert record["order_rule"] == ""
    if scenario == "multi_record_not_applicable":
        assert "fetchone()" in DB_MULTI_RECORD_NA
        assert cursor.fetchall_called is False
    normalized, prometheus, lines = _run_publish_pipeline(
        "mssql", result, monkeypatch
    )
    assert normalized["mssql"][0]["fill_factor"] == "0"
    assert 'fill_factor="0"' in prometheus
    assert "fill_factor=0" in lines[0]
    assert "mssql_info" in prometheus
    assert len(lines) == 1
    assert cursor.closed and connection.closed


class _OracleCursor:
    def __init__(self, values):
        self.values = values
        self.description = ()
        self.current = None
        self.fetchall_called = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query):
        self.current = self.values[query]
        self.description = ((self.current[0],),)

    def fetchone(self):
        return (self.current[1],) if self.current[1] is not None else None

    def fetchall(self):
        self.fetchall_called = True
        raise AssertionError("实例级collector不得枚举第二行")


class _OracleConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self._cursor


def _oracle_values(*, empty=False, missing_optional=False):
    from plugins.inputs.oracle.oracle_info import OracleInfo

    return {
        OracleInfo.SQL_QUERIES["version"]: ("BANNER", None if empty else "Oracle 19c"),
        OracleInfo.SQL_QUERIES["max_mem"]: (
            "TOTAL_MEMORY",
            None if empty else 1024,
        ),
        OracleInfo.SQL_QUERIES["max_conn"]: ("VALUE", None if empty else 100),
        OracleInfo.SQL_QUERIES["db_name"]: ("NAME", None if empty else "ORCL"),
        OracleInfo.SQL_QUERIES["database_role"]: (
            "DATABASE_ROLE",
            None if empty or missing_optional else "PRIMARY",
        ),
        OracleInfo.SQL_QUERIES["sid"]: (
            "SID",
            None if empty or missing_optional else "",
        ),
    }


@pytest.mark.parametrize(
    "scenario",
    (
        "normal_non_empty",
        "empty",
        "missing_optional_field",
        "multi_record_not_applicable",
        "authentication_or_protocol_error",
    ),
)
def test_Oracle只Mockoracledb边界并运行真实父collector五态(
    scenario, monkeypatch
):
    from plugins.inputs.oracle import oracle_info

    cursor = _OracleCursor(
        _oracle_values(
            empty=scenario == "empty",
            missing_optional=scenario == "missing_optional_field",
        )
    )
    connection = _OracleConnection(cursor)

    def connect_boundary(**kwargs):
        if scenario == "authentication_or_protocol_error":
            raise oracle_info.oracledb.Error("Oracle authentication rejected")
        assert kwargs["dsn"] == "192.0.2.92:1521/contract"
        return connection

    monkeypatch.setattr(oracle_info.oracledb, "connect", connect_boundary)
    collector = oracle_info.OracleInfo(
        {
            "host": "192.0.2.92",
            "port": 1521,
            "user": "contract-user",
            "password": "contract-password",
            "service_name": "contract",
        }
    )

    result = collector.list_all_resources()

    if scenario == "authentication_or_protocol_error":
        assert result["success"] is False
        assert "Oracle authentication rejected" in result["result"][
            "cmdb_collect_error"
        ]
        return

    assert result["success"] is True
    record = result["result"]["oracle"][0]
    if scenario in ("empty", "missing_optional_field"):
        assert record["database_role"] == ""
    if scenario == "multi_record_not_applicable":
        assert "fetchone()" in DB_MULTI_RECORD_NA
        assert cursor.fetchall_called is False
    normalized, prometheus, lines = _run_publish_pipeline(
        "oracle", result, monkeypatch
    )
    assert normalized["oracle"][0]["port"] == 1521
    if scenario == "empty":
        assert normalized["oracle"][0]["max_mem"] == "0"
        assert 'max_mem="0"' in prometheus
        assert "max_mem=0" in lines[0]
    assert "oracle_info" in prometheus
    assert len(lines) == 1


class _Oid:
    def __init__(self, value):
        self.value = value

    def prettyPrint(self):
        return self.value


class _SnmpValue:
    def __init__(self, value):
        self.value = value
        self._value = str(value).encode()

    def prettyPrint(self):
        return str(self.value)


def _system_binds(*, missing_optional=False):
    binds = [
        (_Oid("1.3.6.1.2.1.1.1.0"), _SnmpValue("contract-switch")),
        (_Oid("1.3.6.1.2.1.1.5.0"), _SnmpValue("switch.example.invalid")),
    ]
    if not missing_optional:
        binds.append((_Oid("1.3.6.1.2.1.1.6.0"), _SnmpValue("")))
    return binds


def _interface_row(index):
    return [
        (_Oid(f"1.3.6.1.2.1.2.2.1.1.{index}"), _SnmpValue(str(index))),
        (_Oid(f"1.3.6.1.2.1.2.2.1.2.{index}"), _SnmpValue(f"eth{index}")),
        (_Oid(f"1.3.6.1.2.1.2.2.1.7.{index}"), _SnmpValue("1")),
    ]


@pytest.mark.parametrize(
    "scenario",
    (
        "normal_non_empty",
        "empty",
        "missing_optional_field",
        "multi_record",
        "authentication_or_protocol_error",
    ),
)
def test_Network只MockSNMP命令边界并运行真实父collector五态(
    scenario, monkeypatch
):
    from plugins.inputs.network import snmp_facts

    class BoundaryCommandGenerator:
        def getCmd(self, *args, **kwargs):
            if scenario == "authentication_or_protocol_error":
                return ("SNMP authentication rejected", None, None, [])
            if scenario == "empty":
                return (None, None, None, [])
            return (
                None,
                None,
                None,
                _system_binds(missing_optional=scenario == "missing_optional_field"),
            )

        def nextCmd(self, *args, **kwargs):
            if scenario == "empty":
                return (None, None, None, [])
            rows = [_interface_row(1)]
            if scenario == "multi_record":
                rows.append(_interface_row(2))
            return (None, None, None, rows)

    monkeypatch.setattr(snmp_facts.socket, "gethostbyname", lambda host: host)
    monkeypatch.setattr(
        snmp_facts.cmdgen, "CommandGenerator", BoundaryCommandGenerator
    )
    collector = snmp_facts.SnmpFacts(
        {
            "host": "192.0.2.93",
            "version": "v2c",
            "community": "contract-community",
        }
    )

    result = collector.list_all_resources()

    if scenario == "authentication_or_protocol_error":
        assert result["success"] is False
        assert "SNMP authentication rejected" in result["result"][
            "cmdb_collect_error"
        ]
        return

    assert result["success"] is True
    assert len(result["result"]["network_interfaces"]) == (
        2 if scenario == "multi_record" else (0 if scenario == "empty" else 1)
    )
    if scenario == "missing_optional_field":
        assert "syslocation" not in result["result"]["network_system"][0]
    normalized, prometheus, lines = _run_publish_pipeline(
        "network", result, monkeypatch
    )
    assert "network_system" in normalized
    assert "network_system_info" in prometheus
    if scenario == "empty":
        assert "network_interfaces_info" in prometheus
        assert len(lines) == 2
    else:
        assert len(lines) == 1 + len(result["result"]["network_interfaces"])
    if scenario == "normal_non_empty":
        assert 'syslocation=""' in prometheus
        assert all("syslocation=" not in line for line in lines)
