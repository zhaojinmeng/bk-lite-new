import json
import sys
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

STARGAZER_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = STARGAZER_ROOT.parents[1]
CONTRACT_MANIFEST_PATH = (
    REPOSITORY_ROOT
    / "server"
    / "apps"
    / "cmdb"
    / "tests"
    / "e2e"
    / "contract_manifest.json"
)
if str(STARGAZER_ROOT) not in sys.path:
    sys.path.insert(0, str(STARGAZER_ROOT))

FIXED_TIMESTAMP_MS = 1_700_000_000_123


@dataclass(frozen=True)
class ProductionAdapterBinding:
    task_type: str
    supported_model_id: str
    adapter_dir: str
    emitted_model_ids: tuple[str, ...]
    source_model_aliases: tuple[tuple[str, str], ...] = ()
    collector_import_exemption_reason: str | None = None

    @property
    def case_id(self) -> str:
        return f"{self.task_type}-{self.supported_model_id}"

    @property
    def plugin_path(self) -> Path:
        return STARGAZER_ROOT / "plugins" / "inputs" / self.adapter_dir / "plugin.yml"

    @property
    def contracts(self) -> set[tuple[str, str, str]]:
        return {
            (self.task_type, self.supported_model_id, emitted_model_id)
            for emitted_model_id in self.emitted_model_ids
        }

    def source_model_id(self, emitted_model_id: str) -> str:
        aliases = dict(self.source_model_aliases)
        return aliases.get(emitted_model_id, emitted_model_id)

    @property
    def source_model_ids(self) -> tuple[str, ...]:
        return tuple(
            self.source_model_id(emitted_model_id)
            for emitted_model_id in self.emitted_model_ids
        )

    @property
    def publish_params(self) -> dict[str, Any]:
        return {
            "monitor_type": self.supported_model_id,
            "plugin_name": f"{self.adapter_dir}_info",
            "model_id": self.supported_model_id,
            "tags": {
                "agent_id": "agent-contract",
                "instance_id": f"cmdb-{self.supported_model_id}",
                "instance_type": self.supported_model_id,
                "collect_type": "discovery",
                "config_type": "production-contract",
            },
        }

    @property
    def source_raw(self) -> dict[str, Any]:
        return {
            "success": True,
            "result": {
                source_model_id: [
                    {
                        "inst_name": f"{source_model_id}-contract",
                        "resource_id": f"{source_model_id}-001",
                        "contract_zero": 0,
                        "contract_false": False,
                        "contract_empty": "",
                        "contract_none": None,
                        "contract_nested": {"ignored": True},
                    }
                ]
                for source_model_id in self.source_model_ids
            },
        }

    def run_real_normalizer(self) -> dict[str, Any]:
        from service.collection_service import CollectionService

        params = {
            "plugin_name": f"{self.adapter_dir}_info",
            "model_id": self.adapter_dir,
            "host": None if self.task_type == "cloud" else "192.0.2.100",
        }
        service = CollectionService(deepcopy(params))
        return service._process_result(deepcopy(self.source_raw))


