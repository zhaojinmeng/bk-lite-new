"""云采集 SDK/API 边界合同。

官方依据：
- 腾讯云 CVM DescribeInstances，API 2017-03-12：
  https://cloud.tencent.com/document/product/213/15728
- 阿里云 ECS DescribeInstances，API 2014-05-26：
  https://help.aliyun.com/en/ecs/developer-reference/api-ecs-2014-05-26-describeinstances
- 华为云 ECS ListServersDetails：
  https://support.huaweicloud.com/intl/en-us/api-ecs/ecs-api-pdf.pdf
"""

import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

STARGAZER_ROOT = Path(__file__).resolve().parents[2]
if str(STARGAZER_ROOT) not in sys.path:
    sys.path.insert(0, str(STARGAZER_ROOT))

from aliyunsdkcore.request import CommonRequest  # noqa: E402
from common.cmp.cloud_apis.constant import CloudType  # noqa: E402
from common.cmp.cloud_apis.resource_apis import cw_huaweicloud  # noqa: E402
from plugins.inputs.aliyun.aliyun_info import Aliyun  # noqa: E402
from plugins.inputs.hwcloud.huaweicloud_info import HuaweiCloudManager  # noqa: E402
from plugins.inputs.qcloud import qcloud_info  # noqa: E402
from plugins.inputs.qcloud.qcloud_info import TencentCloudManager  # noqa: E402
from tencentcloud.common.exception import tencent_cloud_sdk_exception  # noqa: E402

QCLOUD_SCENARIO_MATRIX = json.loads(
    (Path(__file__).with_name("qcloud_operation_scenarios.json")).read_text(
        encoding="utf-8"
    )
)
ALIYUN_SCENARIO_MATRIX = json.loads(
    (Path(__file__).with_name("aliyun_operation_scenarios.json")).read_text(
        encoding="utf-8"
    )
)
HWCLOUD_SCENARIO_MATRIX = json.loads(
    (Path(__file__).with_name("hwcloud_operation_scenarios.json")).read_text(
        encoding="utf-8"
    )
)
QCLOUD_SCENARIOS = {
    "single_page",
    "pagination",
    "empty",
    "missing_optional_field",
    "documented_error",
}
HWCLOUD_SCENARIOS = QCLOUD_SCENARIOS
QCLOUD_EVIDENCE_ROOT = (
    STARGAZER_ROOT.parents[1]
    / "server"
    / "apps"
    / "cmdb"
    / "tests"
    / "e2e"
    / "fixtures"
)


@pytest.fixture(autouse=True)
def 禁止测试访问真实网络(monkeypatch):
    def fail_network(*args, **kwargs):
        raise AssertionError("云源合同测试禁止访问真实网络")

    class NetworkForbiddenSocket(socket.socket):
        def connect(self, *args, **kwargs):
            fail_network()

        def connect_ex(self, *args, **kwargs):
            fail_network()

    monkeypatch.setattr(socket, "socket", NetworkForbiddenSocket)
    monkeypatch.setattr(socket, "create_connection", fail_network)


def _qcloud_manager(monkeypatch, call_json):
    manager = TencentCloudManager(
        {"secret_id": "contract-id", "secret_key": "contract-key"}
    )
    manager.__dict__["available_region_list"] = ["ap-shanghai"]
    manager.__dict__["zone_id_zone_map"] = {}

    def sdk_boundary(self, action, params):
        return call_json(action, params)

    monkeypatch.setattr(qcloud_info.CommonClient, "call_json", sdk_boundary)
    return manager


def _qcloud_cvm(instance):
    return {"Response": {"TotalCount": 1, "InstanceSet": [instance]}}


def test_腾讯云十四项operation显式声明五态与官方来源():
    operations = QCLOUD_SCENARIO_MATRIX["operations"]

    assert {item["case_id"] for item in operations} == {
        "qcloud_bucket",
        "qcloud_clb",
        "qcloud_cmq",
        "qcloud_cmq_topic",
        "qcloud_cvm",
        "qcloud_domain",
        "qcloud_eip",
        "qcloud_filesystem",
        "qcloud_mysql",
        "qcloud_redis",
        "qcloud_mongodb",
        "qcloud_pgsql",
        "qcloud_plusar_cluster",
        "qcloud_rocketmq",
    }
    for operation in operations:
        assert set(operation["scenarios"]) == QCLOUD_SCENARIOS
        assert operation["documentation_url"].startswith(
            ("https://cloud.tencent.com/", "https://intl.cloud.tencent.com/")
        )
        pagination = operation["pagination"]
        assert pagination["kind"] in {"offset_limit", "not_applicable"}
        if pagination["kind"] == "not_applicable":
            assert pagination["reason"]
            assert pagination["documentation_url"] == operation["documentation_url"]


@pytest.mark.parametrize(
    "operation",
    QCLOUD_SCENARIO_MATRIX["operations"],
    ids=lambda operation: operation["case_id"],
)
def test_腾讯云逐case_provenance与operation矩阵严格一致(operation):
    case_id = operation["case_id"]
    evidence = QCLOUD_EVIDENCE_ROOT / case_id
    provenance = json.loads(
        (evidence / "00_provenance.json").read_text(encoding="utf-8")
    )
    source = json.loads((evidence / "01_source_raw.json").read_text(encoding="utf-8"))

    assert {
        key: provenance[key]
        for key in (
            "vendor",
            "service",
            "api_operation",
            "api_or_sdk_version",
            "documentation_url",
            "read_at",
        )
    } == {
        "vendor": QCLOUD_SCENARIO_MATRIX["vendor"],
        "service": operation["service"],
        "api_operation": operation["api_operation"],
        "api_or_sdk_version": operation["api_or_sdk_version"],
        "documentation_url": operation["documentation_url"],
        "read_at": QCLOUD_SCENARIO_MATRIX["read_at"],
    }
    assert provenance["emitted_case_id"] == case_id
    assert set(source["result"]) == {provenance["source_model_id"]}


_QCLOUD_FIRST_BATCH = {
    "qcloud_cvm": {
        "method": "get_qcloud_cvm",
        "operation": "DescribeInstances",
        "collection": "InstanceSet",
        "minimal": {"InstanceName": "cvm-contract", "InstanceId": "ins-001"},
        "id": "ins-001",
    },
    "qcloud_mysql": {
        "method": "get_qcloud_mysql",
        "operation": "DescribeDBInstances",
        "collection": "Items",
        "minimal": {"InstanceName": "mysql-contract", "InstanceId": "cdb-001"},
        "id": "cdb-001",
    },
    "qcloud_redis": {
        "method": "get_qcloud_redis",
        "operation": "DescribeInstances",
        "collection": "InstanceSet",
        "minimal": {"InstanceName": "redis-contract", "InstanceId": "crs-001"},
        "id": "crs-001",
    },
    "qcloud_mongodb": {
        "method": "get_qcloud_mongodb",
        "operation": "DescribeDBInstances",
        "collection": "InstanceDetails",
        "minimal": {"InstanceName": "mongo-contract", "InstanceId": "cmgo-001"},
        "id": "cmgo-001",
    },
    "qcloud_pgsql": {
        "method": "get_qcloud_pgsql",
        "operation": "DescribeDBInstances",
        "collection": "DBInstanceSet",
        "minimal": {
            "DBInstanceName": "pgsql-contract",
            "DBInstanceId": "postgres-001",
            "DBInstanceMemory": 0,
            "DBInstanceStorage": 0,
        },
        "id": "postgres-001",
    },
    "qcloud_plusar_cluster": {
        "method": "get_qcloud_pulsar_cluster",
        "operation": "DescribeClusters",
        "collection": "ClusterSet",
        "minimal": {"ClusterName": "pulsar-contract", "ClusterId": "pulsar-001"},
        "id": "pulsar-001",
    },
}


