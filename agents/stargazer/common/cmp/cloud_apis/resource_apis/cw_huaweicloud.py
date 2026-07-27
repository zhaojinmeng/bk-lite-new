# -*- coding: UTF-8 -*-
from __future__ import absolute_import, unicode_literals

import copy
import datetime
import time
import uuid
from functools import wraps

from huaweicloudsdkbss.v2 import (
    BssClient,
    DemandProductInfo,
    ListCustomerselfResourceRecordsRequest,
    ListOnDemandResourceRatingsRequest,
    ListRateOnPeriodDetailRequest,
    PeriodProductInfo,
    RateOnDemandReq,
    RateOnPeriodReq,
    RenewalResourcesReq,
    RenewalResourcesRequest,
    ShowCustomerAccountBalancesRequest,
    ShowCustomerMonthlySumRequest,
)
from huaweicloudsdkbss.v2.region.bss_region import BssRegion
from huaweicloudsdkces.v1 import BatchListMetricDataRequest, CesClient, ShowMetricDataRequest
from huaweicloudsdkces.v1.region.ces_region import CesRegion
from huaweicloudsdkces.v2 import CesClient as CesClient_V2
from huaweicloudsdkces.v2 import ListAgentDimensionInfoRequest
from huaweicloudsdkces.v2.region.ces_region import CesRegion as CesRegion_V2
from huaweicloudsdkcore.auth.credentials import BasicCredentials, GlobalCredentials
from huaweicloudsdkcore.exceptions import exceptions
from huaweicloudsdkcore.http.http_config import HttpConfig
from huaweicloudsdkdcs.v2 import DcsClient
from huaweicloudsdkdcs.v2 import ListFlavorsRequest as ListRedisFlavorsRequest
from huaweicloudsdkdcs.v2.region.dcs_region import DcsRegion
from huaweicloudsdkdds.v3 import DdsClient
from huaweicloudsdkdds.v3 import ListFlavorsRequest as ListMongodbFlavorsRequest
from huaweicloudsdkdds.v3.region.dds_region import DdsRegion
from huaweicloudsdkecs.v2 import (
    AttachServerVolumeOption,
    AttachServerVolumeRequest,
    AttachServerVolumeRequestBody,
    BatchRebootServersRequest,
    BatchRebootServersRequestBody,
    BatchRebootSeversOption,
    BatchStartServersOption,
    BatchStartServersRequest,
    BatchStartServersRequestBody,
    BatchStopServersOption,
    BatchStopServersRequest,
    BatchStopServersRequestBody,
    CreateServersRequest,
    CreateServersRequestBody,
    DeleteServersRequest,
    DeleteServersRequestBody,
    DetachServerVolumeRequest,
    EcsClient,
    GetServerRemoteConsoleOption,
    ListFlavorsRequest,
    ListServerInterfacesRequest,
    ListServersDetailsRequest,
    NovaAddSecurityGroupOption,
    NovaAssociateSecurityGroupRequest,
    NovaAssociateSecurityGroupRequestBody,
    NovaDisassociateSecurityGroupRequest,
    NovaDisassociateSecurityGroupRequestBody,
    NovaListAvailabilityZonesRequest,
    NovaRemoveSecurityGroupOption,
    PrePaidServer,
    PrePaidServerDataVolume,
    PrePaidServerEip,
    PrePaidServerEipBandwidth,
    PrePaidServerExtendParam,
    PrePaidServerNic,
    PrePaidServerPublicip,
    PrePaidServerRootVolume,
    PrePaidServerSecurityGroup,
    ResetServerPasswordOption,
    ResetServerPasswordRequest,
    ResetServerPasswordRequestBody,
    ResizePrePaidServerOption,
    ResizeServerRequest,
    ResizeServerRequestBody,
    ServerId,
)
from huaweicloudsdkecs.v2 import ShowJobRequest as ShowEcsJobRequest
from huaweicloudsdkecs.v2 import ShowServerRemoteConsoleRequest, ShowServerRemoteConsoleRequestBody, ShowServerRequest
from huaweicloudsdkecs.v2.region.ecs_region import EcsRegion
from huaweicloudsdkeip.v2 import (
    CreatePrePaidPublicipExtendParamOption,
    CreatePrePaidPublicipOption,
    CreatePrePaidPublicipRequest,
    CreatePrePaidPublicipRequestBody,
    CreatePublicipBandwidthOption,
    CreatePublicipOption,
    CreatePublicipRequest,
    CreatePublicipRequestBody,
    DeletePublicipRequest,
    EipClient,
    ListPublicipsRequest,
    ShowBandwidthRequest,
    ShowPublicipRequest,
    UpdateBandwidthOption,
    UpdateBandwidthRequest,
    UpdateBandwidthRequestBody,
    UpdatePublicipOption,
    UpdatePublicipRequest,
    UpdatePublicipsRequestBody,
)
from huaweicloudsdkeip.v2.region.eip_region import EipRegion
from huaweicloudsdkelb.v2 import DeleteMemberRequest
from huaweicloudsdkelb.v3 import (
    CreateListenerOption,
    CreateListenerRequest,
    CreateListenerRequestBody,
    CreateLoadBalancerOption,
    CreateLoadBalancerRequest,
    CreateLoadBalancerRequestBody,
    CreateMemberOption,
    CreateMemberRequest,
    CreateMemberRequestBody,
    CreatePoolOption,
    CreatePoolRequest,
    CreatePoolRequestBody,
    DeleteListenerRequest,
    DeleteLoadBalancerRequest,
    DeletePoolRequest,
    ElbClient,
    ListListenersRequest,
    ListLoadBalancersRequest,
    ListPoolsRequest,
    ShowListenerRequest,
    ShowLoadBalancerRequest,
    ShowPoolRequest,
    UpdateListenerOption,
    UpdateListenerRequest,
    UpdateListenerRequestBody,
    UpdateLoadBalancerOption,
    UpdateLoadBalancerRequest,
    UpdateLoadBalancerRequestBody,
    UpdatePoolOption,
    UpdatePoolRequest,
    UpdatePoolRequestBody,
)
from huaweicloudsdkelb.v3.region.elb_region import ElbRegion
from huaweicloudsdkevs.v2 import (
    BssParamForCreateVolume,
    BssParamForResizeVolume,
    CinderListVolumeTypesRequest,
    CreateSnapshotOption,
    CreateSnapshotRequest,
    CreateSnapshotRequestBody,
    CreateVolumeOption,
    CreateVolumeRequest,
    CreateVolumeRequestBody,
    DeleteSnapshotRequest,
    DeleteVolumeRequest,
    EvsClient,
    ListSnapshotsRequest,
    ListVolumesRequest,
    OsExtend,
    ResizeVolumeRequest,
    ResizeVolumeRequestBody,
    RollbackSnapshotOption,
    RollbackSnapshotRequest,
    RollbackSnapshotRequestBody,
)
from huaweicloudsdkevs.v2 import ShowJobRequest as ShowEvsJobRequest
from huaweicloudsdkevs.v2 import ShowSnapshotRequest, ShowVolumeRequest
from huaweicloudsdkevs.v2.region.evs_region import EvsRegion
from huaweicloudsdkiam.v3 import (
    IamClient,
    KeystoneListAuthDomainsRequest,
    KeystoneListAuthProjectsRequest,
    KeystoneListRegionsRequest,
    KeystoneShowRegionRequest,
)
from huaweicloudsdkiam.v3.region.iam_region import IamRegion
from huaweicloudsdkims.v2 import ImsClient, ListImagesRequest
from huaweicloudsdkims.v2.region.ims_region import ImsRegion
from huaweicloudsdkrds.v3 import ListFlavorsRequest as ListMySQLFlavorsRequest
from huaweicloudsdkrds.v3 import RdsClient
from huaweicloudsdkrds.v3.region.rds_region import RdsRegion
from huaweicloudsdkvpc.v2 import (
    CreateRouteTableReq,
    CreateRoutetableReqBody,
    CreateRouteTableRequest,
    CreateSecurityGroupOption,
    CreateSecurityGroupRequest,
    CreateSecurityGroupRequestBody,
    CreateSecurityGroupRuleOption,
    CreateSecurityGroupRuleRequest,
    CreateSecurityGroupRuleRequestBody,
    CreateSubnetOption,
    CreateSubnetRequest,
    CreateSubnetRequestBody,
    CreateVpcOption,
    CreateVpcRequest,
    CreateVpcRequestBody,
    CreateVpcRouteOption,
    CreateVpcRouteRequest,
    CreateVpcRouteRequestBody,
    DeleteRouteTableRequest,
    DeleteSecurityGroupRequest,
    DeleteSecurityGroupRuleRequest,
    DeleteSubnetRequest,
    DeleteVpcRequest,
    DeleteVpcRouteRequest,
    ExtraDhcpOption,
    ListRouteTablesRequest,
    ListSecurityGroupRulesRequest,
    ListSecurityGroupsRequest,
    ListSubnetsRequest,
    ListVpcRoutesRequest,
    ListVpcsRequest,
    ShowRouteTableRequest,
    ShowSecurityGroupRequest,
    ShowSecurityGroupRuleRequest,
    ShowSubnetRequest,
    ShowVpcRequest,
    ShowVpcRouteRequest,
    UpdateRouteTableReq,
    UpdateRoutetableReqBody,
    UpdateRouteTableRequest,
    VpcClient,
)
from huaweicloudsdkvpc.v2.region.vpc_region import VpcRegion
from loguru import logger
from obs import DeleteObjectsRequest, Object, ObsClient
from six.moves import range

from common.cmp.cloud_apis.base import PublicCloudManage
from common.cmp.cloud_apis.cloud_constant import CloudPlatform
from common.cmp.cloud_apis.constant import CloudResourceType
from common.cmp.cloud_apis.resource_apis.resource_format.common.base_format import get_format_method
from common.cmp.cloud_apis.resource_apis.resource_format.huaweicloud.huaweicloud_constant import (
    HwCloudDiskType,
    huaweicloud_bucket_cn_dict,
    huaweicloud_disk_cn_dict,
)
from common.cmp.cloud_apis.resource_apis.utils import check_required_params, fail, set_optional_params_huawei, success
from common.cmp.utils import (
    format_huawei_bill_charge_mode,
    format_public_cloud_resource_type,
    generate_serial_number,
    get_compute_price_module,
    get_storage_pricemodule,
    set_dir_size,
)


class CwHuaweicloud(object):
    """
    华为云组件类,通过该类创建华为云的Client实例，调用华为云api接口
    """

    def __init__(self, username, password, region_id, **kwargs):
        """
        初始化方法，创建Client实例。在创建Client实例时，您需要获取Region ID、username和password
        :param username:
        :param password:
        :param region_id:
        :param kwargs:
        """
        self.ak = username
        self.sk = password
        if "project_id" not in kwargs:
            raise ValueError("项目id不可为空")
        self.project_id = kwargs["project_id"]
        self.region_id = "cn-south-1" if not region_id else region_id
        for k, v in kwargs.items():
            setattr(self, k, v)

        config = HttpConfig.get_default_config()
        config.timeout = 10
        basic_credentials = BasicCredentials(self.ak, self.sk, self.project_id)
        global_credentials = GlobalCredentials(self.ak, self.sk)
        self.config = config
        self.basic_credentials = basic_credentials
        self.global_credentials = global_credentials
        self.obs_client = ObsClient(
            access_key_id=self.ak,
            secret_access_key=self.sk,
            server="https://obs.{}.myhuaweicloud.com".format(self.region_id),
        )

    def __getattr__(self, item):
        """
        private方法，返回对应的华为云接口类
        :param item:
        :return:
        """
        return Huaweicloud(
            ak=self.ak,
            sk=self.sk,
            name=item,
            region_id=self.region_id,
            project_id=self.project_id,
            config=self.config,
            basic_credentials=self.basic_credentials,
            global_credentials=self.global_credentials,
            obs_client=self.obs_client,
        )


