import importlib
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import semantics
import yaml
from conftest import PRODUCTION_ADAPTER_BINDINGS, confirm_real_collector_execution
from plugins import base_utils, script_executor
from plugins.base_utils import convert_to_prometheus_format
from service.collection_service import CollectionService
from tasks.utils.nats_helper import convert_prometheus_to_influx


def _collector_config(binding):
    plugin_config = yaml.safe_load(binding.plugin_path.read_text(encoding="utf-8"))
    default_executor = plugin_config["default_executor"]
    executor_config = plugin_config["executors"][default_executor]
    collector_config = executor_config["collector"]
    module = importlib.import_module(collector_config["module"])
    return executor_config, getattr(module, collector_config["class"])


def _default_script(executor_config):
    default_script = executor_config["default_script"]
    return executor_config["scripts"][default_script]


def _marked_binding_parameters(bindings):
    return tuple(
        pytest.param(
            binding,
            marks=pytest.mark.real_collector_binding(binding.case_id),
            id=binding.case_id,
        )
        for binding in bindings
    )


def _assert_real_result_reaches_publish(binding, result, expected_source_models):
    assert result["success"] is True
    assert set(result["result"]) == set(expected_source_models)
    executed_contracts = {
        (binding.task_type, binding.supported_model_id, emitted_model_id)
        for emitted_model_id in binding.emitted_model_ids
        if binding.source_model_id(emitted_model_id) in result["result"]
    }
    assert executed_contracts == binding.contracts

    service = CollectionService(
        {
            "plugin_name": f"{binding.adapter_dir}_info",
            "model_id": binding.adapter_dir,
            "host": None if binding.task_type == "cloud" else "192.0.2.100",
        }
    )
    normalized = service._process_result(deepcopy(result))
    prometheus_text = convert_to_prometheus_format(normalized)
    samples = list(semantics.parse_prometheus(prometheus_text).elements())
    assert {sample.metric_name for sample in samples} == {
        f"{model_id}_info" for model_id in expected_source_models
    }

    lines = convert_prometheus_to_influx(prometheus_text, binding.publish_params)
    records = list(semantics.parse_line_protocol(lines).elements())
    assert {record.measurement for record in records} == {
        f"{model_id}_info" for model_id in expected_source_models
    }
    for sample in samples:
        identity_labels = {key: value for key, value in sample.labels if value != ""}
        matches = [
            record
            for record in records
            if record.measurement == sample.metric_name
            and all(
                dict(record.tags).get(key) == value
                for key, value in identity_labels.items()
            )
        ]
        assert len(matches) == 1
        assert matches[0].timestamp_ns == sample.timestamp_ms * 1_000_000
    confirm_real_collector_execution(binding.case_id)


GENERIC_SSH_BINDINGS = tuple(
    binding
    for binding in PRODUCTION_ADAPTER_BINDINGS
    if _collector_config(binding)[1].__name__ == "SSHPlugin"
)


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", _marked_binding_parameters(GENERIC_SSH_BINDINGS))
async def test_通用SSH生产collector在NATS外边界执行并发布(binding, monkeypatch):
    executor_config, collector_class = _collector_config(binding)

    async def nats_boundary(subject, payload, timeout):
        decoded = json.loads(payload.decode())
        assert subject == "ssh.execute.node-contract"
        assert decoded["args"][0]["command"]
        assert decoded["args"][0]["connection_test"] is True
        assert timeout > 0
        return {
            "success": True,
            "result": json.dumps(
                [
                    {
                        "inst_name": f"{binding.supported_model_id}-contract",
                        "contract_zero": 0,
                        "contract_false": False,
                    }
                ]
            ),
        }

    monkeypatch.setattr(script_executor, "nats_request", nats_boundary)
    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)
    collector = collector_class(
        {
            "node_id": "node-contract",
            "host": "192.0.2.100",
            "script_path": _default_script(executor_config),
            "model_id": binding.source_model_ids[0],
        }
    )

    result = await collector.list_all_resources()

    _assert_real_result_reaches_publish(
        binding, result, binding.execution_source_model_ids
    )