def _qcloud_page(spec, items, *, total_count):
    return {"Response": {"TotalCount": total_count, spec["collection"]: items,}}


@pytest.mark.parametrize("case_id", tuple(_QCLOUD_FIRST_BATCH))
def test_腾讯云首批列表API按官方OffsetLimit完整翻页(case_id, monkeypatch):
    spec = _QCLOUD_FIRST_BATCH[case_id]
    first = dict(spec["minimal"])
    second = dict(spec["minimal"])
    second_id = f"{spec['id']}-page-2"
    if case_id == "qcloud_pgsql":
        second["DBInstanceId"] = second_id
    else:
        second[
            {
                "qcloud_cvm": "InstanceId",
                "qcloud_mysql": "InstanceId",
                "qcloud_redis": "InstanceId",
                "qcloud_mongodb": "InstanceId",
                "qcloud_plusar_cluster": "ClusterId",
            }[case_id]
        ] = second_id
    sdk_call = Mock(
        side_effect=[
            _qcloud_page(spec, [first], total_count=101),
            _qcloud_page(spec, [second], total_count=101),
        ]
    )
    manager = _qcloud_manager(monkeypatch, sdk_call)

    result = getattr(manager, spec["method"])()

    assert [item["resource_id"] for item in result] == [spec["id"], second_id]
    assert sdk_call.call_args_list == [
        call(spec["operation"], {"Limit": 100, "Offset": 0}),
        call(spec["operation"], {"Limit": 100, "Offset": 100}),
    ]


@pytest.mark.parametrize("case_id", tuple(_QCLOUD_FIRST_BATCH))
def test_腾讯云首批列表API空集稳定返回空列表(case_id, monkeypatch):
    spec = _QCLOUD_FIRST_BATCH[case_id]
    manager = _qcloud_manager(
        monkeypatch, Mock(return_value=_qcloud_page(spec, [], total_count=0))
    )

    assert getattr(manager, spec["method"])() == []


@pytest.mark.parametrize("case_id", tuple(_QCLOUD_FIRST_BATCH))
def test_腾讯云首批列表API缺可选字段仍保留资源身份(case_id, monkeypatch):
    spec = _QCLOUD_FIRST_BATCH[case_id]
    manager = _qcloud_manager(
        monkeypatch,
        Mock(return_value=_qcloud_page(spec, [spec["minimal"]], total_count=1)),
    )

    result = getattr(manager, spec["method"])()

    assert result[0]["resource_id"] == spec["id"]


@pytest.mark.parametrize("case_id", tuple(_QCLOUD_FIRST_BATCH))
def test_腾讯云首批列表API文档化鉴权错误不伪装为空集(case_id, monkeypatch):
    spec = _QCLOUD_FIRST_BATCH[case_id]
    sdk_error = tencent_cloud_sdk_exception.TencentCloudSDKException(
        "AuthFailure.SignatureFailure", "signature invalid", "request-contract"
    )
    manager = _qcloud_manager(monkeypatch, Mock(side_effect=sdk_error))

    with pytest.raises(
        tencent_cloud_sdk_exception.TencentCloudSDKException
    ) as exc_info:
        getattr(manager, spec["method"])()

    assert exc_info.value.code == "AuthFailure.SignatureFailure"


_QCLOUD_SECOND_BATCH = {
    "qcloud_rocketmq": {
        "method": "get_qcloud_rocketmq",
        "operation": "DescribeRocketMQClusters",
        "collection": "ClusterList",
        "minimal": {
            "Info": {
                "ClusterName": "rocketmq-contract",
                "ClusterId": "rocketmq-001",
                "ZoneId": "200001",
            },
            "Config": {},
        },
        "id_key": ("Info", "ClusterId"),
        "id": "rocketmq-001",
    },
    "qcloud_cmq": {
        "method": "get_qcloud_cmq",
        "operation": "DescribeQueueDetail",
        "collection": "QueueSet",
        "minimal": {"QueueName": "queue-contract", "QueueId": "queue-001"},
        "id_key": ("QueueId",),
        "id": "queue-001",
    },
    "qcloud_cmq_topic": {
        "method": "get_qcloud_cmq_topic",
        "operation": "DescribeTopicDetail",
        "collection": "TopicSet",
        "minimal": {"TopicName": "topic-contract", "TopicId": "topic-001"},
        "id_key": ("TopicId",),
        "id": "topic-001",
    },
    "qcloud_clb": {
        "method": "get_qcloud_clb",
        "operation": "DescribeLoadBalancers",
        "collection": "LoadBalancerSet",
        "minimal": {"LoadBalancerName": "clb-contract", "LoadBalancerId": "lb-001"},
        "id_key": ("LoadBalancerId",),
        "id": "lb-001",
    },
    "qcloud_eip": {
        "method": "get_qcloud_eip",
        "operation": "DescribeAddresses",
        "collection": "AddressSet",
        "minimal": {"AddressName": "eip-contract", "AddressId": "eip-001"},
        "id_key": ("AddressId",),
        "id": "eip-001",
    },
    "qcloud_filesystem": {
        "method": "get_qcloud_filesystem",
        "operation": "DescribeCfsFileSystems",
        "collection": "FileSystems",
        "minimal": {"FsName": "cfs-contract", "FileSystemId": "cfs-001"},
        "id_key": ("FileSystemId",),
        "id": "cfs-001",
    },
    "qcloud_domain": {
        "method": "get_qcloud_domain",
        "operation": "DescribeDomainNameList",
        "collection": "DomainSet",
        "minimal": {"DomainName": "example.invalid", "DomainId": "domain-001"},
        "id_key": ("DomainId",),
        "id": "domain-001",
    },
}


def _replace_nested_id(item, key_path, value):
    cursor = item
    for key in key_path[:-1]:
        cursor = cursor[key]
    cursor[key_path[-1]] = value


def _prepare_second_batch_manager(case_id, monkeypatch, sdk_call):
    manager = _qcloud_manager(monkeypatch, sdk_call)
    if case_id in {"qcloud_cmq", "qcloud_cmq_topic"}:
        monkeypatch.setitem(
            qcloud_info.product_available_region_list_map, "cmq", ["ap-shanghai"],
        )
        monkeypatch.setattr(qcloud_info.time, "sleep", lambda _: None)
    return manager


def _second_batch_page(spec, items, total_count):
    return {"Response": {spec["collection"]: items, "TotalCount": total_count,}}