class Huaweicloud(PublicCloudManage):
    """
    华为云接口类。使用华为云开发者工具套件（SDK），并进行封装，访问华为云服务
    """

    def __init__(self, ak, sk, name, region_id, project_id, config, basic_credentials, global_credentials, obs_client):
        self.ak = ak
        self.sk = sk
        self.name = name
        self.region_id = region_id
        self.project_id = project_id
        self.config = config
        self.basic_credentials = basic_credentials
        self.global_credentials = global_credentials
        self.obs_client = obs_client

    def __call__(self, *args, **kwargs):
        """
        找到功能方法name，并执行它
        """
        return getattr(self, self.name, self._non_function)(*args, **kwargs)

    @classmethod
    def _non_function(cls, *args, **kwargs):
        """
        未找到所需的功能方法
        """
        return {"result": True, "data": []}

    def get_client(self, client_class, region_class, **kwargs):
        """
        获取对应资源client
        :param client_class: 连接类，如IamClient
        :param region_class: 区域类，如IamRegion
        """
        return (
            client_class.new_builder()
            .with_http_config(self.config)
            .with_credentials(kwargs.get("credentials", self.basic_credentials))
            .with_region(region_class.value_of(kwargs.get("region_id", self.region_id)))
            .build()
        )

    def get_job_result(self, request, job_id, client_class, region_class, loop=60):
        """
        获取任务执行结果（部分华为云接口异步执行）
        :param request 获取任务的请求类， 如ShowEcsJobRequest
        :param job_id 任务ID， 类型：str
        :param client_class 连接类，如IamClient
        :param region_class 连接类，如IamRegion
        :param loop 轮训次数。类型：int
        """
        if loop < 0:
            return fail("执行失败")
        request.job_id = job_id

        @exception_handler
        def show_job():
            return self.get_client(client_class, region_class).show_job(request)

        response = show_job
        if not response["result"]:
            logger.error(response["message"])
            return fail("执行失败")
        job_obj = response["data"]
        if job_obj["status"] == "SUCCESS":
            return success("执行成功")
        elif job_obj["status"] == "FAIL":
            logger.error(job_obj["fail_reason"])
            return fail("执行失败")
        else:
            time.sleep(5)
            loop -= 1
            return self.get_job_result(request, job_id, client_class, region_class, loop)

    def list_projects(self, resource_id="", **kwargs):
        """
        查询IAM用户可以访问的项目列表或详情
        """
        if resource_id:
            return fail("暂不支持获取此类资源详情")
        request = KeystoneListAuthProjectsRequest()

        @exception_handler
        def keystone_list_auth_projects():
            return self.get_client(
                IamClient, IamRegion, credentials=self.global_credentials
            ).keystone_list_auth_projects(request)

        response = keystone_list_auth_projects
        if not response["result"]:
            logger.error(response["message"])
            return fail("项目列表获取失败")
        return success(
            format_resource(
                CloudResourceType.PROJECT.value, response["data"]["projects"], self.region_id, self.project_id
            )
        )

    def list_regions(self, resource_id="", **kwargs):
        """
        查询区域列表或详情
        """
        if resource_id:
            return self.get_region_detail(resource_id)
        request = KeystoneListRegionsRequest()

        @exception_handler
        def keystone_list_regions():
            return self.get_client(IamClient, IamRegion, credentials=self.global_credentials).keystone_list_regions(
                request
            )

        response = keystone_list_regions
        if not response["result"]:
            logger.error(response["message"])
            return fail("区域列表获取失败")
        return success(
            format_resource(
                CloudResourceType.REGION.value, response["data"]["regions"], self.region_id, self.project_id
            )
        )

    def get_region_detail(self, region_id):
        """
        查询区域详情
        """
        request = KeystoneShowRegionRequest()
        request.region_id = region_id

        @exception_handler
        def keystone_show_region():
            return self.get_client(IamClient, IamRegion, credentials=self.global_credentials).keystone_show_region(
                request
            )

        response = keystone_show_region
        if not response["result"]:
            logger.error(response["message"])
            return fail("区域详情获取失败")
        return success(
            format_resource(
                CloudResourceType.REGION.value, [response["data"]["region"]], self.region_id, self.project_id
            )
        )

    def list_domains(self, resource_id="", **kwargs):
        """
        查询IAM用户可以访问的账号详情或详情
        """
        if resource_id:
            return fail("暂不支持获取此类资源详情")
        request = KeystoneListAuthDomainsRequest()

        @exception_handler
        def keystone_list_auth_domains():
            return self.get_client(
                IamClient, IamRegion, credentials=self.global_credentials
            ).keystone_list_auth_domains(request)

        response = keystone_list_auth_domains
        if not response["result"]:
            logger.error(response["message"])
            return fail("账号列表获取失败")
        return success(
            format_resource(
                CloudResourceType.DOMAIN.value, response["data"]["domains"], self.region_id, self.project_id
            )
        )

    def get_connection_result(self):
        """
        测试连接是否正常
        """
        response = self.list_domains()

        if not response["result"]:
            return response
        # 当获取到[]
        if not response["data"]:
            return fail()
        return success()

    def list_zones(self, resource_id="", **kwargs):
        """
        查询可用区列表或详情
        """
        if resource_id:
            return fail("暂不支持获取此类资源详情")
        request = NovaListAvailabilityZonesRequest()

        @exception_handler
        def nova_list_availability_zones():
            return self.get_client(EcsClient, EcsRegion).nova_list_availability_zones(request)

        response = nova_list_availability_zones
        if not response["result"]:
            logger.error(response["message"])
            return fail("可用区列表获取失败")
        return success(
            format_resource(
                CloudResourceType.ZONE.value,
                response["data"]["availability_zone_info"],
                self.region_id,
                self.project_id,
            )
        )

    def list_instance_types(self, resource_id="", **kwargs):
        """
        查询规格列表或详情
        """
        if resource_id:
            return fail("暂不支持获取此类资源详情")
        request = ListFlavorsRequest()
        if "availability_zone" in kwargs:
            request.availability_zone = kwargs["availability_zone"]

        @exception_handler
        def _list_flavors():
            return self.get_client(EcsClient, EcsRegion).list_flavors(request)

        response = _list_flavors
        if not response["result"]:
            logger.error(response["message"])
            return fail("规格列表获取失败")
        return success(
            format_resource(
                CloudResourceType.INSTANCE_TYPE.value, response["data"]["flavors"], self.region_id, self.project_id
            )
        )

    def get_spec_price(self, **kwargs):
        """
        查询指定规格报价
        :param kwargs: zone，spec
        :return: spec_price
        """
        request = ListOnDemandResourceRatingsRequest()

        # 查询虚拟机每小时的单位官网价
        listDemandProductInfoProductInfosbody = [
            DemandProductInfo(
                id="1",
                cloud_service_type="hws.service.type.ec2",
                resource_type="hws.resource.type.vm",
                resource_spec="{}.linux".format(kwargs["spec"]),
                region=self.region_id,
                usage_factor="Duration",
                usage_value=1,
                usage_measure_id=4,
                subscription_num=1,
            )
        ]
        request.body = RateOnDemandReq(product_infos=listDemandProductInfoProductInfosbody, project_id=self.project_id)

        @exception_handler
        def list_on_demand_resource_ratings():
            return self.get_client(
                BssClient, BssRegion, credentials=self.global_credentials, region_id="cn-north-1"
            ).list_on_demand_resource_ratings(request)

        response = list_on_demand_resource_ratings
        if not response["result"]:
            logger.error(response["message"])
            return fail("规格报价获取失败")
        product_rating_results = response["data"]["product_rating_results"]
        for i in product_rating_results:
            if i["id"] == "1":
                return success(i["official_website_amount"])
        return fail("规格报价获取失败")

    def get_storage_list(self, **kwargs):
        """
        查询多种云盘(包年包月)价格 单位 每G每月
        """
        storage_type = [
            {"name": HwCloudDiskType.GPSSD_cn.value, "type": HwCloudDiskType.GPSSD.value},
            {"name": HwCloudDiskType.SAS_cn.value, "type": HwCloudDiskType.SAS.value},
            {"name": HwCloudDiskType.SSD_cn.value, "type": HwCloudDiskType.SSD.value},
        ]
        request = ListRateOnPeriodDetailRequest()
        product_infos = [
            PeriodProductInfo(
                id="1",
                cloud_service_type="hws.service.type.ebs",
                resource_type="hws.resource.type.volume",
                resource_spec="SATA",
                region=self.region_id,
                resource_size=1,
                size_measure_id=17,
                period_type=2,
                period_num=1,
                subscription_num=1,
            ),
            PeriodProductInfo(
                id="2",
                cloud_service_type="hws.service.type.ebs",
                resource_type="hws.resource.type.volume",
                resource_spec="SAS",
                region=self.region_id,
                resource_size=1,
                size_measure_id=17,
                period_type=2,
                period_num=1,
                subscription_num=1,
            ),
            PeriodProductInfo(
                id="3",
                cloud_service_type="hws.service.type.ebs",
                resource_type="hws.resource.type.volume",
                resource_spec="SSD",
                region=self.region_id,
                resource_size=1,
                size_measure_id=17,
                period_type=2,
                period_num=1,
                subscription_num=1,
            ),
        ]
        request.body = RateOnPeriodReq(product_infos=product_infos, project_id=self.project_id)

        @exception_handler
        def list_rate_on_period_detail():
            return self.get_client(
                BssClient, BssRegion, credentials=self.global_credentials, region_id="cn-north-1"
            ).list_rate_on_period_detail(request)

        response = list_rate_on_period_detail
        if not response["result"]:
            return response
        price_data = response["data"]["official_website_rating_result"]["product_rating_results"]
        storage_price_list = []
        for i in price_data:
            index = int(i["id"]) - 1
            storage_price_list.append(
                {
                    "price": i["official_website_amount"],
                    "name": storage_type[index]["name"],
                    "type": storage_type[index]["type"],
                }
            )
        return success(storage_price_list)

    def list_instance_type_families(self, **kwargs):
        """
        查询规格族
        """
        request = ListFlavorsRequest()

        @exception_handler
        def list_flavors():
            return self.get_client(EcsClient, EcsRegion).list_flavors(request)

        response = list_flavors
        if not response["result"]:
            logger.error("规格则列表获取失败")
            return response
        flavor_list = response["data"]["flavors"]
        return_data = []
        type_list = []
        for i in flavor_list:
            instance_type_familie = i["name"].split(".")[0]
            if instance_type_familie not in type_list:
                return_data.append({"id": instance_type_familie, "name": instance_type_familie})
                type_list.append(instance_type_familie)
        return success(
            format_resource(CloudResourceType.INSTANCE_TYPE_FAMILY.value, return_data, self.region_id, self.project_id)
        )

    def get_spec_list(self, instance_type_familie_id):
        """
        查询某规格族下规格列表
        instance_type_familie: 规格族id，str
        """
        if not instance_type_familie_id:
            return fail("规格族不能为空")
        request = ListFlavorsRequest()

        @exception_handler
        def list_flavors():
            return self.get_client(EcsClient, EcsRegion).list_flavors(request)

        response = list_flavors
        if not response["result"]:
            return response
        flavor_list = response["data"]["flavors"]
        return_data = []
        for i in flavor_list:
            if i["name"].split(".")[0] == instance_type_familie_id:
                return_data.append(
                    {
                        "id": i["id"],
                        "text": i["name"],
                        "InstanceType": i["id"],
                        "CPU": i["vcpus"],
                        "Memory": i["ram"] // 1024,
                    }
                )
        return success(return_data)

    def list_vms(self, ids="", **kwargs):
        """
        获取虚拟机列表或虚拟机详情
        """
        if ids:
            return self.get_vm_detail(ids[0])
        request = ListServersDetailsRequest()
        page_size = 50
        request.limit = page_size
        list_optional_params = [
            "enterprise_project_id",
            "flavor",
            "ip",
            "name",
            "not-tags",
            "reservation_id",
            "status",
            "tags",
        ]
        request = set_optional_params_huawei(list_optional_params, kwargs, request)

        def list_servers_details():
            @exception_handler
            def request_page():
                return self.get_client(EcsClient, EcsRegion).list_servers_details(request)

            return request_page

        response = list_servers_details()
        if not response["result"]:
            logger.error(response["message"])
            return fail("虚拟机列表获取失败")
        response = response["data"]
        count = response["count"]
        servers_list = list(response["servers"])
        data = []
        while len(servers_list) < count:
            request.offset = len(servers_list)
            response = list_servers_details()
            if not response["result"]:
                logger.error(response["message"])
                return fail("虚拟机列表获取失败")
            page_servers = response["data"]["servers"]
            if not page_servers:
                break
            servers_list.extend(page_servers)
        for server in servers_list:
            res = self.get_server_interfaces(server["id"])
            if not res["data"]:
                logger.error(res["message"])
                return fail("虚拟机子网信息获取失败")
            server["subnet_id"] = res["data"]
            data.append(server)
        return success(format_resource(CloudResourceType.VM.value, data, self.region_id, self.project_id))

    def get_vm_detail(self, resource_id, **kwargs):
        """
        获取虚拟机详情
        """
        request = ShowServerRequest()
        request.server_id = resource_id

        @exception_handler
        def show_server():
            return self.get_client(EcsClient, EcsRegion).show_server(request)

        response = show_server
        if not response["result"]:
            logger.error(response["message"])
            return fail("虚拟机详情获取失败")
        server = response["data"]["server"]
        res = self.get_server_interfaces(server["id"])
        if not response["result"]:
            logger.error(response["message"])
            return fail("虚拟机子网信息获取失败")
        server["subnet_id"] = res["data"]
        return success(format_resource(CloudResourceType.VM.value, [server], self.region_id, self.project_id))

    def get_server_interfaces(self, server_id):
        """
        获取虚拟机网卡信息
        :return:
        """
        request = ListServerInterfacesRequest()
        request.server_id = server_id

        @exception_handler
        def list_server_interfaces():
            return self.get_client(EcsClient, EcsRegion).list_server_interfaces(request)

        response = list_server_interfaces
        if not response["result"]:
            logger.error(response["message"])
            return fail("网卡信息获取失败")
        subnet_id = response["data"]["interface_attachments"][0]["fixed_ips"][0]["subnet_id"]
        return success(subnet_id)

    def start_vm(self, vm_id, **kwargs):
        """
        云服务器开机
        :param resource_id: 云服务器ID
        :return: result: 结果判定，布尔值
                data：result=True时，返回的值，{"job_id": "70a599e0-31e7-49b7-b260-868f441e862b"}
                message: result=False时，返回的错误信息。
        """
        request = BatchStartServersRequest()
        servers = [ServerId(id=vm_id)]
        os_start = BatchStartServersOption(servers=servers)
        request.body = BatchStartServersRequestBody(os_start=os_start)

        @exception_handler
        def batch_start_servers():
            return self.get_client(EcsClient, EcsRegion).batch_start_servers(request)

        response = batch_start_servers
        if not response["result"]:
            logger.error(response["message"])
            return fail("执行失败")
        # 获取执行任务结果
        return self.get_job_result(ShowEcsJobRequest(), response["data"]["job_id"], EcsClient, EcsRegion)

    def stop_vm(self, vm_id, **kwargs):
        """
        云服务器关机
        :param resource_id: 云服务器ID
        :return: result: 结果判定，布尔值
                data：result=True时，返回的值，{"job_id": "70a599e0-31e7-49b7-b260-868f441e862b"}
                message: result=False时，返回的错误信息。
        """
        request = BatchStopServersRequest()
        servers = [ServerId(id=vm_id)]
        os_stop = BatchStopServersOption(servers=servers)
        request.body = BatchStopServersRequestBody(os_stop=os_stop)

        @exception_handler
        def batch_stop_servers():
            return self.get_client(EcsClient, EcsRegion).batch_stop_servers(request)

        response = batch_stop_servers
        if not response["result"]:
            logger.error(response["message"])
            return fail("执行失败")
        # 获取执行任务结果
        return self.get_job_result(ShowEcsJobRequest(), response["data"]["job_id"], EcsClient, EcsRegion)

    def restart_vm(self, vm_id, **kwargs):
        """
        云服务器重启
        :param resource_id: 云服务器ID
               kwargs:
                 type：取值范围：SOFT，普通重启；HARD：强制重启
        :return: result: 结果判定，布尔值
                data：result=True时，返回的值，{"job_id": "70a599e0-31e7-49b7-b260-868f441e862b"}
                message: result=False时，返回的错误信息。
        """
        request = BatchRebootServersRequest()
        servers = [ServerId(id=vm_id)]
        reboot = BatchRebootSeversOption(servers=servers, type=kwargs.get("type", "SOFT"))
        request.body = BatchRebootServersRequestBody(reboot=reboot)

        @exception_handler
        def batch_reboot_servers():
            return self.get_client(EcsClient, EcsRegion).batch_reboot_servers(request)

        response = batch_reboot_servers
        if not response["result"]:
            logger.error(response["message"])
            return fail("执行失败")
        # 获取执行任务结果
        return self.get_job_result(ShowEcsJobRequest(), response["data"]["job_id"], EcsClient, EcsRegion)

    def destroy_vm(self, **kwargs):
        """
        云服务器释放
        :param kwargs:
               vm_id：云服务器ID
               kwargs:
                 delete_volume：是否同时删除磁盘
                 delete_publicip：是否同时删除弹性公网
        :return: result: 结果判定，布尔值
                data：result=True时，返回的值，{"job_id": "70a599e0-31e7-49b7-b260-868f441e862b"}
                message: result=False时，返回的错误信息。
        """
        request = DeleteServersRequest()
        servers = [ServerId(id=kwargs["vm_id"])]
        request.body = DeleteServersRequestBody(
            servers=servers,
            delete_volume=kwargs.get("delete_volume", False),
            delete_publicip=kwargs.get("delete_publicip", False),
        )

        @exception_handler
        def delete_servers():
            return self.get_client(EcsClient, EcsRegion).delete_servers(request)

        response = delete_servers
        if not response["result"]:
            logger.error(response["message"])
            return fail("执行失败")
        # 获取执行任务结果
        return self.get_job_result(ShowEcsJobRequest(), response["data"]["job_id"], EcsClient, EcsRegion)

    def create_vm(self, **kwargs):
        """
        创建主机
        :param kwargs:
                    adminPass：指定云服务器管理员帐户初始登录密码。类型：String。
                    availability_zone：创建云服务器所在的可用分区。类型：String。必选
                    count：创建云服务器数量。类型：integer。
                    data_volumes：云服务器对应数据盘相关配置。类型：Arrays of DataVolumes objects。
                        DataVolumes：data_image_id：数据镜像的ID,UUID格式。类型：String。
                                     extendparam：磁盘的产品信息。类型：server.data_volumes.Extendparam object。
                                        Extendparam：snapshotId：整机镜像中自带的原始数据盘(简称“原数据盘”)所对应的快照ID或
                                                    原始数据盘ID。
                                     hw:passthrough：数据卷是否使用SCSI锁。类型：boolean。
                                     metadata：创建云硬盘的metadata信息。类型：Metadata object。
                                        Metadata：__system__cmkid：metadata中的加密cmkid字段,与__system__encrypted配合表示
                                                需要加密,cmkid长度固定为36个字节。
                                                __system__encrypted：metadata中的表示加密功能的字段,0代表不加密,1代表加密。
                                     multiattach：创建共享磁盘的信息。true:创建的磁盘为共享盘。false:创建的磁盘为普通云硬盘。
                                        类型：boolean
                                     size：数据盘大小,容量单位为GB,输入大小范围为[10,32768]。类型：integer。必选
                                     volumetype：云服务器数据盘对应的磁盘类型,需要与系统所提供的磁盘类型相匹配
                                               磁盘类型枚举值：SATA:普通IO磁盘类型。
                                                              SAS:高IO磁盘类型。
                                                              SSD:超高IO磁盘类型。
                                                              co-p1:高IO (性能优化I型)
                                                              uh-l1:超高IO (时延优化)磁盘类型。类型：String。必选
                    description：云服务器描述信息,默认为空字符串。类型：String。
                    extendparam：创建云服务器附加信息。 server.Extendparam object。必选。
                        chargingMode：计费模式：取值范围:
                                                prePaid-预付费,即包年包月;
                                                postPaid-后付费,即按需付费;
                                                默认值是postPaid
                        enterprise_project_id：企业项目ID。类型：string
                        marketType：创建竞价实例时,需指定该参数的值为“spot”。类型：string
                        regionID：云服务器所在区域ID。类型：string
                        spotPrice：用户愿意为竞价实例每小时支付的最高价格。类型：string
                        support_auto_recovery：是否配置弹性云服务器自动恢复的功能。类型：boolean
                        periodNum:订购周期数。periodType=month(周期类型为月)时,取值为[1,9];
                                             periodType=year(周期类型为年)时,取值为[1,3];类型：int
                        periodType:订购周期类型。取值范围:month-月,year-年。类型：string
                    flavorRef：待创建云服务器的系统规格的ID。类型：string。必选
                    imageRef：待创建云服务器的系统镜像,需要指定已创建镜像的ID。类型：string。必选
                    isAutoRename：当批量创建弹性云服务器时,云服务器名称是否允许重名,当count大于1的时候该参数生效。类型：boolean
                    key_name：如果需要使用SSH密钥方式登录云服务器,请指定已创建密钥的名称。类型：string
                    metadata：用户自定义字段键值对。类型：Map<String,String>
                    name：云服务器名称。类型：string。必选。
                    nics：待创建云服务器的网卡信息。网卡对应的子网(subnet)必须属于vpcid对应的VPC。类型：Arrays of Nics objects。必选。
                        server.nics：ip_address：待创建云服务器网卡的IP地址,IPv4格式。类型：string
                                    ipv6_bandwidth：绑定的共享带宽ID。类型：string
                                    ipv6_enable：是否支持ipv6。类型：string。
                                    subnet_id：待创建云服务器的网卡信息。类型：string。必选。
                    os:scheduler_hints：云服务器调度信息。类型：Os:schedulerHints object。
                        server.os:scheduler_hints：group：云服务器组ID。类型：string。
                    publicip：配置云服务器的弹性IP信息,弹性IP有三种配置方式。不使用(无该字段)自动分配,需要指定新创建弹性IP的信息，
                    使用已有,需要指定已创建弹性IP的信息。类型：Publicip object
                        server.publicip：eip：配置云服务器自动分配弹性IP时,创建弹性IP的配置参数。类型：Eip object。
                                            server.publicip.eip：bandwidth：弹性IP地址带宽参数。类型：Bandwidth object。必选
                                                server.publicip.eip.bandwidth：
                                                       chargemode：带宽的计费类型。未传该字段,表示按带宽计费。字段值为
                                                       “traffic”,表示按流量计费。类型：string。
                                                        id：带宽ID。类型：string。
                                                        sharetype：带宽的共享类型。共享类型枚举:PER,表示独享。WHOLE,
                                                        表示共享。类型：string。必选
                                                        size：带宽大小。带宽(Mbit/s),取值范围为[1,2000]。类型：integer。
                                                extendparam：创建弹性IP的附加信息。类型：Extendparam object。
                                                    server.publicip.eip.extendparam：
                                                        chargingMode：公网IP的计费模式。prePaid-预付费,即包年包月;
                                                        postPaid-后付费,即按需付费;类型：string。
                                                iptype：弹性IP地址类型。类型：string。
                                        id：为待创建云服务器分配已有弹性IP时,分配的弹性IP的ID。只能分配状态(status)为DOWN的
                                        弹性IP。类型：string
                    root_volume：云服务器对应系统盘相关配置。类型：RootVolume object。必选。
                        server.root_volume：
                            extendparam：磁盘的产品信息。类型：Extendparam object。
                                 server.root_volume.extendparam:
                                   snapshotId：整机镜像中自带的原始数据盘(简称“原数据盘”)所对应的快照ID或原始数据盘ID。
                                   类型：string。
                            hw:passthrough：,如果该参数值为true,说明创建的为scsi类型的卷。类型：boolean
                            size：系统盘大小,容量单位为GB, 输入大小范围为[1,1024]。类型：integer
                            volumetype：云服务器系统盘对应的磁盘类型,需要与系统所提供的磁盘类型相匹配。类型：string。必选
                    security_groups：云服务器对应安全组信息。类型：Arrays of SecurityGroups objects。
                        server.security_groups:
                            id:可以为空,待创建云服务器的安全组,会对创建云服务器中配置的网卡生效。类型：string。必选
                    server_tags：弹性云服务器的标签。类型：Arrays of Map。
                    tags：弹性云服务器的标签。类型：Arrays of string
                    user_data：创建云服务器过程中待注入用户数据。类型：string
                    vpcid：待创建云服务器所属虚拟私有云(简称VPC),需要指定已创建VPC的ID。类型：string。必选
        :return:
        """
        # 设置创建参数
        request = self._set_create_vm_params(**kwargs)

        @exception_handler
        def create_servers():
            return self.get_client(EcsClient, EcsRegion).create_servers(request)

        response = create_servers
        if not response["result"]:
            logger.error(response["message"])
            return fail("执行失败")
        return success(response["data"]["serverIds"])

    def _set_create_vm_params(self, **kwargs):
        """
        设置创建虚拟机的参数
        """
        request = CreateServersRequest()
        extendparam = PrePaidServerExtendParam(
            charging_mode=kwargs["extendparam"]["chargingMode"],
            period_type=kwargs["extendparam"].get("periodType"),
            period_num=kwargs["extendparam"].get("periodNum"),
        )
        root_volume = PrePaidServerRootVolume(
            volumetype=kwargs["root_volume"]["volumetype"], size=kwargs["root_volume"]["size"]
        )
        data_volumes = []
        for volume in kwargs.get("data_volumes", []):
            data_volumes.append(PrePaidServerDataVolume(volumetype=volume["volumetype"], size=volume["size"]))
        nics = []
        for nic in kwargs.get("nics", []):
            nics.append(PrePaidServerNic(subnet_id=nic["subnet_id"]))
        security_groups = []
        for security_group_id in kwargs.get("security_groups", []):
            security_groups.append(PrePaidServerSecurityGroup(id=security_group_id))
        publicip = None
        if kwargs.get("publicip"):
            eip = None
            if "eip" in kwargs["publicip"]:
                eip = PrePaidServerEip(
                    iptype=kwargs["publicip"]["eip"]["iptype"],
                    bandwidth=PrePaidServerEipBandwidth(
                        size=kwargs["publicip"]["eip"]["bandwidth"]["size"],
                        sharetype=kwargs["publicip"]["eip"]["bandwidth"]["sharetype"],
                    ),
                )
            publicip = PrePaidServerPublicip(id=kwargs["publicip"].get("id"), eip=eip)
        server = PrePaidServer(
            image_ref=kwargs["imageRef"],
            flavor_ref=kwargs["flavorRef"],
            name=kwargs["name"],
            user_data=kwargs.get("user_data", ""),
            admin_pass=kwargs.get("admin_pass", ""),
            key_name=kwargs.get("key_name", ""),
            vpcid=kwargs["vpcid"],
            nics=nics if nics else None,
            publicip=publicip,
            count=kwargs.get("count", 1),
            root_volume=root_volume,
            data_volumes=data_volumes if data_volumes else None,
            security_groups=security_groups if security_groups else None,
            extendparam=extendparam,
            availability_zone=kwargs["availability_zone"],
        )
        request.body = CreateServersRequestBody(server=server, dry_run=False)
        return request

    def resize_vm(self, **kwargs):
        """
        主机变配
        :param kwargs:
            vm_id (str): 虚拟机id.    (required)
            instance_type_id (str): 变更后的虚拟机规格id.     (required)
        :return:
        """
        request = ResizeServerRequest()
        request.server_id = kwargs["vm_id"]
        resize = ResizePrePaidServerOption(flavor_ref=kwargs["instance_type_id"])
        request.body = ResizeServerRequestBody(resize=resize)

        @exception_handler
        def resize_server():
            return self.get_client(EcsClient, EcsRegion).resize_server(request)

        response = resize_server
        if not response["result"]:
            logger.error(response["message"])
            return fail("执行失败")
        return self.get_job_result(ShowEcsJobRequest(), response["data"]["job_id"], EcsClient, EcsRegion)

    def renew_vm(self, **kwargs):
        """
        虚拟机续期
        :param kwargs:
                vm_id: 虚拟机ID
                period:包年包月续费时长
        """
        resource_ids = [kwargs["vm_id"]]
        period_num = kwargs["period"]
        period_type = 2
        if period_num > 12:
            period_type = 3
            period_num = period_num // 12
        request = RenewalResourcesRequest()
        request.body = RenewalResourcesReq(
            is_auto_pay=1, expire_policy=0, period_num=period_num, period_type=period_type, resource_ids=resource_ids
        )

        @exception_handler
        def resize_server():
            return self.get_client(BssClient, BssRegion, credentials=self.global_credentials).resize_server(request)

        response = resize_server
        if not response["result"]:
            logger.error(response["message"])
            return fail("执行失败")
        return {"result": True, "data": "执行成功"}

    def get_available_flavor(self, **kwargs):
        """
        获取可用规格
        :param kwargs:
               zone_id: 可用区id
        :return:
        """
        availability_zone = kwargs["zone_id"]
        cpu = int(kwargs["cpu"])
        memory = int(kwargs["memory"])
        res = self.list_instance_types(availability_zone=availability_zone)
        if not res["result"]:
            logger.error(res["message"])
            return fail("获取规格列表失败")
        list_instance_types = res["data"]
        for instance_type in list_instance_types:
            if instance_type.vcpus == cpu and instance_type.memory // 1024 == memory:
                return success(instance_type.resource_id)

    def remote_connect_vm(self, **kwargs):
        """
        获取远程控制台URL
        :param kwargs:
                vm_id：虚拟机id
        :return: url
        """
        request = ShowServerRemoteConsoleRequest()
        request.server_id = kwargs["vm_id"]
        remote_console = GetServerRemoteConsoleOption(protocol="vnc", type="novnc")
        request.body = ShowServerRemoteConsoleRequestBody(remote_console=remote_console)

        @exception_handler
        def show_server_remote_console():
            return self.get_client(EcsClient, EcsRegion).show_server_remote_console(request)

        response = show_server_remote_console
        if not response["result"]:
            logger.error(response["message"])
            return fail("获取远程控制台URL失败")
        return success(response["data"]["remote_console"]["url"])

    def reset_instances_password(self, **kwargs):
        r"""
        重置弹性云服务器管理帐号（root用户或Administrator用户）的密码。
        :param kwargs:
                vm_id：类型：String。必选。描述：云服务器ID。
                new_password：类型：String。必选。描述：弹性云服务器新密码。该接口默认不做密码安全性校验；如需校验，请指定字
                段“is_check_password”为true。新密码的校验规则：
                            密码长度范围为8到26位。
                            允许输入的字符包括：!@%-_=+[]:./?
                            禁止输入的字符包括：汉字及【】：；“”‘’、，。《》？￥…（）—— ·！~`#&^,{}*();"'<>|\ $
                            复杂度上必须包含大写字母（A-Z）、小写字母（a-z）、数字（0-9）、以及允许的特殊字符中的3种以上搭配
                            不能包含用户名 "Administrator" 和“root”及逆序字符
                            不能包含用户名 "Administrator" 中连续3个字符
                    is_check_password：类型：Boolean。描述：是否检查密码的复杂度。
        :return:
        """
        request = ResetServerPasswordRequest()
        request.server_id = kwargs["server_id"]
        reset_password = ResetServerPasswordOption(new_password=kwargs["new_password"])
        request.body = ResetServerPasswordRequestBody(reset_password=reset_password)

        @exception_handler
        def reset_server_password():
            return self.get_client(EcsClient, EcsRegion).reset_server_password(request)

        response = reset_server_password
        if not response["result"]:
            return fail("执行失败")
        return success("执行成功")

    def associate_security_groups(self, **kwargs):
        """
        给实例绑定安全组
        :param kwargs:
            vm_id: 弹性云服务器id
            security_group_id: string 安全组id
        :type kwargs:
        :return:
        :rtype:
        """
        request = NovaAssociateSecurityGroupRequest()
        request.server_id = kwargs["server_id"]
        add_security_group = NovaAddSecurityGroupOption(name=kwargs["security_group_id"])
        request.body = NovaAssociateSecurityGroupRequestBody(add_security_group=add_security_group)

        @exception_handler
        def nova_associate_security_group():
            return self.get_client(EcsClient, EcsRegion).nova_associate_security_group(request)

        response = nova_associate_security_group
        if not response["result"]:
            logger.error(response["message"])
            return fail("执行失败")
        return success("执行成功")

    def disassociate_security_groups(self, **kwargs):
        """
        给实例绑解绑安全组
        :param kwargs:
            vm_id: vm id
            security_group_ids: list 安全组id集合
        :type kwargs:
        :return:
        :rtype:
        """
        request = NovaDisassociateSecurityGroupRequest()
        request.server_id = kwargs["server_id"]
        remove_security_group = NovaRemoveSecurityGroupOption(name=kwargs["security_group_id"])
        request.body = NovaDisassociateSecurityGroupRequestBody(remove_security_group=remove_security_group)

        @exception_handler
        def nova_disassociate_security_group():
            return self.get_client(EcsClient, EcsRegion).nova_disassociate_security_group(request)

        response = nova_disassociate_security_group
        if not response["result"]:
            logger.error(response["message"])
            return fail("执行失败")
        return success("执行成功")

    # ----------------镜像------------------------------

    def list_images(self, ids=None):
        """
        查询镜像列表信息
        :return: image_list
        """
        request = ListImagesRequest()
        request.limit = 2000

        @exception_handler
        def list_images():
            return self.get_client(ImsClient, ImsRegion).list_images(request)

        response = list_images
        if not response["result"]:
            logger.error(response["message"])
            return fail("镜像列表获取失败")
        return success(
            format_resource(CloudResourceType.IMAGE.value, response["data"]["images"], self.region_id, self.project_id)
        )

    # --------------云盘-------------------------

    def list_disks(self, ids=None, **kwargs):
        """
        获取云盘列表
        :param resource_id:
        :param kwargs:
        ----------------
        * disk_ids: the IDs of disk.(optional)
                    type: array
        ----------------
        :return: volume_list
        """
        # 查询单个详情
        if ids:
            return self.get_disk_detail(ids[0])
        request = ListVolumesRequest()
        page_size = 50
        request.limit = page_size
        list_optional_params = [
            "marker",
            "name",
            "sort_key",
            "sort_dir",
            "status",
            "metadata",
            "availability_zone",
            "service_type",
            "multiattach",
            "dedicated_storage_id",
            "volume_type_id",
            "ids",
        ]
        request = set_optional_params_huawei(list_optional_params, kwargs, request)

        def list_volumes():
            @exception_handler
            def request_page():
                return self.get_client(EvsClient, EvsRegion).list_volumes(request)

            return request_page

        response = list_volumes()
        if not response["result"]:
            logger.error(response["message"])
            return fail("获取磁盘列表失败")
        response = response["data"]
        count = response["count"]
        volume_list = list(response["volumes"])
        while len(volume_list) < count:
            request.offset = len(volume_list)
            response = list_volumes()
            if not response["result"]:
                logger.error(response["message"])
                return fail("获取磁盘列表失败")
            page_volumes = response["data"]["volumes"]
            if not page_volumes:
                break
            volume_list.extend(page_volumes)
        return success(format_resource(CloudResourceType.DISK.value, volume_list, self.region_id, self.project_id))

    def get_disk_detail(self, resource_id, **kwargs):
        request = ShowVolumeRequest()
        request.volume_id = resource_id

        @exception_handler
        def show_volume():
            return self.get_client(EvsClient, EvsRegion).show_volume(request)

        response = show_volume
        if not response["result"]:
            logger.error(response["message"])
            return fail("获取磁盘详情失败")
        return success(
            format_resource(CloudResourceType.DISK.value, [response["data"]["volume"]], self.region_id, self.project_id)
        )

    def create_disk(self, **kwargs):
        """
        创建云盘(若传入虚拟机id，可创建并挂载到云主机)
        :param kwargs:
        :return:
        """
        request = CreateVolumeRequest()
        bss_param = BssParamForCreateVolume(
            charging_mode=kwargs.get("chargingMode", "postPaid"),
            period_num=kwargs.get("periodNum"),
            period_type=kwargs.get("period_type"),
        )
        volume = CreateVolumeOption(
            availability_zone=kwargs["availability_zone"],
            backup_id=kwargs.get("backup_id"),
            count=kwargs.get("count"),
            description=kwargs.get("description"),
            enterprise_project_id=kwargs.get("enterprise_project_id"),
            image_ref=kwargs.get("image_ref"),
            multiattach=kwargs.get("multiattach"),
            name=kwargs.get("name"),
            size=kwargs.get("size"),
            snapshot_id=kwargs.get("snapshot_id"),
            volume_type=kwargs["volume_type"],
        )
        request.body = CreateVolumeRequestBody(
            server_id=kwargs.get("server_id"),
            volume=volume,
            bss_param=bss_param,
        )

        @exception_handler
        def create_volume():
            return self.get_client(EvsClient, EvsRegion).create_volume(request)

        response = create_volume
        if not response["result"]:
            logger.error(response["message"])
            return fail("磁盘创建失败")
        return success(response["data"]["volume_ids"])

    def destroy_disk(self, disk_id):
        """
        释放云盘
        :param disk_id: disk_id
        :return:
        """
        request = DeleteVolumeRequest()
        request.volume_id = disk_id

        @exception_handler
        def delete_volume():
            return self.get_client(EvsClient, EvsRegion).delete_volume(request)

        response = delete_volume
        if not response["result"]:
            logger.error(response["message"])
            return fail("磁盘删除失败")
        return self.get_job_result(ShowEvsJobRequest(), response["data"]["job_id"], EvsClient, EvsRegion)

    def attach_disk(self, **kwargs):
        """
        云盘绑定主机
        :param kwargs:
                disk_id: 磁盘id
                vm_id: 虚拟机id
        :return:
        """
        request = AttachServerVolumeRequest()
        request.server_id = kwargs["InstanceId"]
        volume_attachment = AttachServerVolumeOption(volume_id=kwargs["volume"])
        request.body = AttachServerVolumeRequestBody(volume_attachment=volume_attachment)

        @exception_handler
        def attach_server_volume():
            return self.get_client(EcsClient, EcsRegion).attach_server_volume(request)

        response = attach_server_volume
        if not response["result"]:
            logger.error(response["message"])
            return fail("磁盘挂载失败")
        return self.get_job_result(ShowEcsJobRequest(), response["data"]["job_id"], EcsClient, EcsRegion)

    def detach_disk(self, **kwargs):
        """
        云盘解绑主机
        :param kwargs:
                disk_id: 磁盘id
                vm_id: 虚拟机id
        :return:
        """
        request = DetachServerVolumeRequest()
        request.volume_id = kwargs["volume"]
        request.delete_flag = "0"
        request.server_id = kwargs["InstanceId"]

        @exception_handler
        def detach_server_volume():
            return self.get_client(EcsClient, EcsRegion).detach_server_volume(request)

        response = detach_server_volume
        if not response["result"]:
            logger.error(response["message"])
            return fail("磁盘卸载失败")
        return self.get_job_result(ShowEcsJobRequest(), response["data"]["job_id"], EcsClient, EcsRegion)

    def resize_disk(self, **kwargs):
        """
        扩容云盘
        :param kwargs:
            disk_id (str): 磁盘id.    (required)
            size (int): 磁盘新容量.    (required)
        :return:
        """
        request = ResizeVolumeRequest()
        request.volume_id = kwargs["disk_id"]
        os_extend = OsExtend(new_size=kwargs["size"])
        bss_param = BssParamForResizeVolume(is_auto_pay="false")
        request.body = ResizeVolumeRequestBody(os_extend=os_extend, bss_param=bss_param)

        @exception_handler
        def resize_volume():
            return self.get_client(EvsClient, EvsRegion).resize_volume(request)

        response = resize_volume
        if not response["result"]:
            logger.error(response["message"])
            return fail("磁盘扩容失败")
        return self.get_job_result(ShowEvsJobRequest(), response["data"]["job_id"], EvsClient, EvsRegion)

    # ---------------对象存储-----------------------

    def list_buckets(self):
        """
        获取桶列表
        :return:
        """
        try:
            resp = self.obs_client.listBuckets()
            resp = resp["body"]["buckets"]
            policy = ""
            data = []
            for i in resp:
                bucket_name = i.get("name", "")
                marker = None
                list_objects = []
                while True:
                    body = self.obs_client.listObjects(
                        bucket_name, marker=marker
                    )["body"]
                    page_objects = body.get("contents") or []
                    list_objects.extend(page_objects)
                    is_truncated = bool(
                        body.get("isTruncated") or body.get("is_truncated")
                    )
                    if not is_truncated:
                        break
                    next_marker = body.get("nextMarker") or body.get("next_marker")
                    if not next_marker or next_marker == marker:
                        break
                    marker = next_marker
                object_num = len(list_objects)
                bucket_type = (
                    list_objects[0].get("storageClass", "") if list_objects else ""
                )
                capacity = sum(j.get("size", 0) or 0 for j in list_objects)
                capacity = round(float(capacity) / 1024 / 1024, 2)
                i["object_num"] = object_num
                i["capacity"] = capacity
                i["bucket_type"] = bucket_type
                i["policy"] = policy
                data.append(i)
            return success(format_resource(CloudResourceType.BUCKET.value, data, self.region_id, self.project_id))
        except Exception as e:
            logger.exception(e)
            return fail("存储通列表获取失败")

    # ----------------快照-------------------------

    def list_snapshots(self, ids="", **kwargs):
        """
        查询云硬盘快照详细列表信息
        :param resource_id:
        :param kwargs:
                    offset：类型：integer。描述：偏移量。 说明:分页查询快照时使用,与limit配合使用。假如共有30个快照,设置offset为11,
                limit为10,即为从第12个快照开始查询,一次最多可读取10个快照。
                    limit：类型：integer。描述：返回结果个数限制,值为大于0的整数。默认值为1000。
                    name：类型：String。描述：云硬盘快照名称。最大支持255个字节。
                    status：类型：String。描述：云硬盘快照状态,具体请参见A.3 云硬盘快照状态。
                    volume_id：类型：String。描述：快照所属云硬盘的ID。
                    availability_zone：类型：String。描述：快照所属云硬盘的可用区。
                    id：类型：String。描述：指定快照id进行过滤。
        :return:
        """
        if ids:
            return self.get_snapshot_detail(ids[0])
        request = ListSnapshotsRequest()
        page_size = 50
        request.limit = page_size
        list_optional_params = [
            "name",
            "status",
            "volume_id",
            "availability_zone",
            "status",
            "dedicated_storage_name",
            "dedicated_storage_id",
            "dedicated_storage_id",
            "service_type",
        ]
        request = set_optional_params_huawei(list_optional_params, kwargs, request)

        @exception_handler
        def list_snapshots():
            return self.get_client(EvsClient, EvsRegion).list_snapshots(request)

        response = list_snapshots
        if not response["result"]:
            logger.error(response["message"])
            return fail("快照列表获取失败")
        data = response["data"]
        count = data["count"]
        page_num = count // page_size
        if page_num == 0:
            return success(
                format_resource(CloudResourceType.SNAPSHOT.value, data["snapshots"], self.region_id, self.project_id)
            )
        snapshot_list = data["snapshots"]
        for page in range(1, page_num):
            request.offset = page * page_size
            response = list_snapshots
            if not response["result"]:
                logger.error(response["message"])
                return fail("快照列表获取失败")
            snapshot_list += response["data"]["snapshots"]
        return success(
            format_resource(CloudResourceType.SNAPSHOT.value, snapshot_list, self.region_id, self.project_id)
        )

    def get_snapshot_detail(self, resource_id):
        """
        获取快照详情
        """
        request = ShowSnapshotRequest()
        request.snapshot_id = resource_id

        @exception_handler
        def show_snapshot():
            return self.get_client(EvsClient, EvsRegion).show_snapshot(request)

        response = show_snapshot
        if not response["result"]:
            logger.error(response["message"])
            return fail("快照详情获取失败")
        return success(
            format_resource(
                CloudResourceType.SNAPSHOT.value, [response["data"]["snapshot"]], self.region_id, self.project_id
            )
        )

    def delete_snapshot(self, snapshot_id):
        """
        删除云硬盘快照。
        :param snapshot_id: 类型：String。必选。描述：快照ID
        :return:
        """
        request = DeleteSnapshotRequest()
        request.snapshot_id = snapshot_id

        @exception_handler
        def delete_snapshot():
            return self.get_client(EvsClient, EvsRegion).delete_snapshot(request)

        response = delete_snapshot
        if not response["result"]:
            logger.error(response["message"])
            return fail("快照删除失败")
        return success("执行成功")

    def restore_snapshot(self, **kwargs):
        """
        将快照数据回滚到云硬盘。
        :param kwargs:
                snapshot_id：类型：String。必选。描述：快照id
                disk_id：类型：String。必选。描述：磁盘id。
        :return:
        """
        request = RollbackSnapshotRequest()
        request.snapshot_id = kwargs["snapshot_id"]
        rollback = RollbackSnapshotOption(name=kwargs.get("name", ""), volume_id=kwargs.get("disk_id"))
        request.body = RollbackSnapshotRequestBody(rollback=rollback)

        @exception_handler
        def rollback_snapshot():
            return self.get_client(EvsClient, EvsRegion).rollback_snapshot(request)

        response = rollback_snapshot
        if not response["result"]:
            logger.error(response["message"])
            return fail("快照回滚失败")
        return success("执行成功")

    def create_snapshot(self, **kwargs):
        """
        描述：创建snapshot
        """
        request = CreateSnapshotRequest()
        snapshotCreateSnapshotOption = CreateSnapshotOption(**kwargs["snapshot"])
        request.body = CreateSnapshotRequestBody(snapshot=snapshotCreateSnapshotOption)

        @exception_handler
        def create_snapshot():
            return self.get_client(EvsClient, EvsRegion).create_snapshot(request)

        response = create_snapshot
        if not response["result"]:
            logger.error(response["message"])
            return fail("snapshot创建失败")
        return success([response["data"]["snapshot"]["id"]])

    # ************************网络*****************************

    def list_vpcs(self, resource_id="", **kwargs):
        """
        查询VPC列表信息（未分页获取全部）
        :param kwargs resource_id: 类型：String, vpc_id
        :param kwargs:可选的查询参数.可用参数包括：
                    marker：类型：String。描述：分页查询起始的资源ID，为空时为查询第一页
                    limit：类型：Integer。描述：每页返回的个数
                    enterprise_project_id：类型：String。描述：企业项目ID。可以使用该字段过滤某个企业项目下的虚拟私有云。
                    id：类型：String。描述：虚拟私有云ID。
        :return:
        """
        if resource_id:
            return self.get_vpc_detail(resource_id)
        request = ListVpcsRequest()
        page_size = 1000
        request.limit = page_size

        @exception_handler
        def list_vpcs():
            return self.get_client(VpcClient, VpcRegion).list_vpcs(request)

        response = list_vpcs
        if not response["result"]:
            logger.error(response["message"])
            return fail("VPC列表获取失败")
        return success(
            format_resource(CloudResourceType.VPC.value, response["data"]["vpcs"], self.region_id, self.project_id)
        )

    def get_vpc_detail(self, resource_id):
        """
        获取vpc详情
        """
        request = ShowVpcRequest()
        request.vpc_id = resource_id

        @exception_handler
        def show_vpc():
            return self.get_client(VpcClient, VpcRegion).show_vpc(request)

        response = show_vpc
        if not response["result"]:
            logger.error(response["message"])
            return fail("VPC详情获取失败")
        return success(
            format_resource(CloudResourceType.VPC.value, [response["data"]["vpc"]], self.region_id, self.project_id)
        )

    def create_vpc(self, **kwargs):
        """
        创建VPC
        :param kwargs:可选的查询参数.可用参数包括：
                    cidr：类型：String。描述：虚拟私有云下可用子网的范围。取值范围:
                                    10.0.0.0/8 ~ 10.255.255.240/28
                                    172.16.0.0/12 ~ 172.31.255.240/28
                                    192.168.0.0/16 ~ 192.168.255.240/28 约束:必须是ipv4 cidr格式,例如:192.168.0.0/16
                    description：类型：String。描述：虚拟私有云的描述
                    enterprise_project_id：类型：String。描述：企业项目ID。可以使用该字段过滤某个企业项目下的虚拟私有云。
                    name：类型：String。描述：虚拟私有云名称。取值范围:0-64个字符,支持数字、字母、中文、_(下划线)、-(中划线)、.(点)
                约束:如果名称不为空,则同一个租户下的名称不能重复
        :return:
        """
        request = CreateVpcRequest()
        vpc = CreateVpcOption(
            cidr=kwargs["vpc_cidr"],
            name=kwargs["vpc_name"],
            description=kwargs.get("description", ""),
            # enterprise_project_id=kwargs.get("enterprise_project_id", ''),
        )
        request.body = CreateVpcRequestBody(vpc=vpc)

        @exception_handler
        def create_vpc():
            return self.get_client(VpcClient, VpcRegion).create_vpc(request)

        response = create_vpc
        if not response["result"]:
            logger.error(response["message"])
            return fail("VPC创建失败")
        return success([response["data"]["vpc"]["id"]])

    def delete_vpc(self, **kwargs):
        """
        删除vpc
        :param kwargs:
                vpc_id: vpc资源id
        :return:
        """
        request = DeleteVpcRequest()
        request.vpc_id = kwargs["vpc_id"]

        @exception_handler
        def delete_vpc():
            return self.get_client(VpcClient, VpcRegion).delete_vpc(request)

        response = delete_vpc
        if not response["result"]:
            logger.error(response["message"])
            return fail("VPC删除失败")
        return success("执行成功")

    def list_subnets(self, resource_id="", **kwargs):
        """
        查询子网信息(未分页获取全部)
        :param resource_id:
        :param kwargs:
        :return: switch_list
        """
        if resource_id:
            return self.get_subnet_detail(resource_id)
        request = ListSubnetsRequest()
        request.vpc_id = kwargs.get("vpc_id")

        @exception_handler
        def list_subnets():
            return self.get_client(VpcClient, VpcRegion).list_subnets(request)

        response = list_subnets
        if not response["result"]:
            logger.error(response["message"])
            return fail("子网列表获取失败")
        return success(
            format_resource(
                CloudResourceType.SUBNET.value, response["data"]["subnets"], self.region_id, self.project_id
            )
        )

    def get_subnet_detail(self, resource_id):
        """
        获取子网详情
        """
        request = ShowSubnetRequest()
        request.subnet_id = resource_id

        @exception_handler
        def show_subnet():
            return self.get_client(VpcClient, VpcRegion).show_subnet(request)

        response = show_subnet
        if not response["result"]:
            logger.error(response["message"])
            return fail("子网详情获取失败")
        return success(
            format_resource(
                CloudResourceType.SUBNET.value, [response["data"]["subnet"]], self.region_id, self.project_id
            )
        )

    def create_subnet(self, **kwargs):
        """
        创建子网。
        :param kwargs:可选的查询参数.可用参数包括：
                    name：类型：String。必选。描述：子网名称。取值范围:1-64个字符,支持数字、字母、中文、_(下划线)、-(中划线)、.(点)
                    cidr：类型：String。必选。描述：子网的网段。取值范围:必须在vpc对应cidr范围内约束:必须是cidr格式。掩码长度不能大于28
                    gateway_ip：类型：String。必选。描述：子网的网关。取值范围:子网网段中的IP地址。约束:必须是ip格式
                    vpc_id：类型：String。必选。描述：子网所在VPC标识
                    dhcp_enable：类型：boolean。描述：子网是否开启dhcp功能
                    primary_dns：类型：String。描述：子网dns服务器地址1
                    secondary_dns：类型：String。描述：子网dns服务器地址2
                    dnsList：类型：Array<String>。描述：子网dns服务器地址的集合;如果想使用两个以上dns服务器,请使用该字段
                    availability_zone：类型：String。描述：子网所在的可用分区标识
                    示例：
                    {
                        "subnet": {
                            "availability_zone": "aa-bb-cc",
                            "cidr": "192.168.20.0/24",
                            "dhcp_enable": true,
                            "dnsList": [
                                "114.xx.xx.114",
                                "114.xx.xx.115"
                            ],
                            "extra_dhcp_opts": [
                                {
                                    "opt_name": "ntp",
                                    "opt_value": "10.100.0.33,10.100.0.34"
                                }
                            ],
                            "gateway_ip": "192.168.20.1",
                            "ipv6_enable": "true",
                            "name": "subnet",
                            "primary_dns": "114.xx.xx.114",
                            "secondary_dns": "114.xx.xx.115",
                            "vpc_id": "3ec3b33f-ac1c-4630-ad1c-7dba1ed79d85"
                        }
                    }
        :return:
        """
        request = CreateSubnetRequest()
        extra_dhcp_opts = [
            ExtraDhcpOption(
                opt_name="ntp",
            )
        ]
        subnetCreateSubnetOption = CreateSubnetOption(
            name=kwargs["subnet_name"],
            description=kwargs.get("description"),
            cidr=kwargs["subnet_cidr"],
            vpc_id=kwargs["vpc_id"],
            gateway_ip=kwargs["gateway_ip"],
            availability_zone=kwargs["subnet_zone"],
            extra_dhcp_opts=extra_dhcp_opts,
        )
        request.body = CreateSubnetRequestBody(subnet=subnetCreateSubnetOption)

        @exception_handler
        def create_subnet():
            return self.get_client(VpcClient, VpcRegion).create_subnet(request)

        response = create_subnet
        if not response["result"]:
            logger.error(response["message"])
            return fail("子网创建失败")
        return success([response["data"]["subnet"]["id"]])

    def delete_subnet(self, **kwargs):
        """
        删除子网
        :param subnet_id:
        :return:
        """
        request = DeleteSubnetRequest()
        request.vpc_id = kwargs["vpc_id"]
        request.subnet_id = kwargs["subnet_id"]

        @exception_handler
        def delete_subnet():
            return self.get_client(VpcClient, VpcRegion).delete_subnet(request)

        response = delete_subnet
        if not response["result"]:
            logger.error(response["message"])
            return fail("子网删除失败")
        return success("执行成功")

    def list_route_tables(self, ids=None, **kwargs):
        """查询路由列表"""
        if ids:
            return self.get_route_table_spec(ids[0], **kwargs)
        request = ListRouteTablesRequest()
        list_optional_params = ["id", "vpc_id", "subnet_id"]
        request = set_optional_params_huawei(list_optional_params, kwargs, request)

        @exception_handler
        def list_route_tables():
            return self.get_client(VpcClient, VpcRegion).list_route_tables(request)

        response = list_route_tables
        if not response["result"]:
            logger.error(response["message"])
            return fail("查询路由列表失败")
        return success(
            format_resource(
                CloudResourceType.ROUTE_TABLE.value, response["data"]["routetables"], self.region_id, self.project_id
            )
        )

    def get_route_table_spec(self, rt_id, **kwargs):
        """查询路由表详情"""
        request = ShowRouteTableRequest()
        request.routetable_id = rt_id

        @exception_handler
        def show_route_table():
            return self.get_client(VpcClient, VpcRegion).show_route_table(request)

        response = show_route_table
        if not response["result"]:
            logger.error(response["message"])
            return fail("查询路由表详情")
        return success(
            format_resource(CloudResourceType.LOAD_BALANCER.value, [response["data"]], self.region_id, self.project_id)
        )

    def create_route_table(self, **kwargs):
        """创建路由表"""
        must_params = ["vpc_id"]
        check_required_params(must_params, kwargs)
        request = CreateRouteTableRequest()
        routetable = CreateRouteTableReq(**kwargs)
        request.body = CreateRoutetableReqBody(routetable)

        @exception_handler
        def create_route_table():
            return self.get_client(VpcClient, VpcRegion).create_route_table(request)

        response = create_route_table
        if not response["result"]:
            logger.error(response["message"])
            return fail("创建路由表失败")
        return success([response["data"]["routetable"]["id"]])

    def delete_route_table(self, rt_id):
        """删除路由表"""
        request = DeleteRouteTableRequest()
        request.routetable_id = rt_id

        @exception_handler
        def delete_route_table():
            return self.get_client(VpcClient, VpcRegion).delete_route_table(request)

        response = delete_route_table
        if not response["result"]:
            logger.error(response["message"])
            return fail("删除路由表失败")
        return success("执行成功")

    def modify_route_table(self, rt_id, **kwargs):
        """修改路由表属性"""
        request = UpdateRouteTableRequest()
        rt = UpdateRouteTableReq(**kwargs)
        request.body = UpdateRoutetableReqBody(routetable=rt)
        request.routetable_id = rt_id

        @exception_handler
        def modify_route_table():
            return self.get_client(VpcClient, VpcRegion).update_route_table(request)

        response = modify_route_table
        if not response["result"]:
            logger.error(response["message"])
            return fail("修改路由表属性失败")
        return success([response["data"]["routetable"]["id"]])

    # todo 目前写的路由是VPC路由与其它的公有云路由有差异,未写operate
    def create_route_entry(self, **kwargs):
        """新建路由策略"""
        must_params = ["destination", "nexthop", "type", "vpc_id"]
        check_required_params(must_params, kwargs)
        request = CreateVpcRouteRequest()
        route = CreateVpcRouteOption(**kwargs)
        request.body = CreateVpcRouteRequestBody(route=route)

        @exception_handler
        def create_route_entry():
            return self.get_client(VpcClient, VpcRegion).create_vpc_route(request)

        response = create_route_entry
        if not response["result"]:
            logger.error(response["message"])
            return fail("新建路由策略失败")
        return success([response["data"]["route"]["id"]])

    def delete_route_entry(self, route_id):
        """删除路由策略"""
        request = DeleteVpcRouteRequest()
        request.route_id = route_id

        @exception_handler
        def delete_vpc_route():
            return self.get_client(VpcClient, VpcRegion).delete_vpc_route(request)

        response = delete_vpc_route
        if not response["result"]:
            logger.error(response["message"])
            return fail("删除路由策略失败")
        return success("执行成功")

    def list_route_entrys(self, ids=None, **kwargs):
        """查询VPC路由列表"""
        if ids:
            return self.get_route_entry(ids[0])
        request = ListVpcRoutesRequest()
        list_optional_params = ["id", "type", "vpc_id", "destination", "tenant_id"]
        request = set_optional_params_huawei(list_optional_params, kwargs, request)

        @exception_handler
        def list_vpc_routes():
            return self.get_client(VpcClient, VpcRegion).list_vpc_routes(request)

        response = list_vpc_routes
        if not response["result"]:
            logger.error(response["message"])
            return fail("查询VPC路由列表失败")
        return success(format_resource("route", response["data"]["routes"], self.region_id, self.project_id))

    def get_route_entry(self, re_id):
        """查询VPC路由"""
        request = ShowVpcRouteRequest()
        request.route_id = re_id

        @exception_handler
        def show_vpc_route():
            return self.get_client(VpcClient, VpcRegion).show_vpc_route(request)

        response = show_vpc_route
        if not response["result"]:
            logger.error(response["message"])
            return fail("查询VPC路由失败")
        return success(
            format_resource(
                CloudResourceType.LOAD_BALANCER.value, [response["data"]["route"]], self.region_id, self.project_id
            )
        )

    def list_eips(self, resource_id="", **kwargs):
        """
        查询公网IP信息（未分页获取全部）
        :param resource_id:
        :param kwargs:
        :return: outip_list
        """
        if resource_id:
            return self.get_eip_detail(resource_id)
        request = ListPublicipsRequest()
        request.limit = int(kwargs.get("limit", 1000))
        request.ip_version = 4

        def list_publicips():
            @exception_handler
            def request_page():
                return self.get_client(EipClient, EipRegion).list_publicips(request)

            return request_page

        response = list_publicips()
        if not response["result"]:
            logger.error(response["message"])
            return fail("弹性公网IP列表获取失败")
        eip_list = list(response["data"]["publicips"])
        while len(response["data"]["publicips"]) == request.limit:
            next_marker = response["data"]["publicips"][-1].get("id")
            if not next_marker or next_marker == getattr(request, "marker", None):
                break
            request.marker = next_marker
            response = list_publicips()
            if not response["result"]:
                logger.error(response["message"])
                return fail("弹性公网IP列表获取失败")
            page_eips = response["data"]["publicips"]
            if not page_eips:
                break
            eip_list.extend(page_eips)
        data = []
        for eip in eip_list:
            res = self.supplement_eip_attr(eip)
            if not res["result"]:
                logger.error(res["message"])
                return fail("弹性公网参数补充失败")
            data.append(res["data"])
        return success(format_resource(CloudResourceType.EIP.value, data, self.region_id, self.project_id))

    def get_eip_detail(self, resource_id):
        """
        查询指定eip
        """
        request = ShowPublicipRequest()
        request.publicip_id = resource_id

        @exception_handler
        def show_publicip():
            return self.get_client(EipClient, EipRegion).show_publicip(request)

        response = show_publicip
        if not response["result"]:
            logger.error(response["message"])
            return fail("弹性公网IP详情获取失败")
        eip = response["data"]["publicip"]
        res = self.supplement_eip_attr(eip)
        if not res["result"]:
            logger.error(res["message"])
            return fail("弹性公网参数补充失败")
        eip = res["data"]
        return success(format_resource(CloudResourceType.EIP.value, [eip], self.region_id, self.project_id))

    def get_eip_bandwidth_id(self, resource_id):
        """
        查询指定eip的bandwidth_id
        """
        request = ShowPublicipRequest()
        request.publicip_id = resource_id

        @exception_handler
        def show_publicip():
            return self.get_client(EipClient, EipRegion).show_publicip(request)

        response = show_publicip
        if not response["result"]:
            logger.error(response["message"])
            return fail("弹性公网IP详情获取失败")
        return response["data"]["publicip"]["bandwidth_id"]

    def supplement_eip_attr(self, eip_obj):
        """
        补充弹性公网参数
        """
        bandwidth_detail = self.get_bandwidth_detail(eip_obj["bandwidth_id"])
        if not bandwidth_detail["result"]:
            logger.error(bandwidth_detail["message"])
            return fail("带宽详情获取失败")
        eip_obj["charge_type"] = bandwidth_detail["data"]["charge_mode"]
        return success(eip_obj)

    def get_bandwidth_detail(self, resource_id):
        """
        查询带宽详情
        """
        request = ShowBandwidthRequest()
        request.bandwidth_id = resource_id

        @exception_handler
        def show_bandwidth():
            return self.get_client(EipClient, EipRegion).show_bandwidth(request)

        response = show_bandwidth
        if not response["result"]:
            logger.error(response["message"])
            return fail("带宽详情获取失败")
        return success(response["data"]["bandwidth"])

    def create_eip(self, **kwargs):
        """
        申请弹性公网
        """
        kwargs["eip_info"]["charge_mode"] = "traffic"

        if kwargs["eip_info"]["charge_type"] == "PREPAID":
            return self.create_prepaid_publicip(**kwargs["eip_info"])
        else:
            return self.create_publicip(**kwargs["eip_info"])

    def create_publicip(self, **kwargs):
        """
        创建按需付费eip
        """
        request = CreatePublicipRequest()
        publicip = CreatePublicipOption(type=kwargs["public_ip_type"], ip_version=4)
        bandwidth = CreatePublicipBandwidthOption(
            charge_mode=kwargs["charge_mode"],
            name=kwargs["bandwidth_name"],
            share_type=kwargs["share_type"],
            size=kwargs["bandwidth_size"],
        )
        request.body = CreatePublicipRequestBody(publicip=publicip, bandwidth=bandwidth)

        @exception_handler
        def create_publicip():
            return self.get_client(EipClient, EipRegion).create_publicip(request)

        response = create_publicip

        if not response["result"]:
            logger.error(response["message"])
            return fail("弹性公网IP创建失败")
        return success([response["data"]["publicip"]["id"]])

    def create_prepaid_publicip(self, **kwargs):
        """
        创建包年包月eip
        """
        request = CreatePrePaidPublicipRequest()
        extend_param = CreatePrePaidPublicipExtendParamOption(
            charge_mode="prePaid",
            period_type=kwargs.get("period_type", "month"),  # 旧接口无“年”，需要添加
            period_num=kwargs["period"],
            is_auto_renew=True,
            is_auto_pay=True,
        )
        bandwidth = CreatePublicipBandwidthOption(
            charge_mode=kwargs["charge_mode"], name=kwargs["bandwidth_name"], share_type=kwargs["share_type"]
        )
        publicip = CreatePrePaidPublicipOption(type=kwargs["public_ip_type"], ip_version=4)
        request.body = CreatePrePaidPublicipRequestBody(
            extend_param=extend_param,
            bandwidth=bandwidth,
            publicip=publicip,
        )

        @exception_handler
        def create_pre_paid_publicip():
            return self.get_client(EipClient, EipRegion).create_pre_paid_publicip(request)

        response = create_pre_paid_publicip

        if not response["result"]:
            logger.error(response["message"])
            return fail("弹性公网IP创建失败")
        return success([response["data"]["publicip_id"]])

    def delete_eip(self, eip_id):
        """
        删除弹性公网IP。
        :param eip_id: 弹性公网Id
        :return:
        """
        request = DeletePublicipRequest()
        request.publicip_id = eip_id

        @exception_handler
        def delete_publicip():
            return self.get_client(EipClient, EipRegion).delete_publicip(request)

        response = delete_publicip

        if not response["result"]:
            logger.error(response["message"])
            return fail("弹性公网IP删除失败")
        return success("执行成功")

    def associate_address(self, **kwargs):
        """
        更新弹性公网IP,将弹性公网IP跟一个网卡解绑定
        :param public_ip:弹性公网IP
        :param kwargs:
                    vm_id: 虚拟机id,非必选，存在则表示绑定，不存在表示劫镖
                    eip_id：弹性公网IP的id，必选
        :return:
        """
        port_id = None
        if kwargs.get("instance_id"):
            res = self.list_vms([kwargs["instance_id"]])
            if not res["result"]:
                return fail("弹性公网ip绑定网卡失败")
            port_id = res["data"][0]["extra"]["port_id"]
        request = UpdatePublicipRequest()
        request.publicip_id = kwargs["eip_id"]
        # public_ip = UpdatePublicipOption()
        public_ip = {}
        if port_id:
            public_ip = UpdatePublicipOption(port_id=port_id)
        request.body = UpdatePublicipsRequestBody(publicip=public_ip)

        @exception_handler
        def update_publicip():
            return self.get_client(EipClient, EipRegion).update_publicip(request)

        response = update_publicip

        if not response["result"]:
            logger.error(response["message"])
            message = "弹性公网IP解绑失败"
            if port_id:
                message = "弹性公网IP绑定失败"
            return fail(message)
        return success("执行成功")

    def modify_eip_band_width(self, **kwargs):
        """
        更新带宽。
        :param kwargs:
                bandwidth_id: str , 带宽id
                size: int , 带宽大小
        :return:
        """

        request = UpdateBandwidthRequest()
        request.bandwidth_id = self.get_eip_bandwidth_id(kwargs["eip_id"])
        bandwidth = UpdateBandwidthOption(
            size=kwargs["bandwidth"],
        )
        request.body = UpdateBandwidthRequestBody(bandwidth=bandwidth)

        @exception_handler
        def update_bandwidth():
            return self.get_client(EipClient, EipRegion).update_bandwidth(request)

        response = update_bandwidth
        if not response["result"]:
            logger.error(response["message"])
            return fail("带宽更新失败")
        return success("执行成功")

    # -----------------安全组----------------------------

    def list_security_groups(self, resource_id="", **kwargs):
        """
        获取安全组信息
        :param resource_id:
        :param kwargs:
        :return: securitygroups_info_list
        """
        if resource_id:
            return self.get_security_group_detail(resource_id)
        request = ListSecurityGroupsRequest()
        request.limit = 1000
        request.vpc_id = kwargs.get("vpc_id")

        @exception_handler
        def list_security_groups():
            return self.get_client(VpcClient, VpcRegion).list_security_groups(request)

        response = list_security_groups
        if not response["result"]:
            logger.error(response["message"])
            return fail("安全组列表获取失败")
        return success(
            format_resource(
                CloudResourceType.SECURITY_GROUP.value,
                response["data"]["security_groups"],
                self.region_id,
                self.project_id,
            )
        )

    def get_security_group_detail(self, resource_id):
        """
        获取安全组详情
        """
        request = ShowSecurityGroupRequest()
        request.security_group_id = resource_id

        @exception_handler
        def show_security_group():
            return self.get_client(VpcClient, VpcRegion).show_security_group(request)

        response = show_security_group
        if not response["result"]:
            logger.error(response["message"])
            return fail("安全组详情获取失败")
        return success(
            format_resource(
                CloudResourceType.SECURITY_GROUP.value,
                [response["data"]["security_group"]],
                self.region_id,
                self.project_id,
            )
        )

    def create_security_group(self, **kwargs):
        """
        为弹性云服务器添加一个安全组。
        :param kwargs:
                    description：类型：String。必选。描述：安全组描述。
                    name：类型：String。必选。描述：安全组名称。
        :return:
        """
        request = CreateSecurityGroupRequest()
        security_group = CreateSecurityGroupOption(
            name=kwargs["name"],
            vpc_id=kwargs.get("vpc_id"),
        )
        request.body = CreateSecurityGroupRequestBody(security_group=security_group)

        @exception_handler
        def create_security_group():
            return self.get_client(VpcClient, VpcRegion).create_security_group(request)

        response = create_security_group
        if not response["result"]:
            logger.error(response["message"])
            return fail("安全组创建失败")
        return success([response["data"]["security_group"]["id"]])

    def delete_security_group(self, security_group_id):
        """
        删除安全组.
        :param security_group_id: 安全组ID。
        :return:
        """
        request = DeleteSecurityGroupRequest()
        request.security_group_id = security_group_id

        @exception_handler
        def delete_security_group():
            return self.get_client(VpcClient, VpcRegion).delete_security_group(request)

        response = delete_security_group
        if not response["result"]:
            logger.error(response["message"])
            return fail("安全组删除失败")
        return success("执行成功")

    def create_security_group_rule(self, **kwargs):
        """
        创建安全组规则
        """
        request = CreateSecurityGroupRuleRequest()
        security_group_rule = kwargs["security_group_rule"]
        security_group_rule = CreateSecurityGroupRuleOption(
            security_group_id=security_group_rule["security_group_id"],
            direction=security_group_rule["direction"],
            port_range_min=security_group_rule["port_range_min"],
            port_range_max=security_group_rule["port_range_max"],
            ethertype=security_group_rule.get("ethertype", "IPv4"),
            protocol=security_group_rule["protocol"],
            remote_ip_prefix=security_group_rule["remote_ip_prefix"]
            # remote_group_id=security_group_rule.get('remote_group_id', None),
        )
        request.body = CreateSecurityGroupRuleRequestBody(security_group_rule=security_group_rule)

        @exception_handler
        def create_security_group_rule():
            return self.get_client(VpcClient, VpcRegion).create_security_group_rule(request)

        response = create_security_group_rule
        if not response["result"]:
            logger.error(response["message"])
            return fail("安全组规则创建失败")
        return success([response["data"]["security_group_rule"]["id"]])

    def delete_security_group_rule(self, security_group_rule_id):
        """
        删除安全组规则。
        :param security_group_rule_id: 安全组规则ID
        :return:
        """
        request = DeleteSecurityGroupRuleRequest()
        request.security_group_rule_id = security_group_rule_id

        @exception_handler
        def delete_security_group_rule():
            return self.get_client(VpcClient, VpcRegion).delete_security_group_rule(request)

        response = delete_security_group_rule
        if not response["result"]:
            logger.error(response["message"])
            return fail("安全组规则删除失败")
        return success("执行成功")

    def list_security_group_rules(self, security_group_id):
        """
        查询安全组规则列表。(已修改)
        :param security_group_id： 安全组id
        :return:
        """
        request = ListSecurityGroupRulesRequest()
        request.limit = 1000
        if security_group_id:
            request.security_group_id = security_group_id

        @exception_handler
        def list_security_group_rules():
            return self.get_client(VpcClient, VpcRegion).list_security_group_rules(request)

        response = list_security_group_rules
        if not response["result"]:
            logger.error(response["message"])
            return fail("安全组规则列表获取失败")
        return success(
            format_resource(
                CloudResourceType.SECURITY_GROUP_RULE.value,
                response["data"]["security_group_rules"],
                self.region_id,
                self.project_id,
            )
        )

    def get_security_group_rule_detail(self, resource_id):
        """
        获取安全组规则详情
        """
        request = ShowSecurityGroupRuleRequest()
        request.security_group_rule_id = resource_id

        @exception_handler
        def show_security_group_rule():
            return self.get_client(VpcClient, VpcRegion).show_security_group_rule(request)

        response = show_security_group_rule
        if not response["result"]:
            logger.error(response["message"])
            return fail("安全组规则详情获取失败")
        return success(
            format_resource(
                CloudResourceType.SECURITY_GROUP_RULE.value,
                [response["data"]["security_group_rule"]],
                self.region_id,
                self.project_id,
            )
        )

    # *************************负载均衡*******************************
    def create_load_balancer(self, **kwargs):
        """创建负载均衡"""
        must_params = ["availability_zone_list"]
        check_required_params(must_params, kwargs)
        request = CreateLoadBalancerRequest()
        lb = CreateLoadBalancerOption(**kwargs)
        request.body = CreateLoadBalancerRequestBody(loadbalancer=lb)

        @exception_handler
        def create_load_balancer():
            return self.get_client(ElbClient, ElbRegion).create_load_balancer(request)

        response = create_load_balancer
        if not response["result"]:
            logger.error(response["message"])
            return fail("创建负载均衡失败")
        return success([response["data"]["loadbalancer"]["id"]])

    def delete_load_balancer(self, lb_id):
        """删除负载均衡"""
        request = DeleteLoadBalancerRequest()
        request.loadbalancer_id = lb_id

        @exception_handler
        def delete_load_balancer():
            return self.get_client(ElbClient, ElbRegion).delete_load_balancer(request)

        response = delete_load_balancer
        if not response["result"]:
            logger.error(response["message"])
            return fail("删除负载均衡失败")
        return success("执行成功")

    def modify_load_balancer(self, lb_id, **kwargs):
        """修改负载均衡实例的属性"""
        request = UpdateLoadBalancerRequest()
        lb = UpdateLoadBalancerOption(**kwargs)
        request.body = UpdateLoadBalancerRequestBody(loadbalancer=lb)
        request.loadbalancer_id = lb_id

        @exception_handler
        def modify_load_balancer():
            return self.get_client(ElbClient, ElbRegion).update_load_balancer(request)

        response = modify_load_balancer
        if not response["result"]:
            logger.error(response["message"])
            return fail("修改负载均衡实例失败")
        return success([response["data"]["loadbalancer"]["id"]])

    def list_load_balancers(self, ids=None, **kwargs):
        """查询负载均衡列表"""
        if ids:
            return self.get_load_balancer_spec(ids[0])
        request = ListLoadBalancersRequest()
        request.limit = int(kwargs.get("limit", 1000))
        list_optional_params = [
            "id",
            "name",
            "description",
            "provisioning_status",
            "operating_status",
            "guaranteed",
            "vpc_id",
            "vip_port_id",
            "vip_address",
            "vip_subnet_cidr_id",
            "pv6_vip_port_id",
            "eips",
            "publicips",
            "availability_zone_list",
            "l4_flavor_id",
            "l4_scale_flavor_id",
            "l7_flavor_id",
            "l7_scale_flavor_id",
            "billing_info",
            "member_device_id",
            "member_address",
            "enterprise_project_id",
            "ip_version",
            "deletion_protection_enable",
            "elb_virsubnet_type",
        ]
        request = set_optional_params_huawei(list_optional_params, kwargs, request)

        def list_load_balancers():
            @exception_handler
            def request_page():
                return self.get_client(ElbClient, ElbRegion).list_load_balancers(request)

            return request_page

        response = list_load_balancers()
        if not response["result"]:
            logger.error(response["message"])
            return fail("查询负载均衡列表失败")
        load_balancers = list(response["data"]["loadbalancers"])
        while len(response["data"]["loadbalancers"]) == request.limit:
            next_marker = response["data"]["loadbalancers"][-1].get("id")
            if not next_marker or next_marker == getattr(request, "marker", None):
                break
            request.marker = next_marker
            response = list_load_balancers()
            if not response["result"]:
                logger.error(response["message"])
                return fail("查询负载均衡列表失败")
            page_load_balancers = response["data"]["loadbalancers"]
            if not page_load_balancers:
                break
            load_balancers.extend(page_load_balancers)
        return success(
            format_resource(
                CloudResourceType.LOAD_BALANCER.value,
                load_balancers,
                self.region_id,
                self.project_id,
            )
        )

    def get_load_balancer_spec(self, lb_id):
        """查询负载均衡详情"""
        request = ShowLoadBalancerRequest()
        request.loadbalancer_id = lb_id

        @exception_handler
        def show_load_balancer():
            return self.get_client(ElbClient, ElbRegion).show_load_balancer(request)

        response = show_load_balancer
        if not response["result"]:
            logger.error(response["message"])
            return fail("查询负载均衡详情失败")
        return success(
            format_resource(
                CloudResourceType.LOAD_BALANCER.value,
                [response["data"]["loadbalancer"]],
                self.region_id,
                self.project_id,
            )
        )

    def create_backend_server(self, **kwargs):
        """创建后端服务器"""
        must_params = ["subnet_cidr_id", "protocol_port", "address"]
        check_required_params(must_params, kwargs)
        pool_id = kwargs.pop("pool_id")
        request = CreateMemberRequest()
        member = CreateMemberOption(**kwargs)
        request.pool_id = pool_id
        request.body = CreateMemberRequestBody(member=member)

        @exception_handler
        def create_member():
            return self.get_client(ElbClient, ElbRegion).create_member(request)

        response = create_member
        if not response["result"]:
            logger.error(response["message"])
            return fail("创建后端服务器失败")
        return success(response["data"]["member"]["id"])

    def delete_backend_server(self, pool_id, member_id):
        """删除后端服务器"""
        request = DeleteMemberRequest()
        request.pool_id = pool_id
        request.member_id = member_id

        @exception_handler
        def delete_member():
            return self.get_client(ElbClient, ElbRegion).delete_member(request)

        response = delete_member
        if not response["result"]:
            logger.error(response["message"])
            return fail("删除后端服务器")
        return success("执行成功")

    def list_listeners(self, ids=None, **kwargs):
        """查询监听器列表"""
        if ids:
            return self.get_listener_spec(ids[0])
        request = ListListenersRequest()
        list_optional_params = [
            "protocol_port",
            "protocol",
            "description",
            "default_tls_container_ref",
            "client_ca_tls_container_ref",
            "admin_state_up",
            "connection_limit",
            "default_pool_id",
            "id",
            "name",
            "default_tls_container_ref",
            "http2_enable",
            "http2_enable",
            "loadbalancer_id",
            "tls_ciphers_policy",
            "member_address",
            "member_device_id",
            "enterprise_project_id",
            "enable_member_retry",
            "enable_member_retry",
            "member_timeout",
        ]
        request = set_optional_params_huawei(list_optional_params, kwargs, request)

        @exception_handler
        def list_listeners():
            return self.get_client(ElbClient, ElbRegion).list_listeners(request)

        response = list_listeners
        if not response["result"]:
            logger.error(response["message"])
            return fail("查询监听器列表")
        return success(
            format_resource(
                CloudResourceType.LISTENER.value, response["data"]["listeners"], self.region_id, self.project_id
            )
        )

    def get_listener_spec(self, ls_id):
        """查询监听器详情"""
        request = ShowListenerRequest()
        request.listener_id = ls_id

        @exception_handler
        def show_listener():
            return self.get_client(ElbClient, ElbRegion).show_listener(request)

        response = show_listener
        if not response["result"]:
            logger.error(response["message"])
            return fail("查询监听器详情失败")
        return success(
            format_resource(
                CloudResourceType.LISTENER.value, [response["data"]["listener"]], self.region_id, self.project_id
            )
        )

    def create_listener(self, **kwargs):
        """创建负载均衡监听器"""
        must_params = ["loadbalancer_id", "protocol", "protocol_port"]
        check_required_params(must_params, kwargs)
        request = CreateListenerRequest()
        listener = CreateListenerOption(**kwargs)
        request.body = CreateListenerRequestBody(listener=listener)

        @exception_handler
        def create_listener():
            return self.get_client(ElbClient, ElbRegion).create_listener(request)

        response = create_listener
        if not response["result"]:
            logger.error(response["message"])
            return fail("创建负载均衡监听器失败")
        return success(response["data"]["listener"]["id"])

    def delete_listener(self, listener_id):
        """删除监听器"""
        request = DeleteListenerRequest()
        request.listener_id = listener_id

        @exception_handler
        def delete_listener():
            return self.get_client(ElbClient, ElbRegion).delete_listener(request)

        response = delete_listener
        if not response["result"]:
            logger.error(response["message"])
            return fail("删除监听器失败")
        return success("执行成功")

    def modify_listener(self, listener_id, **kwargs):
        """修改监听器属性"""
        request = UpdateListenerRequest()
        listener = UpdateListenerOption(**kwargs)
        request.body = UpdateListenerRequestBody(listener=listener)
        request.listener_id = listener_id

        @exception_handler
        def modify_listener():
            return self.get_client(ElbClient, ElbRegion).update_listener(request)

        response = modify_listener
        if not response["result"]:
            logger.error(response["message"])
            return fail("修改监听器属性失败")
        return success([response["data"]["listener"]["id"]])

    def list_vserver_groups(self, load_balancer_id=None, **kwargs):
        """查询后端服务器组列表"""
        if load_balancer_id:
            kwargs["loadbalancer_id"] = [load_balancer_id]
        request = ListPoolsRequest()
        list_optional_params = [
            "description",
            "admin_state_up",
            "healthmonitor_id",
            "id",
            "name",
            "loadbalancer_id",
            "protocol",
            "lb_algorithm",
            "enterprise_project_id",
            "ip_version",
            "member_address",
            "member_device_id",
            "member_deletion_protection_enable",
            "listener_id",
            "member_instance_id",
        ]
        request = set_optional_params_huawei(list_optional_params, kwargs, request)

        @exception_handler
        def list_backend_server_groups():
            return self.get_client(ElbClient, ElbRegion).list_pools(request)

        response = list_backend_server_groups
        if not response["result"]:
            logger.error(response["message"])
            return fail("查询后端服务器组列表")
        return success(
            format_resource(
                CloudResourceType.BACKEND_SECURITY_GROUP.value,
                response["data"]["pools"],
                self.region_id,
                self.project_id,
            )
        )

    def get_vserver_group(self, bsg_id):
        """查询后端服务器组详情"""
        request = ShowPoolRequest()
        request.pool_id = bsg_id

        @exception_handler
        def show_pool():
            return self.get_client(ElbClient, ElbRegion).show_pool(request)

        response = show_pool
        if not response["result"]:
            logger.error(response["message"])
            return fail("查询后端服务器组详情失败")
        return success(
            format_resource(
                CloudResourceType.BACKEND_SECURITY_GROUP.value,
                [response["data"]["pool"]],
                self.region_id,
                self.project_id,
            )
        )

    def create_backend_server_group(self, **kwargs):
        """创建后端服务器组"""
        must_params = ["lb_algorithm", "protocol"]
        check_required_params(must_params, kwargs)
        request = CreatePoolRequest()
        pool = CreatePoolOption(**kwargs)
        request.body = CreatePoolRequestBody(pool=pool)

        @exception_handler
        def create_pool():
            return self.get_client(ElbClient, ElbRegion).create_pool(request)

        response = create_pool
        if not response["result"]:
            logger.error(response["message"])
            return fail("创建后端服务器组失败")
        return success(response["data"]["pool"]["id"])

    def delete_backend_server_group(self, bsg_id):
        """删除后端服务器组"""
        request = DeletePoolRequest()
        request.pool_id = bsg_id

        @exception_handler
        def delete_pool():
            return self.get_client(ElbClient, ElbRegion).delete_pool(request)

        response = delete_pool
        if not response["result"]:
            logger.error(response["message"])
            return fail("删除后端服务器组失败")
        return success("执行成功")

    def modify_backend_server_group(self, bsg_id, **kwargs):
        """更新后端服务器组"""
        request = UpdatePoolRequest()
        pool = UpdatePoolOption(**kwargs)
        request.body = UpdatePoolRequestBody(pool=pool)
        request.pool_id = bsg_id

        @exception_handler
        def modify_pool():
            return self.get_client(ElbClient, ElbRegion).update_pool(request)

        response = modify_pool
        if not response["result"]:
            logger.error(response["message"])
            return fail("更新后端服务器组失败")
        return success([response["data"]["pool"]["id"]])

    # ------------------监控-------------------------

    def monitor_data(self, **kwargs):
        """
        查询监控信息
        :param kwargs:
        :return: monitor_list
        """
        request = ShowMetricDataRequest()
        request.namespace = kwargs.get("namespace", "AGT.ECS")
        request.metric_name = kwargs.get("MetricName", "cpu_usage")
        # 支持多维度的获取,例如磁盘相关指标
        for index, dimension in enumerate(kwargs["dimensions"]):
            setattr(request, f"dim_{index}", dimension)
        request.filter = "average"
        request._from = kwargs["StartTime"]
        request.to = kwargs["EndTime"]
        request.period = int(kwargs.get("Period", 300))

        @exception_handler
        def show_metric_data():
            return self.get_client(CesClient, CesRegion).show_metric_data(request)

        response = show_metric_data
        if not response["result"]:
            logger.error(response["message"])
            return fail("监控数据获取失败")
        return_data = response["data"]["datapoints"]
        monitor_list = [[i["timestamp"] // 1000, round(i["average"], 3)] for i in return_data if i]
        return success(monitor_list)

    @staticmethod
    def split_list(_list, count=100):
        n = len(_list)
        sublists = [_list[i : i + count] for i in range(0, n, count)]
        return sublists

    def batch_query_monitor_data(self, metrics, **kwargs):
        """
        批量查询监控信息
        :param kwargs:
        :return: monitor_list
        """
        all_dimension_mapping = kwargs.get("all_dimension_mapping", {})
        request = BatchListMetricDataRequest()
        batch_metrics = self.split_list(metrics, 500)
        metric_data = {}
        for _index, _metrics in enumerate(batch_metrics):
            request.body = {
                "metrics": metrics,
                "filter": "average",
                "from": kwargs["StartTime"],
                "to": kwargs["EndTime"],
                "period": str(kwargs.get("Period", 300)),
            }

            @exception_handler
            def batch_list_metric_data():
                return self.get_client(CesClient, CesRegion).batch_list_metric_data(request)

            response = batch_list_metric_data
            if not response["result"]:
                logger.error(response["message"])
                return fail("监控数据获取失败")
            metrics_return_data = response["data"]["metrics"]

            for req, resp in zip(_metrics, metrics_return_data):
                instance_id = ""
                _dimensions = []
                metric_name = req["metric_name"]
                for i in req["dimensions"]:
                    if i["name"] == "instance_id":
                        instance_id = i["value"]
                    else:
                        _dimensions.append((i["name"], all_dimension_mapping.get(i["value"], "")))
                monitor_value = [[i["timestamp"], round(i["average"], 3)] for i in resp["datapoints"] if i]
                # if metric_name in ["cpu_usage", "disk_ioUtils"]:
                #     monitor_value = [[i[0], round(i[1] * 100, 2)] for i in monitor_value]
                if _dimensions:
                    metric_data.setdefault(instance_id, {}).setdefault(metric_name, {})[
                        tuple(_dimensions)
                    ] = monitor_value
                else:
                    metric_data.setdefault(instance_id, {}).setdefault(metric_name, []).extend(monitor_value)
        return metric_data

    def get_monitor_data(self, **kwargs):
        """
        查询CPU、内存、系统盘监控
        :param kwargs:
                start_time: 开始时间
                end_time：结束时间
                period：获取数据的单位时间间隔
                resource_id： 虚拟机id
        :return: monitor_data
        """
        monitor_data = {}
        start_time = time.strptime(kwargs["StartTime"], "%Y-%m-%d %H:%M:%S")
        start_time = int(time.mktime(start_time)) * 1000
        end_time = time.strptime(kwargs["EndTime"], "%Y-%m-%d %H:%M:%S")
        end_time = int(time.mktime(end_time)) * 1000
        data = {"Period": kwargs.get("period", "300"), "StartTime": start_time, "EndTime": end_time}
        resource_id = kwargs.get("resourceId", "")
        vm_list = set(resource_id.split(","))
        for i in vm_list:
            data["dimensions"] = f"instance_id,{i}"
            monitor_data[i] = {"cpu_data": [], "memory_data": [], "disk_data": []}
            data["MetricName"] = "cpu_usage"
            # data["MetricName"] = "cpu_util"
            cpu_monitor_data = self.monitor_data(**data)
            if not cpu_monitor_data["result"]:
                return fail("cpu监控信息获取失败")
            monitor_data[i]["cpu_data"].extend(cpu_monitor_data["data"])
            data["MetricName"] = "mem_usedPercent"
            # data["MetricName"] = "mem_util"
            disk_monitor_data = self.monitor_data(**data)
            if not disk_monitor_data["result"]:
                return fail("内存监控信息获取失败")
            monitor_data[i]["memory_data"].extend(disk_monitor_data["data"])
        return success(monitor_data)

    def get_load_monitor_data(self, **kwargs):
        """
        查询CPU、内存、系统盘监控
        :param kwargs:
                start_time: 开始时间
                end_time：结束时间
                period：获取数据的单位时间间隔
                resource_id： 虚拟机id
        :return: monitor_data
        """
        monitor_data = {}
        # start_time = time.strptime(kwargs['StartTime'], "%Y-%m-%d %H:%M:%S")
        start_time = int(kwargs["StartTime"].timestamp()) * 1000
        # end_time = time.strptime(kwargs['EndTime'], "%Y-%m-%d %H:%M:%S")
        end_time = int(kwargs["EndTime"].timestamp()) * 1000
        data = {"Period": kwargs.get("period", 300), "StartTime": start_time, "EndTime": end_time}
        vm_list = list(set(kwargs["resourceId"]))
        for i in vm_list:
            #  todo 磁盘相关得有维度d1
            data["dimensions"] = "instance_id,{}".format(i)
            monitor_data[i] = {"cpu_data": [], "memory_data": [], "load_data": []}

            data["MetricName"] = "cpu_usage"
            cpu_monitor_data = self.monitor_data(**data)
            if not cpu_monitor_data["result"]:
                return fail("cpu监控信息获取失败")
            monitor_data[i]["cpu_data"].extend([float(m[1]) for m in cpu_monitor_data["data"] if m])

            data["MetricName"] = "mem_usedPercent"
            mem_monitor_data = self.monitor_data(**data)
            if not mem_monitor_data["result"]:
                return fail("内存监控信息获取失败")
            monitor_data[i]["memory_data"].extend([float(m[1]) for m in mem_monitor_data["data"] if m])

            data["MetricName"] = "load_average5"
            load_monitor_data = self.monitor_data(**data)
            if not load_monitor_data["result"]:
                return fail("5分钟负载监控信息获取失败")
            monitor_data[i]["load_data"].extend([float(m[1]) for m in load_monitor_data["data"] if m])

        return success(monitor_data)

    def get_metric_dims(self, instance_id, dim_name):
        request = ListAgentDimensionInfoRequest()
        request.instance_id = instance_id
        request.dim_name = dim_name

        @exception_handler
        def list_agent_dimension_info():
            return self.get_client(CesClient_V2, CesRegion_V2).list_agent_dimension_info(request)

        response = list_agent_dimension_info
        if not response["result"]:
            logger.error(response["message"])
            return fail("获取维度失败")
        return response["data"]["dimensions"]

    def get_weops_monitor_data(self, **kwargs):
        """
        获取weops监控
        :param kwargs:
            start_time: 开始时间
            end_time：结束时间
            period：获取数据的单位时间间隔
            period：获取数据的单位时间间隔
            resource_id： 虚拟机id
        :return: monitor_data
        """
        monitor_data = {}
        start_time = time.strptime(kwargs["StartTime"], "%Y-%m-%d %H:%M:%S")
        start_time = int(time.mktime(start_time)) * 1000
        end_time = time.strptime(kwargs["EndTime"], "%Y-%m-%d %H:%M:%S")
        end_time = int(time.mktime(end_time)) * 1000
        data = {"Period": kwargs.get("period", "300"), "StartTime": start_time, "EndTime": end_time}
        resource_id = kwargs.get("resourceId", "")
        metrics = kwargs.get(
            "Metrics",
            [
                "cpu_usage",
                "disk_usedPercent",
                "disk_free",
                "mem_usedPercent",
                "disk_ioUtils",
                "net_bitSent",
                "net_bitRecv",
                "net_packetRecv",
                "net_packetSent",
            ],
        )

        vm_list = set(resource_id.split(","))
        all_dimension_mapping = {}
        metric_list = []
        for vm_id in vm_list:
            for metric in metrics:
                metric_data = {}
                metric_data["namespace"] = "AGT.ECS"
                metric_data["metric_name"] = metric
                metric_data["dimensions"] = [{"name": "instance_id", "value": vm_id}]
                # 如果是磁盘相关,获取其维度
                if metric in ["disk_usedPercent", "disk_free", "disk_ioUtils"]:
                    dim_name = "mount_point"
                    dims = self.get_metric_dims(vm_id, dim_name)
                    for dim in dims:
                        dim_metric_data = copy.deepcopy(metric_data)
                        all_dimension_mapping[dim["value"]] = dim["origin_value"]

                        dim_metric_data["dimensions"].append({"name": dim_name, "value": dim["value"]})
                        metric_list.append(dim_metric_data)

                else:
                    metric_list.append(metric_data)

        monitor_data = self.batch_query_monitor_data(metric_list, **data, all_dimension_mapping=all_dimension_mapping)
        return success(monitor_data)

    # ************************费用***********************************

    def get_realcost(self, **kwargs):
        """
        未使用SDK，待官方完善SDK后换用
        查询实际费用
        :param kwargs:
                billing_cycle: 获取月份的账单
        :return: cost_list
        """
        month = kwargs["BillingCycle"]
        date = kwargs["BillingDate"]
        # region_id = self.region_id
        data_list = []
        ins_list = []
        charge_mode = ["1", "3", "10"]
        request = ListCustomerselfResourceRecordsRequest()
        for m in charge_mode:
            request.cycle = month
            # request.region = region_id
            request.offset = 1
            request.limit = 100  # 最大1000，默认10
            request.charge_mode = m
            request.bill_date_begin = date
            request.bill_date_end = date
            request.include_zero_record = False

            @exception_handler
            def list_customerself_resource_records():
                return self.get_client(
                    BssClient, BssRegion, credentials=self.global_credentials, region_id="cn-north-1"
                ).list_customerself_resource_records(request)

            response = list_customerself_resource_records
            if not response["result"]:
                logger.error(response["message"])
                return fail("资源消费记录查询失败")
            huawei_result = response["data"]
            total_count = huawei_result["total_count"]
            data_list.extend(huawei_result["fee_records"])
            page = total_count // 100 if total_count // 100 == 0 else total_count // 100 + 1
            for p in range(page):
                request.offset = int(p + 2)
                response = list_customerself_resource_records
                if not response["result"]:
                    logger.error(response["message"])
                    return fail("资源消费记录查询失败")
                huawei_result = response["data"]
                data_list.extend(huawei_result["fee_records"])
        measure_id = {1: 1, 2: 10, 3: 100}
        resource_id_list = []
        for item in data_list:
            # if float(item["cash_amount"]) > 0.0:
            if item.get("resource_id") not in resource_id_list:
                resource_id_list.append(item.get("resource_id"))
                original_price = float(item["official_amount"]) / measure_id[item["measure_id"]]
                current_price = float(item["cash_amount"]) / measure_id[item["measure_id"]]
                discount = float(item["discount_amount"]) / measure_id[item["measure_id"]]
                ins_list.append(
                    {
                        "serial_number": generate_serial_number(current_price),
                        "resource_id": item.get("resource_id"),
                        "resource_name": item.get("resource_name"),
                        # "resource_type": item.get("product_name"),
                        "resource_type": format_public_cloud_resource_type("HuaweiCloud", item.get("resource_type"))
                        or "",
                        "mode": format_huawei_bill_charge_mode(item.get("charge_mode")),
                        # "product_name": item.get("ProductName"),
                        "product_detail": item["product_spec_desc"],
                        "original_price": round(original_price, 5),
                        "discount": round(discount, 5),
                        "current_price": round(current_price, 5),
                        # "result_time_month": month,
                        "result_time": item.get("bill_date"),
                    }
                    # {
                    #     "resource_id": item.get("resource_id"),
                    #     "resource_name": item.get("resource_name"),
                    #     "resource_type": item.get("product_name"),
                    #     "cost": float(item["cash_amount"]) / measure_id[item["measure_id"]],
                    #     "cost_time": month,
                    # }
                )
            else:
                index = resource_id_list.index(item.get("resource_id"))
                cost = float(item["cash_amount"]) / measure_id[item["measure_id"]]
                original_cost = float(item["official_amount"]) / measure_id[item["measure_id"]]
                discount_cost = float(item["discount_amount"]) / measure_id[item["measure_id"]]
                ins_list[index]["current_price"] = round(ins_list[index]["current_price"] + cost, 5)
                ins_list[index]["original_price"] = round(ins_list[index]["original_price"] + original_cost, 5)
                ins_list[index]["discount"] = round(ins_list[index]["discount"] + discount_cost, 5)
        return {"result": True, "data": ins_list}

    def get_virtual_cost(self, **kwargs):
        """
        计算虚拟成本
        :param kwargs:
        :return: cost_list
        """
        res = self.list_vms()
        if not res["result"]:
            logger.error(res["message"])
            return fail("虚拟机列表信息获取失败")
        vm_list = res["data"]
        return_data = []
        vm_zone_dict = {}
        zone_list = []
        for vm in vm_list:
            if vm["zone"] not in vm_zone_dict:
                vm_zone_dict[vm["zone"]] = []
                zone_list.append(vm["zone"])
            vm_zone_dict[vm["zone"]].append(vm)
        for zone in zone_list:
            pricemodule, spec_list = get_compute_price_module(
                CloudPlatform.HuaweiCloud, kwargs["account_name"], self.region_id, zone
            )
            spec_set_list = [spec[4] for spec in spec_list]
            spec_price_list = [spec[3] for spec in spec_list]
            spec_memory_list = [spec[2] for spec in spec_list]
            spec_cpu_list = [spec[1] for spec in spec_list]
            for i in vm_zone_dict[zone]:
                ins_memory = 0
                ins_cpu = 0
                ins_spec = i["instance_type"]
                if ins_spec in spec_set_list:
                    price_vm = spec_price_list[spec_set_list.index(ins_spec)]
                    ins_memory = spec_memory_list[spec_set_list.index(ins_spec)]
                    ins_cpu = spec_cpu_list[spec_set_list.index(ins_spec)]
                else:
                    price_vm = 0
                volume_list = []
                volume_id_list = i["data_disk"] + [i["system_disk"]["id"]]
                for volume_id in volume_id_list:
                    volume_data = self.get_disk_detail(volume_id)
                    if not volume_data["result"]:
                        logger.error(volume_data["message"])
                        return fail("获取磁盘信息失败")
                    volume_list.extend(volume_data["data"])
                price_disk = 0
                for disk in volume_list:
                    module, storage_list = get_storage_pricemodule(
                        CloudPlatform.HuaweiCloud, kwargs["account_name"], self.region_id, zone, disk["category"]
                    )
                    if module:
                        price_disk += storage_list[1] * disk.disk_size
                    else:
                        price_disk += 0
                return_data.append(
                    {
                        "resourceId": i["resource_id"],
                        "name": i["resource_name"],
                        "cpu": ins_cpu,
                        "mem": ins_memory,
                        "cost_all": round((float(price_vm)), 2) + round((float(price_disk)), 2),
                        "cost_vm": round((float(price_vm)), 2),
                        "cost_disk": round((float(price_disk)), 2),
                        "cost_net": 0.0,
                        "cost_time": datetime.datetime.now().strftime("%Y-%m-%d"),
                        "source_type": CloudResourceType.VM.value,
                    }
                )
        return success(return_data)

    def query_account_balance(self):
        """
        查询账户可用余额
        """
        request = ShowCustomerAccountBalancesRequest()

        @exception_handler
        def show_customer_account_balances():
            kwargs = {"credentials": self.global_credentials}
            try:
                return self.get_client(
                    BssClient, BssRegion, credentials=self.global_credentials
                ).show_customer_account_balances(request)
            except Exception:
                client = (
                    BssClient.new_builder()
                    .with_http_config(self.config)
                    .with_credentials(kwargs.get("credentials", self.basic_credentials))
                    .with_region(BssRegion.value_of("cn-north-1"))
                    .build()
                )

                return client.show_customer_account_balances(request)

        response = show_customer_account_balances
        if not response["result"]:
            logger.error(response["message"])
            return fail("查询账户余额失败")
        region_list = response["data"]
        amount = 0
        for i in region_list["account_balances"]:
            if i["account_type"] != 1:
                continue
            if i["measure_id"] == 1:
                amount += i["amount"]
            if i["measure_id"] == 2:
                amount += i["amount"] / 10
            if i["measure_id"] == 3:
                amount += i["amount"] / 100
        data = {
            "amount": amount,
            "currency": "CNY",
            "unit": "元",
        }
        return success(data)

    def query_account_transactions(self):
        """
        查询消费详情
        """
        request = ShowCustomerMonthlySumRequest()
        data = []
        now = datetime.datetime.now()
        year = now.year
        month = now.month
        for i in range(11):
            if month < 10:
                cycle = str(year) + "-0" + str(month)
            else:
                cycle = str(year) + "-" + str(month)
            request.bill_cycle = cycle
            request.limit = 1000

            @exception_handler
            def show_customer_monthly_sum():
                return self.get_client(
                    BssClient, BssRegion, credentials=self.global_credentials
                ).show_customer_monthly_sum(request)

            response = show_customer_monthly_sum
            if not response["result"]:
                logger.error(response["message"])
                return fail("月份汇总账单查询失败")
            region_list = response["data"]
            for j in region_list["bill_sums"]:
                data.append(
                    {
                        "amount": j["consume_amount"],
                        "transaction_time": j["bill_cycle"],
                        "currency": region_list["currency"],
                        "unit": "元",
                    }
                )
            if month > 1:
                month = month - 1
            else:
                month = 12
                year = year - 1
        return success(data)

    # ***************************  规格 *******************************
    def get_disk_spec(self):
        """
        获取华为云硬盘规格
        Returns
        -------

        """
        request = CinderListVolumeTypesRequest()
        client = self.get_client(EvsClient, EvsRegion)
        try:
            response = client.cinder_list_volume_types(request)
        except Exception as e:
            logger.exception("调用华为云获取磁盘规格接口失败{}".format(e))
            return {"result": False, "message": str(e)}
        data = [
            {"label": huaweicloud_disk_cn_dict.get(spec["name"], spec["name"]), "value": spec["name"]}
            for spec in response.to_dict()["volume_types"]
        ]
        return {"result": True, "data": data}

    def get_object_storage_spec(self):
        """
        获取华为云对象存储规格，来自枚举
        Returns
        -------

        """
        return {"result": True, "data": [{"label": v, "value": k} for k, v in huaweicloud_bucket_cn_dict.items()]}

    def list_rds(self, ids=None, **kwargs):
        """查询云数据库 RDS 实例列表。

        官方 API: GET /v3/{project_id}/instances (ListInstances)
        参见 https://support.huaweicloud.com/api-rds/rds_01_0004.html
        返回原始 instances 列表（{"result": True, "data": [...]}），
        字段归一化在采集器 HuaweiCloudManager.get_rds 完成。
        """
        from huaweicloudsdkrds.v3 import ListInstancesRequest as _ListRdsInstancesRequest

        request = _ListRdsInstancesRequest()
        request.offset = 0
        request.limit = int(kwargs.get("limit", 100))
        client = self.get_client(RdsClient, RdsRegion)
        instances = []
        while True:
            try:
                response = client.list_instances(request)
            except Exception as e:
                logger.exception("调用华为云查询RDS实例列表接口失败{}".format(e))
                return {"result": False, "message": str(e)}
            body = response.to_dict()
            page_instances = body.get("instances", []) or []
            instances.extend(page_instances)
            total_count = int(body.get("total_count", len(instances)))
            if not page_instances or len(instances) >= total_count:
                break
            request.offset = len(instances)
        return {"result": True, "data": instances}

    def list_dcs(self, ids=None, **kwargs):
        """查询分布式缓存 DCS(Redis) 实例列表。

        官方 API: GET /v2/{project_id}/instances (ListInstances)
        参见 https://support.huaweicloud.com/api-dcs/ListInstances.html
        返回原始 instances 列表，字段归一化在采集器 get_dcs 完成。
        """
        from huaweicloudsdkdcs.v2 import ListInstancesRequest as _ListDcsInstancesRequest

        request = _ListDcsInstancesRequest()
        request.offset = 0
        request.limit = int(kwargs.get("limit", 1000))
        client = self.get_client(DcsClient, DcsRegion)
        instances = []
        while True:
            try:
                response = client.list_instances(request)
            except Exception as e:
                logger.exception("调用华为云查询DCS实例列表接口失败{}".format(e))
                return {"result": False, "message": str(e)}
            body = response.to_dict()
            page_instances = body.get("instances", []) or []
            instances.extend(page_instances)
            instance_num = int(body.get("instance_num", len(instances)))
            if not page_instances or len(instances) >= instance_num:
                break
            request.offset = len(instances)
        return {"result": True, "data": instances}

    def get_mysql_spec(self, version="", spec_code="", database="MySQL"):
        """
        获取mysql规格 ListFlavors。本接口可以获取pgSQL、SQLServer规格，
        Parameters
        ----------
        version (str): 版本号  (optional)
        spec_code (str): 规格编码  (optional)

        Returns
        -------

        """
        request = ListMySQLFlavorsRequest()
        request.database_name = database
        request.project_id = self.project_id
        if version:
            request.version_name = version
        if spec_code:
            request.spec_code = spec_code
        client = self.get_client(RdsClient, RdsRegion)
        try:
            response = client.list_flavors(request)
        except Exception as e:
            logger.exception("调用华为云获取mysql规格接口失败{}".format(e))
            return {"result": False, "message": str(e)}
        data = [
            {
                "label": spec["spec_code"],
                "value": spec["spec_code"],
                "cpu": spec["vcpus"],
                "mem": spec["ram"],
            }
            for spec in response.to_dict()["flavors"]
        ]
        return {"result": True, "data": data}

    def _set_params(self, request, **kwargs):
        """
        设定参数
        Parameters
        ----------
        kwargs

        Returns
        -------

        """
        for k, v in kwargs.items():
            setattr(request, k, v)
        return request

    def get_redis_spec(self, **kwargs):
        """
        查询redis规格
        Parameters
        ----------
        kwargs (dict):
            spec_code (str): 规格编码
            cache_mode (str): 缓存实例类型。  \
                single(单机实例) | ha(主备实例) | cluster (cluster集群) | proxy(proxy集群) | ha_rw_split(读写分离)
            engine (str): 缓存实例类型  Redis | Memcached
            engine_version (str): 版本  3.0等
            cpu_type (str): CPU架构类型  x86_64 | aarch64
            capacity (str): 缓存容量

        Returns
        -------

        """
        kwargs["engine"] = "redis"
        kwargs["cache_mode"] = "cluster"
        request = ListRedisFlavorsRequest()
        self._set_params(request, **kwargs)
        client = self.get_client(DcsClient, DcsRegion)
        try:
            response = client.list_flavors(request)
        except Exception as e:
            logger.exception("调用华为云获取redis规格接口失败{}".format(e))
            return {"result": False, "message": str(e)}
        data = [
            {
                "label": spec["spec_code"],
                "value": spec["spec_code"],
                "cpu": spec["cpu_type"],
                "mem": spec["capacity"],
            }
            for spec in response.to_dict()["flavors"]
        ]
        return {"result": True, "data": data}

    def get_mongodb_spec(self, **kwargs):
        """
        查询mongodb规格
        Parameters
        ----------
        kwargs (dict):

        Returns
        -------

        """
        request = ListMongodbFlavorsRequest()
        # request.engine_name = "DDS-Community"
        request.region = self.region_id
        client = self.get_client(DdsClient, DdsRegion)
        try:
            response = client.list_flavors(request)
        except Exception as e:
            logger.exception("调用华为云获取redis规格接口失败{}".format(e))
            return {"result": False, "message": str(e)}
        data = [
            {
                "label": spec["spec_code"],
                "value": spec["spec_code"],
                "cpu": spec["vcpus"],
                "mem": spec["ram"],
                "type": spec["type"],
            }
            for spec in response.to_dict()["flavors"]
        ]
        return {"result": True, "data": data}

    # -----------------------对象存储服务 OBS--------------------------
    def create_bucket(self, **kwargs):
        """创建存储桶"""
        bucket_name = kwargs.get("bucket_name", uuid.uuid1().hex)
        location = kwargs.get("location", "")
        resp = self.obs_client.createBucket(bucket_name, location=location)
        if resp.status < 300:
            return {"result": True, "data": resp.json()["id"]}
        return {"result": False, "message": "create_bucket failed"}

    def delete_bucket(self, bucket_name, **kwargs):
        """删除某个存储空间（Bucket）"""
        resp = self.obs_client.deleteBucket(bucket_name)
        if resp.status < 300:
            return {"result": True}
        logger.exception(f"delete bucket {bucket_name} fail")

        return {"result": False, "message": resp.errorMessage}

    def put_object(self, *args, **kwargs):
        """上传文件"""
        bucket_name = kwargs.get("bucket_name")
        if "file_path" not in kwargs:
            return {"result": False, "message": "need param local_path"}
        file_path = kwargs.get("file_path")
        if file_path[-1] == "/":
            kwargs.pop("content")
        resp = self.obs_client.putContent(bucket_name, file_path, kwargs.get("content"))
        if resp.status < 300:
            return {"result": True}
        logger.exception(f" put_object {file_path} fail")

        return {"result": False, "message": resp.errorMessage}

    def load_object(self, *args, **kwargs):
        """下载文件"""
        object_name = kwargs.get("object_name")
        bucket_name = kwargs.get("bucket_name")

        resp = self.obs_client.getObject(bucket_name, object_name)
        try:
            # 空文件会报错
            file_content = resp.read()
        except Exception:
            file_content = b""
        if resp.status < 300:
            return {"result": True, "data": file_content}
        logger.exception(f" load_object {object_name} fail")

        return {"result": False, "message": resp.errorMessage}

    def delete_object(self, *args, **kwargs):
        """删除文件"""
        object_list = kwargs.get("object_list")
        bucket_name = kwargs.get("bucket_name")
        objects = [Object(key=key) for key in object_list]
        delete_objects_request = DeleteObjectsRequest(objects=objects)
        resp = self.obs_client.deleteObjects(bucket_name, delete_objects_request)
        if resp.status < 300:
            return {"result": True}
        logger.exception("delete_object fail")

        return {"result": False, "message": resp.errorMessage}

    def list_bucket_file(self, bucket_name, location):
        """获取存储桶下的所有object"""
        resp = self.obs_client.listObjects(bucket_name)
        if resp.status > 300:
            return {"result": False, "message": resp.errorMessage}
        object_lists = resp.body.contents
        new_object_list = []
        for item in object_lists:
            args_object = type("Test", (), {})
            if item.key.endswith("/"):
                args_object.type = "DIR"
                args_object.parent = "/".join(item.key.split("/")[:-2]) if "/" in item.key.strip("/") else ""
                args_object.name = item.key.split("/")[-2]
            else:
                args_object.type = "FILE"
                args_object.parent = "/".join(item.key.split("/")[:-1]) if "/" in item.key else ""
                args_object.name = item.key.split("/")[-1]
            args_object.size = item.size
            args_object.last_modified = item.lastModified
            args_object.key = item.key
            args_object.bucket = bucket_name
            args_object.location = location
            new_object_list.append(args_object)
        top_dir_list = [item for item in new_object_list if item.parent == "" and item.type == "DIR"]
        for top_dir in top_dir_list:
            set_dir_size(top_dir, object_lists)
        return success(
            format_resource(CloudResourceType.BucketFile.value, new_object_list, self.region_id, self.project_id)
        )


def exception_handler(request_method):
    """
    装饰器，用于捕捉调用华为云sdk产生的异常信息
    :param request_method：类型：str，调用sdk的方法
    """

    @wraps(request_method)
    def handler():
        try:
            response = request_method()
        except exceptions.ConnectionException as e:
            logger.exception(e)
            return fail("{}：连接异常".format(request_method.__name__))
        except exceptions.RequestTimeoutException as e:
            logger.exception(e)
            return fail("{}：响应超时".format(request_method.__name__))
        except exceptions.ServiceResponseException as e:
            logger.exception(e)
            return fail("{}：服务器响应异常".format(request_method.__name__))
        else:
            if response.status_code > 300:
                return fail(response.to_str())
            return success(response.to_dict())

    return handler()


def format_resource(resource, obj_list, region_id, project_id, **kwargs):
    """
    将云端资源对象数据转换为本地数据库对象
    :param resource: 类型：str，资源名对象类型，如region
    :param obj_list: 类型：str，需转换的资源对象列表
    :param region_id: 类型：str，对象公共属性，区域id
    :param project_id: 类型：str，对象公共属性，项目id
    """

    data = []
    for obj in obj_list:
        data.append(
            get_format_method(CloudPlatform.HuaweiCloud, resource, region_id=region_id, project_id=project_id)(
                obj, **kwargs
            )
        )
    return data