@pytest.mark.asyncio
@pytest.mark.real_collector_binding("host-host")
async def test_HostInfo生产collector拆分主机与进程并发布(monkeypatch):
    binding = next(
        item
        for item in PRODUCTION_ADAPTER_BINDINGS
        if (item.task_type, item.supported_model_id) == ("host", "host")
    )
    executor_config, collector_class = _collector_config(binding)

    async def nats_boundary(subject, payload, timeout):
        return {
            "success": True,
            "result": json.dumps(
                [
                    {
                        "inst_name": "host-contract",
                        "proc": [{"inst_name": "python", "pid": 101},],
                    }
                ]
            ),
        }

    monkeypatch.setattr(script_executor, "nats_request", nats_boundary)
    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)
    collector = collector_class(
        {
            "node_id": "node-contract",
            "host": "192.0.2.100",
            "script_path": _default_script(executor_config),
            "model_id": "host",
        }
    )

    result = await collector.list_all_resources()

    assert result["result"]["host_proc_usage"][0]["self_device"] == "192.0.2.100"
    _assert_real_result_reaches_publish(binding, result, binding.source_model_ids)


PHYSICAL_SERVER_BINDINGS = tuple(
    item
    for item in PRODUCTION_ADAPTER_BINDINGS
    if item.supported_model_id == "physcial_server"
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "binding", _marked_binding_parameters(PHYSICAL_SERVER_BINDINGS)
)
async def test_PhyscialServerInfo生产collector拆分全部子对象并发布(binding, monkeypatch):
    executor_config, collector_class = _collector_config(binding)
    shell_output = """
=== system_info ===
inst_name=physical-contract
serial_number=SN-001
=== disk_info ===
disk_name=sda
disk_size=100
=== mem_info ===
mem_locator=DIMM-A1
mem_size=16
=== NIC info ===
nic_pci_addr=0000:01:00.0
nic_name=eth0
=== GPU info ===
gpu_name=GPU-0
gpu_memory=24
""".strip()

    async def nats_boundary(subject, payload, timeout):
        return {"success": True, "result": shell_output}

    monkeypatch.setattr(script_executor, "nats_request", nats_boundary)
    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)
    collector = collector_class(
        {
            "node_id": "node-contract",
            "host": "192.0.2.100",
            "script_path": _default_script(executor_config),
            "model_id": "physcial_server",
        }
    )

    result = await collector.list_all_resources()

    _assert_real_result_reaches_publish(
        binding, result, binding.execution_source_model_ids
    )


@pytest.mark.real_collector_binding("cloud-qcloud")
def test_QCloud父collector执行全部资源方法并发布(monkeypatch):
    from plugins.inputs.qcloud import qcloud_info

    binding = next(
        item
        for item in PRODUCTION_ADAPTER_BINDINGS
        if (item.task_type, item.supported_model_id) == ("cloud", "qcloud")
    )

    sdk_calls = []

    def common_client_boundary(self, action, params):
        sdk_calls.append(("CommonClient", action))
        return {"Response": {}}

    def cos_client_boundary(self):
        sdk_calls.append(("CosS3Client", "list_buckets"))
        return {"Buckets": {"Bucket": []}}

    monkeypatch.setattr(qcloud_info.CommonClient, "call_json", common_client_boundary)
    monkeypatch.setattr(qcloud_info.CosS3Client, "list_buckets", cos_client_boundary)
    manager = qcloud_info.TencentCloudManager(
        {"secret_id": "contract-id", "secret_key": "contract-key"}
    )
    manager.__dict__["available_region_list"] = ["ap-shanghai"]
    manager.__dict__["zone_id_zone_map"] = {}
    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)

    result = manager.list_all_resources()

    assert set(sdk_calls) >= {
        ("CommonClient", "DescribeInstances"),
        ("CommonClient", "DescribeRocketMQClusters"),
        ("CommonClient", "DescribeDBInstances"),
        ("CommonClient", "DescribeClusters"),
        ("CommonClient", "DescribeQueueDetail"),
        ("CommonClient", "DescribeTopicDetail"),
        ("CommonClient", "DescribeLoadBalancers"),
        ("CommonClient", "DescribeAddresses"),
        ("CommonClient", "DescribeCfsFileSystems"),
        ("CommonClient", "DescribeDomainNameList"),
        ("CosS3Client", "list_buckets"),
    }
    _assert_real_result_reaches_publish(binding, result, binding.source_model_ids)