@pytest.mark.parametrize("case_id", tuple(_QCLOUD_SECOND_BATCH))
def test_腾讯云第二批列表API单页缺可选字段和空集合同(case_id, monkeypatch):
    spec = _QCLOUD_SECOND_BATCH[case_id]
    single_page_responses = [_second_batch_page(spec, [spec["minimal"]], 1)]
    if case_id == "qcloud_rocketmq":
        single_page_responses.append(_second_batch_page(spec, [], 1))
    manager = _prepare_second_batch_manager(
        case_id, monkeypatch, Mock(side_effect=single_page_responses)
    )

    actual = getattr(manager, spec["method"])()
    assert actual[0]["resource_id"] == spec["id"]

    manager = _prepare_second_batch_manager(
        case_id, monkeypatch, Mock(return_value=_second_batch_page(spec, [], 0)),
    )
    assert getattr(manager, spec["method"])() == []


@pytest.mark.parametrize(
    "case_id",
    (
        "qcloud_cmq",
        "qcloud_cmq_topic",
        "qcloud_clb",
        "qcloud_eip",
        "qcloud_filesystem",
        "qcloud_domain",
    ),
)
def test_腾讯云第二批OffsetLimit列表完整翻页(case_id, monkeypatch):
    spec = _QCLOUD_SECOND_BATCH[case_id]
    first = json.loads(json.dumps(spec["minimal"]))
    second = json.loads(json.dumps(spec["minimal"]))
    second_id = f"{spec['id']}-page-2"
    _replace_nested_id(second, spec["id_key"], second_id)
    limit = 50 if case_id in {"qcloud_cmq", "qcloud_cmq_topic"} else 100
    sdk_call = Mock(
        side_effect=[
            _second_batch_page(spec, [first], limit + 1),
            _second_batch_page(spec, [second], limit + 1),
        ]
    )
    manager = _prepare_second_batch_manager(case_id, monkeypatch, sdk_call)

    actual = getattr(manager, spec["method"])()

    assert [item["resource_id"] for item in actual] == [spec["id"], second_id]
    assert sdk_call.call_args_list == [
        call(spec["operation"], {"Limit": limit, "Offset": 0}),
        call(spec["operation"], {"Limit": limit, "Offset": limit}),
    ]


@pytest.mark.parametrize(
    "case_id",
    (
        "qcloud_rocketmq",
        "qcloud_clb",
        "qcloud_eip",
        "qcloud_filesystem",
        "qcloud_domain",
    ),
)
def test_腾讯云第二批文档化鉴权错误不伪装为空集(case_id, monkeypatch):
    spec = _QCLOUD_SECOND_BATCH[case_id]
    sdk_error = tencent_cloud_sdk_exception.TencentCloudSDKException(
        "AuthFailure.SignatureFailure", "signature invalid", "request-contract"
    )
    manager = _prepare_second_batch_manager(
        case_id, monkeypatch, Mock(side_effect=sdk_error)
    )

    with pytest.raises(
        tencent_cloud_sdk_exception.TencentCloudSDKException
    ) as exc_info:
        getattr(manager, spec["method"])()

    assert exc_info.value.code == "AuthFailure.SignatureFailure"


def test_腾讯云COS分页场景有官方not_applicable依据():
    operation = next(
        item
        for item in QCLOUD_SCENARIO_MATRIX["operations"]
        if item["case_id"] == "qcloud_bucket"
    )

    assert operation["pagination"] == {
        "kind": "not_applicable",
        "documentation_url": operation["documentation_url"],
        "reason": (
            "GET Service/list_buckets returns the complete bucket list for the "
            "account and documents no pagination parameter."
        ),
    }


def test_腾讯云COS在官方SDK边界覆盖单页空集缺可选字段和错误(monkeypatch):
    manager = TencentCloudManager(
        {"secret_id": "contract-id", "secret_key": "contract-key"}
    )
    manager.__dict__["available_region_list"] = ["ap-shanghai"]
    sdk_call = Mock(
        side_effect=[
            {
                "Buckets": {
                    "Bucket": [{"Name": "bucket-contract", "Location": "ap-shanghai"}]
                }
            },
            {"Buckets": {"Bucket": []}},
            {"Buckets": {"Bucket": [{"Name": "bucket-minimal"}]}},
            RuntimeError("AccessDenied"),
        ]
    )
    monkeypatch.setattr(
        manager,
        "get_tencent_cos_client",
        lambda region: type(
            "CosSdkBoundary", (), {"list_buckets": lambda self: sdk_call()}
        )(),
    )

    assert manager.get_qcloud_bucket() == [
        {
            "resource_name": "bucket-contract",
            "resource_id": "bucket-contract",
            "region": "ap-shanghai",
        }
    ]
    assert manager.get_qcloud_bucket() == []
    assert manager.get_qcloud_bucket()[0]["resource_id"] == "bucket-minimal"
    with pytest.raises(RuntimeError, match="AccessDenied"):
        manager.get_qcloud_bucket()


def test_腾讯云单页响应映射为CVM对象(monkeypatch):
    sdk_call = Mock(
        return_value=_qcloud_cvm(
            {
                "InstanceName": "cvm-contract",
                "InstanceId": "ins-001",
                "PrivateIpAddresses": ["10.0.0.8"],
                "PublicIpAddresses": ["203.0.113.8"],
                "Placement": {"Zone": "ap-shanghai-1"},
                "VirtualPrivateCloud": {"VpcId": "vpc-001"},
                "InstanceState": "RUNNING",
                "InstanceType": "S5.MEDIUM4",
                "OsName": "Linux",
                "CPU": 2,
                "Memory": 4,
                "InstanceChargeType": "POSTPAID_BY_HOUR",
            }
        )
    )
    manager = _qcloud_manager(monkeypatch, sdk_call)

    result = manager.get_qcloud_cvm()

    assert result == [
        {
            "resource_name": "cvm-contract",
            "resource_id": "ins-001",
            "ip_addr": "10.0.0.8",
            "public_ip": "203.0.113.8",
            "region": "ap-shanghai",
            "zone": "ap-shanghai-1",
            "vpc": "vpc-001",
            "status": "RUNNING",
            "instance_type": "S5.MEDIUM4",
            "os_name": "Linux",
            "vcpus": 2,
            "memory_mb": 4096,
            "charge_type": "POSTPAID_BY_HOUR",
        }
    ]
    sdk_call.assert_called_once_with("DescribeInstances", {"Limit": 100, "Offset": 0})


def test_腾讯云空集返回空列表(monkeypatch):
    sdk_call = Mock(return_value={"Response": {"TotalCount": 0, "InstanceSet": []}})
    manager = _qcloud_manager(monkeypatch, sdk_call)

    assert manager.get_qcloud_cvm() == []


def test_腾讯云缺少可选字段时使用空值或零值(monkeypatch):
    sdk_call = Mock(
        return_value=_qcloud_cvm(
            {"InstanceName": "minimal", "InstanceId": "ins-minimal"}
        )
    )
    manager = _qcloud_manager(monkeypatch, sdk_call)

    result = manager.get_qcloud_cvm()[0]

    assert result["ip_addr"] == ""
    assert result["public_ip"] == ""
    assert result["zone"] is None
    assert result["vpc"] is None
    assert result["memory_mb"] == 0