# 这是独立于服务端 manifest 的显式生产绑定清单。新增生产三元组时必须在这里声明
# 真实 Stargazer 插件目录、最终模型集合，以及原始模型别名；不能由 manifest 反向生成。
PRODUCTION_ADAPTER_BINDINGS = (
    ProductionAdapterBinding(
        "cloud",
        "aliyun_account",
        "aliyun",
        (
            "aliyun_bucket",
            "aliyun_clb",
            "aliyun_ecs",
            "aliyun_kafka_inst",
            "aliyun_mongodb",
            "aliyun_mysql",
            "aliyun_pgsql",
            "aliyun_redis",
        ),
        collector_import_exemption_reason="测试环境未安装阿里云可选SDK，改验真实源码类定义",
    ),
    ProductionAdapterBinding(
        "cloud",
        "fusioninsight",
        "fusioninsight",
        ("fusioninsight_cluster", "fusioninsight_host"),
    ),
    ProductionAdapterBinding(
        "cloud",
        "hwcloud",
        "hwcloud",
        (
            "hwcloud",
            "hwcloud_dcs",
            "hwcloud_ecs",
            "hwcloud_eip",
            "hwcloud_elb",
            "hwcloud_evs",
            "hwcloud_obs",
            "hwcloud_rds",
            "hwcloud_sg",
            "hwcloud_subnet",
            "hwcloud_vpc",
        ),
    ),
    ProductionAdapterBinding(
        "cloud",
        "qcloud",
        "qcloud",
        (
            "qcloud_bucket",
            "qcloud_clb",
            "qcloud_cmq",
            "qcloud_cmq_topic",
            "qcloud_cvm",
            "qcloud_domain",
            "qcloud_eip",
            "qcloud_filesystem",
            "qcloud_mongodb",
            "qcloud_mysql",
            "qcloud_pgsql",
            "qcloud_plusar_cluster",
            "qcloud_redis",
            "qcloud_rocketmq",
        ),
        (("qcloud_plusar_cluster", "qcloud_pulsar_cluster"),),
        collector_import_exemption_reason="测试环境未安装腾讯云可选SDK，改验真实源码类定义",
    ),
    ProductionAdapterBinding(
        "cloud",
        "storage",
        "oceanstor",
        ("storage", "storage_disk", "storage_pool", "storage_volume"),
    ),
    ProductionAdapterBinding("db", "es", "es", ("es",)),
    ProductionAdapterBinding("db", "hbase", "hbase", ("hbase",)),
    ProductionAdapterBinding("db", "mongodb", "mongodb", ("mongodb",)),
    ProductionAdapterBinding("db", "postgresql", "postgresql", ("postgresql",)),
    ProductionAdapterBinding("db", "redis", "redis", ("redis",)),
    ProductionAdapterBinding("host", "host", "host", ("host", "host_proc_usage")),
    ProductionAdapterBinding(
        "host",
        "physcial_server",
        "physcial_server",
        ("disk", "gpu", "memory", "nic", "physcial_server"),
    ),
    ProductionAdapterBinding("ip", "ip", "ip", ("ip",)),
    ProductionAdapterBinding("middleware", "activemq", "activemq", ("activemq",)),
    ProductionAdapterBinding("middleware", "apache", "apache", ("apache",)),
    ProductionAdapterBinding("middleware", "consul", "consul", ("consul",)),
    ProductionAdapterBinding("middleware", "docker", "docker", ("docker",)),
    ProductionAdapterBinding("middleware", "etcd", "etcd", ("etcd",)),
    ProductionAdapterBinding("middleware", "haproxy", "haproxy", ("haproxy",)),
    ProductionAdapterBinding("middleware", "iis", "iis", ("iis",)),
    ProductionAdapterBinding("middleware", "kafka", "kafka", ("kafka",)),
    ProductionAdapterBinding("middleware", "keepalive", "keepalived", ("keepalived",)),
    ProductionAdapterBinding("middleware", "memcached", "memcached", ("memcached",)),
    ProductionAdapterBinding("middleware", "minio", "minio", ("minio",)),
    ProductionAdapterBinding("middleware", "nginx", "nginx", ("nginx",)),
    ProductionAdapterBinding("middleware", "openresty", "openresty", ("openresty",)),
    ProductionAdapterBinding("middleware", "rabbitmq", "rabbitmq", ("rabbitmq",)),
    ProductionAdapterBinding("middleware", "rocketmq", "rocketmq", ("rocketmq",)),
    ProductionAdapterBinding("middleware", "spark", "spark", ("spark",)),
    ProductionAdapterBinding("middleware", "squid", "squid", ("squid",)),
    ProductionAdapterBinding("middleware", "tomcat", "tomcat", ("tomcat",)),
    ProductionAdapterBinding("middleware", "zookeeper", "zookeeper", ("zookeeper",)),
    ProductionAdapterBinding("protocol", "influxdb", "influxdb", ("influxdb",)),
    ProductionAdapterBinding("protocol", "mssql", "mssql", ("mssql",)),
    ProductionAdapterBinding("protocol", "mysql", "mysql", ("mysql",)),
    ProductionAdapterBinding("protocol", "oracle", "oracle", ("oracle",)),
    ProductionAdapterBinding(
        "protocol", "physcial_server", "physcial_server", ("physcial_server",)
    ),
    ProductionAdapterBinding("protocol", "postgresql", "postgresql", ("postgresql",)),
    ProductionAdapterBinding(
        "snmp",
        "network",
        "network",
        ("network",),
        collector_import_exemption_reason="测试环境未安装pysnmp可选SDK，改验真实源码类定义",
    ),
    ProductionAdapterBinding(
        "vm",
        "vmware_vc",
        "vmware_vc",
        ("vmware_vc",),
        collector_import_exemption_reason="测试环境未安装pyVmomi可选SDK，改验真实源码类定义",
    ),
)


def validation_contracts() -> set[tuple[str, str, str]]:
    manifest = json.loads(CONTRACT_MANIFEST_PATH.read_text(encoding="utf-8"))
    return {
        (entry["task_type"], entry["supported_model_id"], entry["emitted_model_id"],)
        for entry in manifest["validation_contracts"]
    }


def covered_lane_a_contracts() -> set[tuple[str, str, str]]:
    return {
        contract
        for binding in PRODUCTION_ADAPTER_BINDINGS
        for contract in binding.contracts
    }


def lane_a_coverage_failures() -> list[str]:
    expected = validation_contracts()
    covered = covered_lane_a_contracts()
    failures = [
        f"{contract}: 未声明真实Stargazer adapter binding/formatter/publish参数"
        for contract in sorted(expected - covered)
    ]
    failures.extend(
        f"{contract}: binding不属于当前可测试生产manifest，必须归档或补manifest"
        for contract in sorted(covered - expected)
    )
    return failures