@pytest.mark.real_collector_binding("cloud-aliyun_account")
def test_Aliyun父collector在官方SDK空响应边界执行全部资源并发布(monkeypatch):
    from plugins.inputs.aliyun import aliyun_info

    binding = next(
        item
        for item in PRODUCTION_ADAPTER_BINDINGS
        if (item.task_type, item.supported_model_id) == ("cloud", "aliyun_account")
    )

    monkeypatch.setattr(
        aliyun_info.client.AcsClient,
        "do_action_with_exception",
        lambda self, request: json.dumps(
            {"TotalCount": 0, "Instances": {"Instance": []}}
        ).encode(),
    )

    def sdk_response(body):
        return SimpleNamespace(body=body)

    monkeypatch.setattr(
        aliyun_info.Oss20190517Client,
        "list_buckets_with_options",
        lambda self, request, headers, runtime: sdk_response({"buckets": []}),
    )
    monkeypatch.setattr(
        aliyun_info.Rds20140815Client,
        "describe_dbinstances_with_options",
        lambda self, request, runtime: sdk_response({"Items": {"DBInstance": []}}),
    )
    monkeypatch.setattr(
        aliyun_info.R_kvstore20150101Client,
        "describe_instances_with_options",
        lambda self, request, runtime: sdk_response(
            {"Instances": {"KVStoreInstance": []}}
        ),
    )
    monkeypatch.setattr(
        aliyun_info.Dds20151201Client,
        "describe_dbinstances_with_options",
        lambda self, request, runtime: sdk_response(
            {"DBInstances": {"DBInstance": []}}
        ),
    )
    monkeypatch.setattr(
        aliyun_info.alikafka20190916Client,
        "get_instance_list_with_options",
        lambda self, request, runtime: sdk_response(
            {"InstanceList": {"InstanceVO": []}}
        ),
    )
    monkeypatch.setattr(
        aliyun_info.Slb20140515Client,
        "describe_load_balancers_with_options",
        lambda self, request, runtime: sdk_response(
            {"TotalCount": 0, "LoadBalancers": {"LoadBalancer": []}}
        ),
    )
    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)
    manager = aliyun_info.CwAliyun(
        {"secret_id": "contract-id", "secret_key": "contract-key"}
    )

    result = manager.list_all_resources()

    _assert_real_result_reaches_publish(binding, result, binding.source_model_ids)


