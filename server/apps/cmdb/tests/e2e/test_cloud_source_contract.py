"""云采集 SDK/API 边界合同。

官方依据：
- 腾讯云 CVM DescribeInstances，API 2017-03-12：
  https://cloud.tencent.com/document/product/213/15728
- 阿里云 ECS DescribeInstances，API 2014-05-26：
  https://help.aliyun.com/en/ecs/developer-reference/api-ecs-2014-05-26-describeinstances
- 华为云 ECS ListServersDetails：
  https://support.huaweicloud.com/intl/en-us/api-ecs/ecs-api-pdf.pdf
"""

import socket
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
STARGAZER_ROOT = REPOSITORY_ROOT / "agents" / "stargazer"
if str(STARGAZER_ROOT) not in sys.path:
    sys.path.insert(0, str(STARGAZER_ROOT))

from aliyunsdkcore.request import CommonRequest  # noqa: E402
from common.cmp.cloud_apis.constant import CloudType  # noqa: E402
from plugins.inputs.aliyun.aliyun_info import Aliyun  # noqa: E402
from plugins.inputs.hwcloud.huaweicloud_info import HuaweiCloudManager  # noqa: E402
from plugins.inputs.qcloud.qcloud_info import TencentCloudManager  # noqa: E402
from tencentcloud.common.exception import tencent_cloud_sdk_exception  # noqa: E402


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
    proxy = SimpleNamespace(
        cvm=SimpleNamespace(call_json=call_json),
        tdmq=SimpleNamespace(call_json=call_json),
    )
    monkeypatch.setattr(manager, "get_tencent_client", lambda region: proxy)
    return manager


def _qcloud_cvm(instance):
    return {"Response": {"TotalCount": 1, "InstanceSet": [instance]}}


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
    sdk_call.assert_called_once_with("DescribeInstances", {})


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
    monkeypatch.setattr(collector, "_get_result", sdk_call)

    result = collector.list_vms()

    assert result["result"] is True
    assert [item["resource_id"] for item in result["data"]] == [
        "i-page-1",
        "i-page-2",
    ]
    assert sdk_call.call_count == 2


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
    monkeypatch.setattr(collector, "_get_result_c", sdk_call)

    result = collector._handle_list_request_with_page_c("vm", CommonRequest())

    assert result["result"] is True
    assert [item["resource_id"] for item in result["data"]] == [
        "i-common-1",
        "i-common-2",
    ]
    assert sdk_call.call_count == 2


def test_华为云驱动空集与缺可选字段保持稳定(monkeypatch):
    driver = SimpleNamespace(
        list_vms=Mock(
            return_value={
                "result": True,
                "data": [{"id": "ecs-minimal", "name": "minimal"}],
            }
        )
    )
    manager = HuaweiCloudManager(
        {
            "accessKey": "contract-id",
            "accessSecret": "contract-key",
            "region": "cn-south-1",
            "project_id": "project-001",
        }
    )
    monkeypatch.setattr(manager, "_driver", lambda: driver)

    result = manager.get_ecs()

    assert result[0]["resource_id"] == "ecs-minimal"
    assert result[0]["ip_addr"] == ""
    assert result[0]["memory_mb"] == ""


def test_华为云驱动错误转换为明确异常(monkeypatch):
    driver = SimpleNamespace(
        list_vms=Mock(return_value={"result": False, "message": "APIGW.0101"})
    )
    manager = HuaweiCloudManager(
        {
            "accessKey": "contract-id",
            "accessSecret": "contract-key",
            "project_id": "project-001",
        }
    )
    monkeypatch.setattr(manager, "_driver", lambda: driver)

    with pytest.raises(RuntimeError, match="APIGW.0101"):
        manager.get_ecs()