@dataclass(frozen=True)
class LaneAEvidence:
    source_raw: dict[str, Any]
    prometheus_text: str
    line_protocol_text: str
    expected_record_count: int


@dataclass(frozen=True)
class RepresentativeLaneACase:
    case_id: str
    model_id: str
    host: str
    publish_params: dict[str, Any]

    def run_real_adapter(self, source_raw: dict[str, Any]) -> dict[str, Any]:
        from service.collection_service import CollectionService

        service = CollectionService(
            {
                "plugin_name": f"{self.model_id}_info",
                "model_id": self.model_id,
                "host": self.host,
            }
        )
        return service._process_result(deepcopy(source_raw))


def representative_lane_a_cases():
    host_params = {
        "monitor_type": "host",
        "plugin_name": "host_info",
        "model_id": "host",
        "tags": {
            "agent_id": "agent-contract",
            "instance_id": "cmdb_1001",
            "instance_type": "host",
            "collect_type": "discovery",
            "config_type": "job",
        },
    }
    host_case = RepresentativeLaneACase(
        case_id="host-real-normalizer-single-row",
        model_id="host",
        host="192.0.2.10",
        publish_params=host_params,
    )
    host_evidence = LaneAEvidence(
        source_raw={
            "success": True,
            "result": {
                "host": [
                    {"inst_name": "node-01", "cpu_num": 4, "serial": 'SN "A", rack'}
                ]
            },
        },
        prometheus_text=(
            "# HELP host_info independently recorded host evidence\n"
            "# TYPE host_info gauge\n"
            'host_info{serial="SN \\"A\\", rack",inst_name="node-01",'
            'host="192.0.2.10",cpu_num="4",collect_status="success",'
            'bk_obj_id="host",model_id="host"} '
            f"1 {FIXED_TIMESTAMP_MS}\n"
        ),
        line_protocol_text=(
            'host_info,serial=SN\\ \\"A\\"\\,\\ rack,inst_name=node-01,'
            "host=192.0.2.10,cpu_num=4,collect_status=success,bk_obj_id=host,"
            "model_id=host,agent_id=agent-contract,instance_id=cmdb_1001,"
            "instance_type=host,collect_type=discovery,config_type=job "
            f"gauge=1i {FIXED_TIMESTAMP_MS * 1_000_000}\n"
        ),
        expected_record_count=1,
    )

    mysql_params = {
        "monitor_type": "mysql",
        "plugin_name": "mysql_info",
        "model_id": "mysql",
        "tags": {
            "agent_id": "agent-contract",
            "instance_id": "cmdb_1002",
            "instance_type": "mysql",
            "collect_type": "discovery",
            "config_type": "job",
        },
    }
    mysql_case = RepresentativeLaneACase(
        case_id="mysql-real-normalizer-two-rows",
        model_id="mysql",
        host="192.0.2.20",
        publish_params=mysql_params,
    )
    mysql_evidence = LaneAEvidence(
        source_raw={
            "success": True,
            "result": {
                "mysql": [
                    {"inst_name": "mysql-a", "port": 3306},
                    {"inst_name": "mysql-b", "port": 3307},
                ]
            },
        },
        prometheus_text=(
            "# TYPE mysql_info gauge\n"
            'mysql_info{port="3307",model_id="mysql",inst_name="mysql-b",'
            'host="192.0.2.20",collect_status="success",bk_obj_id="mysql"} '
            f"1 {FIXED_TIMESTAMP_MS}\n"
            'mysql_info{port="3306",model_id="mysql",inst_name="mysql-a",'
            'host="192.0.2.20",collect_status="success",bk_obj_id="mysql"} '
            f"1 {FIXED_TIMESTAMP_MS}\n"
        ),
        line_protocol_text=(
            "mysql_info,port=3307,model_id=mysql,inst_name=mysql-b,host=192.0.2.20,"
            "collect_status=success,bk_obj_id=mysql,agent_id=agent-contract,"
            "instance_id=cmdb_1002,instance_type=mysql,collect_type=discovery,"
            f"config_type=job gauge=1i {FIXED_TIMESTAMP_MS * 1_000_000}\n"
            "mysql_info,port=3306,model_id=mysql,inst_name=mysql-a,host=192.0.2.20,"
            "collect_status=success,bk_obj_id=mysql,agent_id=agent-contract,"
            "instance_id=cmdb_1002,instance_type=mysql,collect_type=discovery,"
            f"config_type=job gauge=1i {FIXED_TIMESTAMP_MS * 1_000_000}\n"
        ),
        expected_record_count=2,
    )
    return ((host_case, host_evidence), (mysql_case, mysql_evidence))


@pytest.fixture(params=representative_lane_a_cases(), ids=lambda item: item[0].case_id)
def representative_lane_a_case(request):
    return request.param


@pytest.fixture(
    params=PRODUCTION_ADAPTER_BINDINGS, ids=lambda binding: binding.case_id,
)
def production_adapter_binding(request):
    return request.param