@pytest.mark.real_collector_binding("cloud-hwcloud")
def test_HuaweiCloud父collector执行全部资源方法并发布(monkeypatch):
    from common.cmp.cloud_apis.resource_apis import cw_huaweicloud
    from common.cmp.driver import CMPDriver
    from plugins.inputs.hwcloud.huaweicloud_info import HuaweiCloudManager

    binding = next(
        item
        for item in PRODUCTION_ADAPTER_BINDINGS
        if (item.task_type, item.supported_model_id) == ("cloud", "hwcloud")
    )

    sdk_calls = []

    class FakeSdkResponse:
        status_code = 200

        def __init__(self, data):
            self._data = data

        def to_dict(self):
            return self._data

    def sdk_boundary(client_name, method_name, data):
        def call(self, request):
            sdk_calls.append((client_name, method_name))
            return FakeSdkResponse(data)

        return call

    sdk_methods = (
        (
            cw_huaweicloud.EcsClient,
            "list_servers_details",
            {"count": 0, "servers": []},
        ),
        (cw_huaweicloud.EvsClient, "list_volumes", {"count": 0, "volumes": []},),
        (cw_huaweicloud.VpcClient, "list_vpcs", {"vpcs": []}),
        (cw_huaweicloud.VpcClient, "list_subnets", {"subnets": []}),
        (cw_huaweicloud.EipClient, "list_publicips", {"publicips": []}),
        (cw_huaweicloud.VpcClient, "list_security_groups", {"security_groups": []},),
        (cw_huaweicloud.ElbClient, "list_load_balancers", {"loadbalancers": []},),
        (cw_huaweicloud.RdsClient, "list_instances", {"instances": []}),
        (cw_huaweicloud.DcsClient, "list_instances", {"instances": []}),
    )
    for client_class, method_name, data in sdk_methods:
        monkeypatch.setattr(
            client_class,
            method_name,
            sdk_boundary(client_class.__name__, method_name, data),
        )

    def obs_boundary(self):
        sdk_calls.append(("ObsClient", "listBuckets"))
        return {"body": {"buckets": []}}

    monkeypatch.setattr(cw_huaweicloud.ObsClient, "listBuckets", obs_boundary)

    manager = HuaweiCloudManager(
        {
            "accessKey": "contract-id",
            "accessSecret": "contract-key",
            "region": "cn-south-1",
            "project_id": "project-001",
            "host": "hwcloud.contract.invalid",
        }
    )
    assert isinstance(manager._driver(), CMPDriver)
    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)

    result = manager.list_all_resources()

    assert sdk_calls == [
        ("EcsClient", "list_servers_details"),
        ("EvsClient", "list_volumes"),
        ("ObsClient", "listBuckets"),
        ("VpcClient", "list_vpcs"),
        ("VpcClient", "list_subnets"),
        ("EipClient", "list_publicips"),
        ("VpcClient", "list_security_groups"),
        ("ElbClient", "list_load_balancers"),
        ("RdsClient", "list_instances"),
        ("DcsClient", "list_instances"),
    ]
    _assert_real_result_reaches_publish(binding, result, binding.source_model_ids)


@pytest.mark.real_collector_binding("cloud-fusioninsight")
def test_FusionInsight父collector在HTTP边界执行全部资源并发布(monkeypatch):
    from plugins.inputs.fusioninsight import fusioninsight_info

    binding = next(
        item
        for item in PRODUCTION_ADAPTER_BINDINGS
        if (item.task_type, item.supported_model_id) == ("cloud", "fusioninsight")
    )

    class FakeResponse:
        status_code = 200
        content = b""

        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

    class FakeSession:
        def request(self, method, url, **kwargs):
            if url.endswith("/clusters"):
                return FakeResponse([{"id": 1, "name": "cluster-contract"}])
            if url.endswith("/hosts"):
                return FakeResponse(
                    {
                        "hosts": [
                            {
                                "hostname": "host-contract",
                                "ip": "192.0.2.110",
                                "clusterId": 1,
                            }
                        ]
                    }
                )
            return FakeResponse({})

    monkeypatch.setattr(fusioninsight_info.requests, "Session", FakeSession)
    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)
    manager = fusioninsight_info.FusionInsightManager(
        {
            "username": "contract-user",
            "password": "contract-password",
            "host": "fusioninsight.contract.invalid",
        }
    )

    result = manager.list_all_resources()

    _assert_real_result_reaches_publish(binding, result, binding.source_model_ids)


@pytest.mark.real_collector_binding("cloud-storage")
def test_OceanStor父collector在HTTP边界执行全部资源并发布(monkeypatch):
    from plugins.inputs.oceanstor import oceanstor_info

    binding = next(
        item
        for item in PRODUCTION_ADAPTER_BINDINGS
        if (item.task_type, item.supported_model_id) == ("cloud", "storage")
    )

    class FakeResponse:
        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

    monkeypatch.setattr(
        oceanstor_info.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(
            {"data": {"iBaseToken": "token-contract", "deviceid": "device-001"}}
        ),
    )
    monkeypatch.setattr(
        oceanstor_info.requests,
        "get",
        lambda *args, **kwargs: FakeResponse({"error": {"code": 0}, "data": []}),
    )
    monkeypatch.setattr(
        oceanstor_info.requests, "delete", Mock(return_value=FakeResponse({})),
    )
    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)
    manager = oceanstor_info.OceanStorManager(
        {
            "username": "contract-user",
            "password": "contract-password",
            "host": "oceanstor.contract.invalid",
        }
    )

    result = manager.list_all_resources()

    _assert_real_result_reaches_publish(binding, result, binding.source_model_ids)