def test_腾讯云RocketMQ按LimitOffset翻页至空页(monkeypatch):
    cluster = lambda cluster_id: {
        "Info": {
            "ClusterName": f"cluster-{cluster_id}",
            "ClusterId": cluster_id,
            "ZoneId": "200001",
        },
        "Status": 1,
        "Config": {
            "MaxTopicNum": 10,
            "UsedTopicNum": 1,
            "MaxTpsLimit": 100,
            "MaxNamespaceNum": 5,
            "UsedNamespaceNum": 1,
            "MaxGroupNum": 10,
            "UsedGroupNum": 2,
        },
    }
    sdk_call = Mock(
        side_effect=[
            {"Response": {"ClusterList": [cluster("rocket-001")]}},
            {"Response": {"ClusterList": [cluster("rocket-002")]}},
            {"Response": {"ClusterList": []}},
        ]
    )
    manager = _qcloud_manager(monkeypatch, sdk_call)
    manager.__dict__["zone_id_zone_map"] = {"200001": "ap-shanghai-1"}

    result = manager.get_qcloud_rocketmq()

    assert [item["resource_id"] for item in result] == ["rocket-001", "rocket-002"]
    assert sdk_call.call_args_list == [
        call("DescribeRocketMQClusters", {"Limit": 100, "Offset": 0}),
        call("DescribeRocketMQClusters", {"Limit": 100, "Offset": 100}),
        call("DescribeRocketMQClusters", {"Limit": 100, "Offset": 200}),
    ]


def test_腾讯云文档化鉴权错误原样上抛(monkeypatch):
    sdk_error = tencent_cloud_sdk_exception.TencentCloudSDKException(
        "AuthFailure.SignatureFailure", "signature invalid", "request-001"
    )
    manager = _qcloud_manager(monkeypatch, Mock(side_effect=sdk_error))

    with pytest.raises(
        tencent_cloud_sdk_exception.TencentCloudSDKException
    ) as exc_info:
        manager.get_qcloud_cvm()

    assert exc_info.value.code == "AuthFailure.SignatureFailure"


def test_腾讯云RocketMQ文档化不支持地域错误只跳过该地域(monkeypatch):
    sdk_error = tencent_cloud_sdk_exception.TencentCloudSDKException(
        "UnsupportedRegion", "region is unsupported", "request-002"
    )
    manager = _qcloud_manager(monkeypatch, Mock(side_effect=sdk_error))

    assert manager.get_qcloud_rocketmq() == []


def _aliyun_instance(instance_id):
    return {
        "InstanceId": instance_id,
        "InstanceName": f"ecs-{instance_id}",
        "SecurityGroupIds": {"SecurityGroupId": []},
        "EipAddress": {"IpAddress": ""},
        "PublicIpAddress": {"IpAddress": []},
        "NetworkInterfaces": {"NetworkInterface": []},
        "VpcAttributes": {
            "PrivateIpAddress": {"IpAddress": ["10.0.0.9"]},
            "VpcId": "vpc-aliyun",
            "VSwitchId": "vsw-aliyun",
        },
        "Status": "Running",
        "Cpu": 2,
        "Memory": 4096,
        "InstanceType": "ecs.g7.large",
        "ImageId": "img-001",
        "OSName": "Linux",
        "InstanceChargeType": "PostPaid",
        "ZoneId": "cn-hangzhou-h",
        "RegionId": "cn-hangzhou",
        "CreationTime": "2026-01-01T00:00Z",
        "ExpiredTime": "",
        "Tags": {},
    }


def test_阿里云PageNumber分页只请求实际页数(monkeypatch):
    collector = object.__new__(Aliyun)
    collector.RegionId = "cn-hangzhou"
    collector.cloud_type = CloudType.ALIYUN.value
    sdk_call = Mock(
        side_effect=[
            {
                "TotalCount": 51,
                "Instances": {"Instance": [_aliyun_instance("i-page-1")]},
            },
            {
                "TotalCount": 51,
                "Instances": {"Instance": [_aliyun_instance("i-page-2")]},
            },
            {"TotalCount": 51, "Instances": {"Instance": []}},
        ]
    )
    requested_pages = []

    def sdk_boundary(request):
        requested_pages.append(request.get_PageNumber())
        return json.dumps(sdk_call()).encode()

    collector.client = type(
        "SdkClientBoundary",
        (),
        {"do_action_with_exception": lambda self, request: sdk_boundary(request)},
    )()

    result = collector.list_vms()

    assert result["result"] is True
    assert [item["resource_id"] for item in result["data"]] == [
        "i-page-1",
        "i-page-2",
    ]
    assert sdk_call.call_count == 2
    assert requested_pages == [1, "2"]


def test_阿里云CommonRequest分页只请求实际页数(monkeypatch):
    collector = object.__new__(Aliyun)
    collector.RegionId = "cn-hangzhou"
    collector.cloud_type = CloudType.ALIYUN.value
    sdk_call = Mock(
        side_effect=[
            {
                "TotalCount": 51,
                "Instances": {"Instance": [_aliyun_instance("i-common-1")]},
            },
            {
                "TotalCount": 51,
                "Instances": {"Instance": [_aliyun_instance("i-common-2")]},
            },
            {"TotalCount": 51, "Instances": {"Instance": []}},
        ]
    )
    requested_pages = []

    def sdk_boundary(request):
        requested_pages.append(request.get_query_params()["PageNumber"])
        return json.dumps(sdk_call()).encode()

    collector.client = type(
        "SdkClientBoundary",
        (),
        {"do_action": lambda self, request: sdk_boundary(request)},
    )()

    result = collector._handle_list_request_with_page_c("vm", CommonRequest())

    assert result["result"] is True
    assert [item["resource_id"] for item in result["data"]] == [
        "i-common-1",
        "i-common-2",
    ]
    assert sdk_call.call_count == 2
    assert requested_pages == [1, "2"]


ALIYUN_SCENARIOS = {
    "single_page",
    "pagination",
    "empty",
    "missing_optional_field",
    "documented_error",
}


def test_阿里云八项case由七类官方operation显式声明五态():
    operations = ALIYUN_SCENARIO_MATRIX["operations"]

    assert {case_id for item in operations for case_id in item["case_ids"]} == {
        "aliyun_bucket",
        "aliyun_clb",
        "aliyun_ecs",
        "aliyun_kafka_inst",
        "aliyun_mongodb",
        "aliyun_mysql",
        "aliyun_pgsql",
        "aliyun_redis",
    }
    assert len(operations) == 7
    for operation in operations:
        assert set(operation["scenarios"]) == ALIYUN_SCENARIOS
        assert operation["documentation_url"].startswith(
            ("https://help.aliyun.com/", "https://www.alibabacloud.com/")
        )
        if operation["pagination"]["kind"] == "not_applicable":
            assert (
                operation["pagination"]["documentation_url"]
                == operation["documentation_url"]
            )
            assert operation["pagination"]["reason"]