@pytest.mark.real_collector_binding("protocol-mysql")
def test_Mysql生产collector在PyMySQL边界执行并发布(monkeypatch):
    from plugins.inputs.mysql import mysql_info

    binding = next(
        item
        for item in PRODUCTION_ADAPTER_BINDINGS
        if (item.task_type, item.supported_model_id) == ("protocol", "mysql")
    )

    class FakeCursor:
        def __init__(self):
            self.query = ""

        def execute(self, query):
            self.query = query

        def fetchall(self):
            if self.query == "SHOW GLOBAL VARIABLES":
                return [
                    {"Variable_name": "version", "Value": "8.0.36"},
                    {"Variable_name": "server_uuid", "Value": "mysql-uuid"},
                ]
            return []

        def close(self):
            return None

    cursor = FakeCursor()
    connection = SimpleNamespace(cursor=lambda: cursor, close=lambda: None)
    monkeypatch.setattr(mysql_info.pymysql, "connect", lambda **kwargs: connection)
    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)
    collector = mysql_info.MysqlInfo(
        {
            "host": "192.0.2.120",
            "port": 3306,
            "user": "contract-user",
            "password": "contract-password",
        }
    )

    result = collector.list_all_resources()

    _assert_real_result_reaches_publish(binding, result, binding.source_model_ids)


POSTGRESQL_BINDINGS = tuple(
    item
    for item in PRODUCTION_ADAPTER_BINDINGS
    if item.supported_model_id == "postgresql"
)


@pytest.mark.parametrize("binding", _marked_binding_parameters(POSTGRESQL_BINDINGS))
def test_Postgresql生产collector在Psycopg边界执行并发布(binding, monkeypatch):
    from plugins.inputs.postgresql import postgresql_info

    values = {
        "SHOW server_version": "16.2",
        "SHOW config_file": "/etc/postgresql.conf",
        "SHOW data_directory": "/var/lib/postgresql",
        "SHOW max_connections": "100",
        "SHOW shared_buffers": "128MB",
        "SHOW log_directory": "log",
    }

    class FakeCursor:
        def __init__(self):
            self.query = ""

        def execute(self, query):
            self.query = query

        def fetchall(self):
            return [{"value": values[self.query]}]

        def close(self):
            return None

    cursor = FakeCursor()
    connection = SimpleNamespace(
        cursor=lambda cursor_factory=None: cursor, close=lambda: None
    )
    monkeypatch.setattr(
        postgresql_info.psycopg2, "connect", lambda **kwargs: connection
    )
    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)
    collector = postgresql_info.PostgresqlInfo(
        {
            "host": "192.0.2.121",
            "port": 5432,
            "user": "contract-user",
            "password": "contract-password",
        }
    )

    result = collector.list_all_resources()

    _assert_real_result_reaches_publish(binding, result, binding.source_model_ids)


@pytest.mark.real_collector_binding("protocol-oracle")
def test_Oracle生产collector在OracleDB边界执行并发布(monkeypatch):
    from plugins.inputs.oracle import oracle_info

    binding = next(
        item
        for item in PRODUCTION_ADAPTER_BINDINGS
        if (item.task_type, item.supported_model_id) == ("protocol", "oracle")
    )
    responses = {
        oracle_info.OracleInfo.SQL_QUERIES["version"]: ("BANNER", "Oracle 19c"),
        oracle_info.OracleInfo.SQL_QUERIES["max_mem"]: ("TOTAL_MEMORY", 1024),
        oracle_info.OracleInfo.SQL_QUERIES["max_conn"]: ("VALUE", 100),
        oracle_info.OracleInfo.SQL_QUERIES["db_name"]: ("NAME", "ORCL"),
        oracle_info.OracleInfo.SQL_QUERIES["database_role"]: (
            "DATABASE_ROLE",
            "PRIMARY",
        ),
        oracle_info.OracleInfo.SQL_QUERIES["sid"]: ("SID", "ORCL1"),
    }

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, query):
            key, value = responses[query]
            self.description = [(key,)]
            self.row = (value,)

        def fetchone(self):
            return self.row

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return FakeCursor()

    monkeypatch.setattr(
        oracle_info.oracledb, "connect", lambda **kwargs: FakeConnection()
    )
    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)
    collector = oracle_info.OracleInfo(
        {
            "host": "192.0.2.122",
            "port": 1521,
            "user": "contract-user",
            "password": "contract-password",
        }
    )

    result = collector.list_all_resources()

    _assert_real_result_reaches_publish(binding, result, binding.source_model_ids)