@pytest.mark.parametrize(
    ("case_id", "operation"),
    tuple(
        (case_id, operation)
        for operation in ALIYUN_SCENARIO_MATRIX["operations"]
        for case_id in operation["case_ids"]
    ),
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_阿里云逐case_provenance与operation矩阵严格一致(case_id, operation):
    evidence = QCLOUD_EVIDENCE_ROOT / case_id
    provenance = json.loads(
        (evidence / "00_provenance.json").read_text(encoding="utf-8")
    )
    source = json.loads((evidence / "01_source_raw.json").read_text(encoding="utf-8"))

    assert {
        key: provenance[key]
        for key in (
            "vendor",
            "service",
            "api_operation",
            "api_or_sdk_version",
            "documentation_url",
            "read_at",
        )
    } == {
        "vendor": ALIYUN_SCENARIO_MATRIX["vendor"],
        "service": operation["service"],
        "api_operation": operation["api_operation"],
        "api_or_sdk_version": operation["api_or_sdk_version"],
        "documentation_url": operation["documentation_url"],
        "read_at": ALIYUN_SCENARIO_MATRIX["read_at"],
    }
    assert provenance["emitted_case_id"] == case_id
    assert set(source["result"]) == {provenance["source_model_id"]}


def _aliyun_without_init():
    collector = object.__new__(Aliyun)
    collector.RegionId = "cn-hangzhou"
    collector.cloud_type = CloudType.ALIYUN.value
    return collector


def _sdk_response(body):
    return SimpleNamespace(body=body)


def test_阿里云ECS官方SDK边界覆盖单页空集缺可选字段和错误():
    collector = _aliyun_without_init()
    sdk_call = Mock(
        side_effect=[
            json.dumps(
                {
                    "TotalCount": 1,
                    "Instances": {"Instance": [_aliyun_instance("i-single")]},
                }
            ).encode(),
            json.dumps({"TotalCount": 0, "Instances": {"Instance": []}}).encode(),
            json.dumps(
                {
                    "TotalCount": 1,
                    "Instances": {"Instance": [_aliyun_instance("i-minimal")]},
                }
            ).encode(),
            RuntimeError("InvalidAccessKeyId.NotFound"),
        ]
    )
    collector.client = SimpleNamespace(do_action_with_exception=sdk_call)

    assert collector.list_vms()["data"][0]["resource_id"] == "i-single"
    assert collector.list_vms() == {"result": True, "data": []}
    assert collector.list_vms()["data"][0]["resource_id"] == "i-minimal"
    error = collector.list_vms()
    assert error["result"] is False
    assert "InvalidAccessKeyId.NotFound" in error["message"]


def _oss_bucket(name):
    return {
        "Name": name,
        "Location": "oss-cn-hangzhou",
        "CreationDate": "2026-01-01T00:00:00.000Z",
    }


def _oss_bucket_info(name):
    return {
        "Bucket": {
            "Name": name,
            "Location": "oss-cn-hangzhou",
            "ExtranetEndpoint": "oss-cn-hangzhou.aliyuncs.com",
            "IntranetEndpoint": "oss-cn-hangzhou-internal.aliyuncs.com",
            "StorageClass": "Standard",
            "CrossRegionReplication": "Disabled",
            "BlockPublicAccess": True,
            "CreationDate": "2026-01-01T00:00:00.000Z",
        }
    }


def test_阿里云OSS_ListBuckets按官方Marker完整翻页且逐桶GetBucketInfo(monkeypatch):
    from plugins.inputs.aliyun import aliyun_info

    monkeypatch.setattr(aliyun_info.TeaCore, "to_map", lambda body: body)
    collector = _aliyun_without_init()
    list_call = Mock(
        side_effect=[
            _sdk_response(
                {
                    "buckets": [_oss_bucket("bucket-page-1")],
                    "isTruncated": True,
                    "nextMarker": "bucket-page-1",
                }
            ),
            _sdk_response(
                {"buckets": [_oss_bucket("bucket-page-2")], "isTruncated": False,}
            ),
        ]
    )
    info_call = Mock(
        side_effect=[
            _sdk_response(_oss_bucket_info("bucket-page-1")),
            _sdk_response(_oss_bucket_info("bucket-page-2")),
        ]
    )
    collector.oss_client = SimpleNamespace(
        list_buckets_with_options=list_call, get_bucket_info_with_options=info_call,
    )

    result = collector.list_buckets()

    assert [item["Name"] for item in result["data"]] == [
        "bucket-page-1",
        "bucket-page-2",
    ]
    assert list_call.call_count == 2
    second_request = list_call.call_args_list[1].args[0]
    assert second_request.marker == "bucket-page-1"
    assert info_call.call_count == 2


def test_阿里云OSS官方SDK边界覆盖单页空集缺可选字段和错误(monkeypatch):
    from plugins.inputs.aliyun import aliyun_info

    monkeypatch.setattr(aliyun_info.TeaCore, "to_map", lambda body: body)
    collector = _aliyun_without_init()
    collector.oss_client = SimpleNamespace(
        list_buckets_with_options=Mock(
            side_effect=[
                _sdk_response({"buckets": [_oss_bucket("bucket-single")]}),
                _sdk_response({"buckets": []}),
                _sdk_response({"buckets": [_oss_bucket("bucket-minimal")]}),
                RuntimeError("AccessDenied"),
            ]
        ),
        get_bucket_info_with_options=Mock(
            side_effect=[
                _sdk_response(_oss_bucket_info("bucket-single")),
                _sdk_response({"Bucket": {"Name": "bucket-minimal"}}),
            ]
        ),
    )

    assert collector.list_buckets()["data"][0]["Name"] == "bucket-single"
    assert collector.list_buckets() == {"result": True, "data": []}
    assert collector.list_buckets()["data"][0]["Name"] == "bucket-minimal"
    error = collector.list_buckets()
    assert error["result"] is False
    assert "AccessDenied" in error["message"]


@pytest.mark.parametrize("engine", ("Mysql", "PostgreSQL"))
def test_阿里云RDS_DescribeDBInstances五态覆盖(engine, monkeypatch):
    from plugins.inputs.aliyun import aliyun_info

    monkeypatch.setattr(aliyun_info.TeaCore, "to_map", lambda body: body)
    collector = _aliyun_without_init()
    item = {
        "DBInstanceId": f"rds-{engine.lower()}-001",
        "DBInstanceDescription": f"{engine}-contract",
        "Engine": engine,
    }
    sdk_call = Mock(
        side_effect=[
            _sdk_response({"Items": {"DBInstance": [item]}}),
            _sdk_response({"Items": {"DBInstance": []}}),
            _sdk_response({"Items": {"DBInstance": []}}),
            _sdk_response({"Items": {"DBInstance": [item]}}),
            _sdk_response({"Items": {"DBInstance": []}}),
            RuntimeError("InvalidAccessKeyId.NotFound"),
        ]
    )
    collector.rds_client = SimpleNamespace(describe_dbinstances_with_options=sdk_call)

    result = collector.list_rds(engine=engine)
    assert result["data"][0]["DBInstanceId"] == item["DBInstanceId"]
    assert collector.list_rds(engine=engine) == {"result": True, "data": []}
    assert (
        collector.list_rds(engine=engine)["data"][0]["DBInstanceId"]
        == item["DBInstanceId"]
    )
    error = collector.list_rds(engine=engine)
    assert error["result"] is False
    assert "InvalidAccessKeyId.NotFound" in error["message"]


@pytest.mark.parametrize("engine", ("Mysql", "PostgreSQL"))
def test_阿里云RDS按官方PageNumber分页直到空页(engine, monkeypatch):
    from plugins.inputs.aliyun import aliyun_info

    monkeypatch.setattr(aliyun_info.TeaCore, "to_map", lambda body: body)
    collector = _aliyun_without_init()
    responses = iter(
        [
            _sdk_response({"Items": {"DBInstance": [{"DBInstanceId": "rds-page-1"}]}}),
            _sdk_response({"Items": {"DBInstance": [{"DBInstanceId": "rds-page-2"}]}}),
            _sdk_response({"Items": {"DBInstance": []}}),
        ]
    )
    requested_pages = []

    def sdk_boundary(request, runtime):
        requested_pages.append(request.page_number)
        return next(responses)

    sdk_call = Mock(side_effect=sdk_boundary)
    collector.rds_client = SimpleNamespace(describe_dbinstances_with_options=sdk_call)

    result = collector.list_rds(engine=engine)

    assert [item["DBInstanceId"] for item in result["data"]] == [
        "rds-page-1",
        "rds-page-2",
    ]
    assert requested_pages == [1, 2, 3]


@pytest.mark.parametrize(
    ("method_name", "client_attr", "sdk_method", "collection_path", "item"),
    (
        (
            "list_redis",
            "kvs_client",
            "describe_instances_with_options",
            ("Instances", "KVStoreInstance"),
            {"InstanceId": "redis-001", "InstanceName": "redis-contract"},
        ),
        (
            "list_clb",
            "slb_client",
            "describe_load_balancers_with_options",
            ("LoadBalancers", "LoadBalancer"),
            {"LoadBalancerId": "lb-001", "LoadBalancerName": "clb-contract"},
        ),
    ),
)
def test_阿里云Redis与SLB官方SDK边界覆盖单页空集缺可选字段和错误(
    method_name, client_attr, sdk_method, collection_path, item, monkeypatch,
):
    from plugins.inputs.aliyun import aliyun_info

    monkeypatch.setattr(aliyun_info.TeaCore, "to_map", lambda body: body)
    collector = _aliyun_without_init()
    first, second = collection_path
    responses = [
        _sdk_response({first: {second: [item]}, "TotalCount": 1}),
    ]
    if method_name == "list_redis":
        responses.append(_sdk_response({first: {second: []}, "TotalCount": 1}))
    responses.append(_sdk_response({first: {second: []}, "TotalCount": 0}))
    responses.append(_sdk_response({first: {second: [item]}, "TotalCount": 1}))
    if method_name == "list_redis":
        responses.append(_sdk_response({first: {second: []}, "TotalCount": 1}))
    responses.append(RuntimeError("InvalidAccessKeyId.NotFound"))
    sdk_call = Mock(side_effect=responses)
    setattr(collector, client_attr, SimpleNamespace(**{sdk_method: sdk_call}))

    id_key = "InstanceId" if method_name == "list_redis" else "LoadBalancerId"
    assert getattr(collector, method_name)()["data"][0][id_key] == item[id_key]
    assert getattr(collector, method_name)() == {"result": True, "data": []}
    assert getattr(collector, method_name)()["data"][0][id_key] == item[id_key]
    error = getattr(collector, method_name)()
    assert error["result"] is False
    assert "InvalidAccessKeyId.NotFound" in error["message"]


@pytest.mark.parametrize(
    ("method_name", "client_attr", "sdk_method", "collection_path", "id_key"),
    (
        (
            "list_redis",
            "kvs_client",
            "describe_instances_with_options",
            ("Instances", "KVStoreInstance"),
            "InstanceId",
        ),
        (
            "list_clb",
            "slb_client",
            "describe_load_balancers_with_options",
            ("LoadBalancers", "LoadBalancer"),
            "LoadBalancerId",
        ),
    ),
)
def test_阿里云Redis与SLB按官方PageNumber完整分页(
    method_name, client_attr, sdk_method, collection_path, id_key, monkeypatch,
):
    from plugins.inputs.aliyun import aliyun_info

    monkeypatch.setattr(aliyun_info.TeaCore, "to_map", lambda body: body)
    collector = _aliyun_without_init()
    first, second = collection_path
    requested_pages = []
    responses = iter(
        [
            _sdk_response({first: {second: [{id_key: "page-1"}]}, "TotalCount": 101}),
            _sdk_response({first: {second: [{id_key: "page-2"}]}, "TotalCount": 101}),
            _sdk_response({first: {second: []}, "TotalCount": 101}),
        ]
    )

    def sdk_boundary(request, runtime):
        requested_pages.append(request.page_number)
        return next(responses)

    sdk_call = Mock(side_effect=sdk_boundary)
    setattr(collector, client_attr, SimpleNamespace(**{sdk_method: sdk_call}))

    result = getattr(collector, method_name)()

    assert [item[id_key] for item in result["data"]] == ["page-1", "page-2"]
    if method_name == "list_redis":
        assert requested_pages == [1, 2, 3]
    else:
        assert requested_pages == [1, 2]


def _mongodb_item(instance_id, instance_type):
    return {
        "DBInstanceId": instance_id,
        "DBInstanceDescription": f"{instance_type}-contract",
        "DBInstanceType": instance_type,
    }


def test_阿里云DDS父operation逐一查询官方三类实例并覆盖单页空集缺字段(monkeypatch):
    from plugins.inputs.aliyun import aliyun_info

    monkeypatch.setattr(aliyun_info.TeaCore, "to_map", lambda body: body)
    collector = _aliyun_without_init()
    calls = []
    responses = iter(
        [
            _sdk_response(
                {
                    "DBInstances": {
                        "DBInstance": [_mongodb_item("dds-sharding", "sharding")]
                    }
                }
            ),
            _sdk_response({"DBInstances": {"DBInstance": []}}),
            _sdk_response(
                {
                    "DBInstances": {
                        "DBInstance": [_mongodb_item("dds-replicate", "replicate")]
                    }
                }
            ),
            _sdk_response({"DBInstances": {"DBInstance": []}}),
            _sdk_response(
                {
                    "DBInstances": {
                        "DBInstance": [_mongodb_item("dds-serverless", "serverless")]
                    }
                }
            ),
            _sdk_response({"DBInstances": {"DBInstance": []}}),
        ]
    )

    def sdk_boundary(request, runtime):
        calls.append((request.dbinstance_type, request.page_number))
        return next(responses)

    collector.dds_client = SimpleNamespace(
        describe_dbinstances_with_options=Mock(side_effect=sdk_boundary)
    )

    result = collector.list_mongodb()

    assert [item["DBInstanceId"] for item in result["data"]] == [
        "dds-sharding",
        "dds-replicate",
        "dds-serverless",
    ]
    assert calls == [
        ("sharding", 1),
        ("sharding", 2),
        ("replicate", 1),
        ("replicate", 2),
        ("serverless", 1),
        ("serverless", 2),
    ]


def test_阿里云DDS空集和文档化错误不伪装成功(monkeypatch):
    from plugins.inputs.aliyun import aliyun_info

    monkeypatch.setattr(aliyun_info.TeaCore, "to_map", lambda body: body)
    collector = _aliyun_without_init()
    collector.dds_client = SimpleNamespace(
        describe_dbinstances_with_options=Mock(
            side_effect=[
                _sdk_response({"DBInstances": {"DBInstance": []}}),
                _sdk_response({"DBInstances": {"DBInstance": []}}),
                _sdk_response({"DBInstances": {"DBInstance": []}}),
            ]
        )
    )
    assert collector.list_mongodb() == {"result": True, "data": []}

    collector.dds_client = SimpleNamespace(
        describe_dbinstances_with_options=Mock(
            side_effect=RuntimeError("InvalidAccessKeyId.NotFound")
        )
    )
    error = collector.list_mongodb()
    assert error["result"] is False
    assert "InvalidAccessKeyId.NotFound" in error["message"]


def test_阿里云Kafka_GetInstanceList覆盖单页空集缺可选字段和错误(monkeypatch):
    from plugins.inputs.aliyun import aliyun_info

    monkeypatch.setattr(aliyun_info.TeaCore, "to_map", lambda body: body)
    collector = _aliyun_without_init()
    item = {"InstanceId": "kafka-001", "Name": "kafka-contract"}
    sdk_call = Mock(
        side_effect=[
            _sdk_response({"InstanceList": {"InstanceVO": [item]}}),
            _sdk_response({"InstanceList": {"InstanceVO": []}}),
            _sdk_response({"InstanceList": {"InstanceVO": [item]}}),
            RuntimeError("InvalidAccessKeyId.NotFound"),
        ]
    )
    collector.kafka_client = SimpleNamespace(get_instance_list_with_options=sdk_call)

    assert collector.list_kafka()["data"][0]["InstanceId"] == "kafka-001"
    assert collector.list_kafka() == {"result": True, "data": []}
    assert collector.list_kafka()["data"][0]["InstanceId"] == "kafka-001"
    error = collector.list_kafka()
    assert error["result"] is False
    assert "InvalidAccessKeyId.NotFound" in error["message"]


def test_阿里云Kafka官方字段经真实父collector格式化后保留资源身份(monkeypatch):
    from plugins.inputs.aliyun import aliyun_info

    monkeypatch.setattr(aliyun_info.TeaCore, "to_map", lambda body: body)
    collector = _aliyun_without_init()
    collector.kafka_client = SimpleNamespace(
        get_instance_list_with_options=Mock(
            return_value=_sdk_response(
                {
                    "InstanceList": {
                        "InstanceVO": [
                            {
                                "Name": "kafka-contract",
                                "InstanceId": "alikafka-001",
                                "RegionId": "cn-hangzhou",
                                "ZoneId": "cn-hangzhou-h",
                                "VpcId": "vpc-001",
                                "ServiceStatus": 5,
                                "DiskSize": 3600,
                                "DiskType": 1,
                                "MsgRetain": 72,
                                "TopicNumLimit": 180,
                                "IoMaxRead": 1000,
                                "IoMaxWrite": 1000,
                                "PaidType": 1,
                                "CreateTime": 1_577_961_819_000,
                            }
                        ]
                    }
                }
            )
        )
    )

    raw = collector.list_kafka()
    formatted = collector.format_aliyun_data({"aliyun_kafka_inst": raw["data"]})

    assert formatted["aliyun_kafka_inst"][0]["resource_name"] == "kafka-contract"
    assert formatted["aliyun_kafka_inst"][0]["resource_id"] == "alikafka-001"


def test_阿里云Kafka分页场景由同一官方operation页面明确N_A():
    operation = next(
        item
        for item in ALIYUN_SCENARIO_MATRIX["operations"]
        if item["case_ids"] == ["aliyun_kafka_inst"]
    )

    assert operation["pagination"]["kind"] == "not_applicable"
    assert (
        operation["pagination"]["documentation_url"] == operation["documentation_url"]
    )
    assert "no page number" in operation["pagination"]["reason"]


def test_华为云SDK空集保持稳定(monkeypatch):
    class FakeSdkResponse:
        status_code = 200

        def to_dict(self):
            return {"count": 0, "servers": []}

    sdk_call = Mock(return_value=FakeSdkResponse())
    monkeypatch.setattr(cw_huaweicloud.EcsClient, "list_servers_details", sdk_call)
    manager = HuaweiCloudManager(
        {
            "accessKey": "contract-id",
            "accessSecret": "contract-key",
            "region": "cn-south-1",
            "project_id": "project-001",
        }
    )
    result = manager.get_ecs()

    assert result == []
    assert sdk_call.call_count == 1


def test_华为云SDK错误转换为明确异常(monkeypatch):
    def sdk_error(self, request):
        raise RuntimeError("APIGW.0101")

    monkeypatch.setattr(cw_huaweicloud.EcsClient, "list_servers_details", sdk_error)
    manager = HuaweiCloudManager(
        {
            "accessKey": "contract-id",
            "accessSecret": "contract-key",
            "project_id": "project-001",
        }
    )
    with pytest.raises(RuntimeError, match="APIGW.0101"):
        manager.get_ecs()


def _hwcloud_manager():
    return HuaweiCloudManager(
        {
            "accessKey": "contract-id",
            "accessSecret": "contract-key",
            "region": "cn-south-1",
            "project_id": "project-001",
            "host": "https://ecs.cn-south-1.example.invalid",
        }
    )


class _HuaweiSdkResponse:
    status_code = 200

    def __init__(self, data):
        self._data = data

    def to_dict(self):
        return self._data


def _ecs_server(resource_id):
    address = SimpleNamespace(
        addr="192.0.2.10",
        os_ext_ip_sport_id=f"port-{resource_id}",
        os_ext_ip_stype="fixed",
    )
    return {
        "id": resource_id,
        "name": f"ecs-{resource_id}",
        "host_id": f"host-{resource_id}",
        "description": "",
        "flavor": {"id": "s6.large.2", "vcpus": "2", "ram": "4096"},
        "image": {"id": "image-contract"},
        "metadata": {
            "image_name": "EulerOS",
            "charging_mode": "0",
            "vpc_id": "vpc-001",
        },
        "status": "ACTIVE",
        "addresses": {"contract-net": [address]},
        "os_extended_volumesvolumes_attached": [],
        "security_groups": [],
        "created": "2026-01-02T03:04:05Z",
        "os_ext_a_zavailability_zone": "cn-south-1a",
        "tags": [],
    }


def _patch_ecs_interfaces(monkeypatch):
    def list_interfaces(self, request):
        return _HuaweiSdkResponse(
            {
                "interface_attachments": [
                    {"fixed_ips": [{"subnet_id": "subnet-001"}]}
                ]
            }
        )

    monkeypatch.setattr(
        cw_huaweicloud.EcsClient, "list_server_interfaces", list_interfaces
    )


def test_华为云十一项operation显式声明五态与官方来源():
    operations = HWCLOUD_SCENARIO_MATRIX["operations"]

    assert {
        case_id for operation in operations for case_id in operation["case_ids"]
    } == {
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
    }
    for operation in operations:
        assert set(operation["scenarios"]) == HWCLOUD_SCENARIOS
        assert operation["documentation_url"].startswith(
            (
                "https://support.huaweicloud.com/",
                "https://developer.huaweicloud.com/",
            )
        )
        pagination = operation["pagination"]
        assert pagination["kind"] in {
            "offset_limit",
            "marker",
            "not_applicable",
        }
        if pagination["kind"] == "not_applicable":
            assert pagination["reason"]
            assert pagination["documentation_url"] == operation["documentation_url"]


def test_华为云平台对象来自父账户上下文且可选endpoint保持空值():
    operation = next(
        item
        for item in HWCLOUD_SCENARIO_MATRIX["operations"]
        if item["case_ids"] == ["hwcloud"]
    )
    manager = HuaweiCloudManager(
        {
            "accessKey": "contract-id",
            "accessSecret": "contract-key",
            "region": "cn-south-1",
            "project_id": "project-001",
        }
    )

    assert manager.get_platform() == [{"endpoint": ""}]
    assert operation["pagination"]["kind"] == "not_applicable"
    assert "not returned by a list API" in operation["pagination"]["reason"]


def test_华为云ECS单页与缺可选字段经真实父链路保留身份(monkeypatch):
    _patch_ecs_interfaces(monkeypatch)
    sdk_call = Mock(
        return_value=_HuaweiSdkResponse(
            {"count": 1, "servers": [_ecs_server("ecs-001")]}
        )
    )
    monkeypatch.setattr(
        cw_huaweicloud.EcsClient, "list_servers_details", sdk_call
    )

    result = _hwcloud_manager().get_ecs()

    assert result[0]["resource_id"] == "ecs-001"
    assert result[0]["os_name"] == "EulerOS"
    assert result[0]["expired_time"] == ""
    assert sdk_call.call_count == 1


def test_华为云ECS按官方offset_limit完整翻页(monkeypatch):
    _patch_ecs_interfaces(monkeypatch)
    requests = []
    pages = [
        {
            "count": 51,
            "servers": [_ecs_server(f"ecs-{index:03d}") for index in range(1, 51)],
        },
        {"count": 51, "servers": [_ecs_server("ecs-051")]},
    ]

    def list_servers(self, request):
        requests.append((getattr(request, "offset", None), request.limit))
        return _HuaweiSdkResponse(pages[len(requests) - 1])

    monkeypatch.setattr(
        cw_huaweicloud.EcsClient, "list_servers_details", list_servers
    )

    result = _hwcloud_manager().get_ecs()

    assert len(result) == 51
    assert [result[0]["resource_id"], result[-1]["resource_id"]] == [
        "ecs-001",
        "ecs-051",
    ]
    assert requests == [(None, 50), (50, 50)]


def _evs_volume(resource_id):
    return {
        "id": resource_id,
        "name": f"evs-{resource_id}",
        "description": "",
        "bootable": "false",
        "size": 40,
        "metadata": {"order_id": ""},
        "status": "available",
        "volume_type": "SSD",
        "attachments": [],
        "created_at": "2026-01-02T03:04:05Z",
        "availability_zone": "cn-south-1a",
        "encrypted": False,
    }


def test_华为云EVS单页空集缺可选字段和错误保持明确(monkeypatch):
    responses = [
        _HuaweiSdkResponse({"count": 1, "volumes": [_evs_volume("evs-001")]}),
        _HuaweiSdkResponse({"count": 0, "volumes": []}),
        RuntimeError("EVS.0001"),
    ]

    def list_volumes(self, request):
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(cw_huaweicloud.EvsClient, "list_volumes", list_volumes)
    manager = _hwcloud_manager()

    assert manager.get_evs()[0]["resource_id"] == "evs-001"
    assert manager.get_evs() == []
    assert manager.get_evs() == []


def test_华为云EVS按官方offset_limit完整翻页(monkeypatch):
    requests = []
    pages = [
        {
            "count": 51,
            "volumes": [
                _evs_volume(f"evs-{index:03d}") for index in range(1, 51)
            ],
        },
        {"count": 51, "volumes": [_evs_volume("evs-051")]},
    ]

    def list_volumes(self, request):
        requests.append((getattr(request, "offset", None), request.limit))
        return _HuaweiSdkResponse(pages[len(requests) - 1])

    monkeypatch.setattr(cw_huaweicloud.EvsClient, "list_volumes", list_volumes)

    result = _hwcloud_manager().get_evs()

    assert len(result) == 51
    assert [result[0]["resource_id"], result[-1]["resource_id"]] == [
        "evs-001",
        "evs-051",
    ]
    assert requests == [(None, 50), (50, 50)]


@pytest.mark.parametrize(
    ("case_ids", "method_name", "sdk_class", "sdk_method", "collection", "item"),
    (
        (
            ["hwcloud_vpc"],
            "get_vpc",
            cw_huaweicloud.VpcClient,
            "list_vpcs",
            "vpcs",
            {
                "id": "vpc-001",
                "name": "vpc-contract",
                "description": "",
                "status": "OK",
                "cidr": "192.0.2.0/24",
            },
        ),
        (
            ["hwcloud_subnet"],
            "get_subnet",
            cw_huaweicloud.VpcClient,
            "list_subnets",
            "subnets",
            {
                "id": "subnet-001",
                "name": "subnet-contract",
                "description": "",
                "status": "ACTIVE",
                "gateway_ip": "192.0.2.1",
                "cidr": "192.0.2.0/28",
                "vpc_id": "vpc-001",
            },
        ),
    ),
)
def test_华为云VPC与Subnet五态在V1V2无分页且错误不伪装成功(
    case_ids, method_name, sdk_class, sdk_method, collection, item, monkeypatch
):
    operation = next(
        item
        for item in HWCLOUD_SCENARIO_MATRIX["operations"]
        if item["case_ids"] == case_ids
    )
    responses = [
        _HuaweiSdkResponse({collection: [item]}),
        _HuaweiSdkResponse({collection: []}),
        _HuaweiSdkResponse({collection: [item]}),
        RuntimeError("VPC.0101"),
    ]

    def sdk_boundary(self, request):
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(sdk_class, sdk_method, sdk_boundary)
    manager = _hwcloud_manager()

    assert getattr(manager, method_name)()[0]["resource_id"] == item["id"]
    assert getattr(manager, method_name)() == []
    assert getattr(manager, method_name)()[0]["resource_id"] == item["id"]
    assert getattr(manager, method_name)() == []
    assert operation["pagination"]["kind"] == "not_applicable"