@pytest.mark.real_collector_binding("protocol-mssql")
def test_MSSQL生产collector在PyODBC边界执行并发布(monkeypatch):
    from plugins.inputs.mssql import mssql_info

    binding = next(
        item
        for item in PRODUCTION_ADAPTER_BINDINGS
        if (item.task_type, item.supported_model_id) == ("protocol", "mssql")
    )
    query_values = {
        query: (field, value)
        for field, query, value in (
            ("version", mssql_info.MSSQLInfo.SQL_QUERIES["version"], "16.0"),
            ("max_conn", mssql_info.MSSQLInfo.SQL_QUERIES["max_conn"], 100),
            ("max_mem_mb", mssql_info.MSSQLInfo.SQL_QUERIES["max_mem"], 2048),
            (
                "order_rule",
                mssql_info.MSSQLInfo.SQL_QUERIES["order_rule"],
                "Latin1_General_CI_AS",
            ),
            ("fill_factor", mssql_info.MSSQLInfo.SQL_QUERIES["fill_factor"], 90),
            (
                "boot_account",
                mssql_info.MSSQLInfo.SQL_QUERIES["boot_account"],
                "LocalSystem",
            ),
        )
    }

    class FakeCursor:
        def execute(self, query):
            field, value = query_values[query]
            self.description = [(field,)]
            self.row = (value,)

        def fetchone(self):
            return self.row

        def close(self):
            return None

    cursor = FakeCursor()
    connection = SimpleNamespace(cursor=lambda: cursor, close=lambda: None)
    monkeypatch.setattr(
        mssql_info.pyodbc, "connect", lambda connection_string, timeout: connection
    )
    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)
    collector = mssql_info.MSSQLInfo(
        {
            "host": "192.0.2.123",
            "port": 1433,
            "user": "contract-user",
            "password": "contract-password",
            "database": "master",
        }
    )

    result = collector.list_all_resources()

    _assert_real_result_reaches_publish(binding, result, binding.source_model_ids)


@pytest.mark.real_collector_binding("protocol-influxdb")
def test_InfluxDB生产collector在HTTP边界执行并发布(monkeypatch):
    from plugins.inputs.influxdb import influxdb_info

    binding = next(
        item
        for item in PRODUCTION_ADAPTER_BINDINGS
        if (item.task_type, item.supported_model_id) == ("protocol", "influxdb")
    )

    class FakeResponse:
        status_code = 200
        headers = {}

        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    def http_get(url, **kwargs):
        if url.endswith("/health"):
            return FakeResponse({"version": "2.7.5"})
        return FakeResponse(
            {
                "config": {
                    "engine-path": "/var/lib/influxdb2/engine",
                    "bolt-path": "/var/lib/influxdb2/influxd.bolt",
                    "storage-engine": "tsm1",
                }
            }
        )

    monkeypatch.setattr(influxdb_info.requests, "get", http_get)
    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)
    collector = influxdb_info.InfluxdbInfo(
        {"host": "192.0.2.124", "port": 8086, "token": "contract-token"}
    )

    result = collector.list_all_resources()

    _assert_real_result_reaches_publish(binding, result, binding.source_model_ids)


@pytest.mark.asyncio
@pytest.mark.real_collector_binding("ip-ip")
async def test_IP生产collector在ICMP边界执行并发布(monkeypatch):
    import subprocess

    import icmplib
    from plugins.inputs.ip.ip_discovery_scanner import IPDiscoveryScanner

    binding = next(
        item
        for item in PRODUCTION_ADAPTER_BINDINGS
        if (item.task_type, item.supported_model_id) == ("ip", "ip")
    )

    async def async_ping(ip, count, timeout, privileged):
        return SimpleNamespace(is_alive=True)

    monkeypatch.setattr(icmplib, "async_ping", async_ping)
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=""),
    )
    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)
    collector = IPDiscoveryScanner(
        {"model_id": "ip", "targets": ["192.0.2.125"], "timeout": 0.1}
    )

    result = await collector.list_all_resources()

    _assert_real_result_reaches_publish(binding, result, binding.source_model_ids)


@pytest.mark.real_collector_binding("snmp-network")
def test_SNMP生产collector在CommandGenerator边界执行并发布(monkeypatch):
    from plugins.inputs.network import snmp_facts

    binding = next(
        item
        for item in PRODUCTION_ADAPTER_BINDINGS
        if (item.task_type, item.supported_model_id) == ("snmp", "network")
    )

    class FakeOid:
        def __init__(self, value):
            self.value = value

        def prettyPrint(self):
            return self.value

    class FakeValue:
        def __init__(self, value):
            self.value = value
            self._value = str(value).encode()

        def prettyPrint(self):
            return str(self.value)

    class FakeCommandGenerator:
        def getCmd(self, *args, **kwargs):
            return (
                None,
                None,
                None,
                [
                    (FakeOid("1.3.6.1.2.1.1.1.0"), FakeValue("network-device")),
                    (FakeOid("1.3.6.1.2.1.1.5.0"), FakeValue("switch-01")),
                ],
            )

        def nextCmd(self, *args, **kwargs):
            return (
                None,
                None,
                None,
                [
                    [
                        (FakeOid("1.3.6.1.2.1.2.2.1.1.1"), FakeValue("1")),
                        (FakeOid("1.3.6.1.2.1.2.2.1.2.1"), FakeValue("eth0")),
                    ]
                ],
            )

    monkeypatch.setattr(snmp_facts.socket, "gethostbyname", lambda host: host)
    monkeypatch.setattr(snmp_facts.cmdgen, "CommandGenerator", FakeCommandGenerator)
    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)
    collector = snmp_facts.SnmpFacts(
        {"host": "192.0.2.126", "version": "v2c", "community": "contract-community",}
    )

    result = collector.list_all_resources()

    _assert_real_result_reaches_publish(
        binding, result, binding.execution_source_model_ids
    )


@pytest.mark.real_collector_binding("vm-vmware_vc")
def test_VMware生产collector在SmartConnect边界执行并发布(monkeypatch):
    from plugins.inputs.vmware_vc import vmware_info

    binding = next(
        item
        for item in PRODUCTION_ADAPTER_BINDINGS
        if (item.task_type, item.supported_model_id) == ("vm", "vmware_vc")
    )

    class FakeViewManager:
        def CreateContainerView(self, root, object_types, recursive):
            return SimpleNamespace(view=[])

    content = SimpleNamespace(
        about=SimpleNamespace(name="vCenter-contract", version="8.0"),
        rootFolder=object(),
        viewManager=FakeViewManager(),
    )
    service_instance = SimpleNamespace(RetrieveContent=lambda: content)
    monkeypatch.setattr(vmware_info, "SmartConnect", lambda **kwargs: service_instance)
    monkeypatch.setattr(vmware_info, "Disconnect", lambda si: None)
    monkeypatch.setattr(base_utils.time, "time", lambda: 1_700_000_000.123)
    collector = vmware_info.VmwareManage(
        {
            "host": "vcenter.contract.invalid",
            "port": 443,
            "username": "contract-user",
            "password": "contract-password",
        }
    )

    result = collector.list_all_resources()

    _assert_real_result_reaches_publish(
        binding, result, binding.execution_source_model_ids
    )
