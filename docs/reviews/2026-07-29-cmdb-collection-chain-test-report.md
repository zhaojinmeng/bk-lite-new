# CMDB 配置采集对象全链路测试报告

报告日期：2026-07-29（Asia/Shanghai）  
测试分支：`codex/cmdb-collection-chain-tests`  
测试代码基线：`8fc14dd76`  
结论：批准范围内测试完成度 100%，可以进入 PR 评审。

## 1. 执行摘要

本次共核对 104 个注册合同条目：

| 分区 | 数量 | 结果 |
|---|---:|---|
| 可测试生产三元组 | 79 | Lane A、Lane B 全部通过，无生产对象 `skip/xfail` |
| 生产豁免 | 1 | K8s 按用户决定不验证，单独登记且不计入通过 |
| 非生产/归档对象 | 24 | 注册表分区与归档原因合同通过，未冒充生产 E2E |

生产三元组的数据来源分布：

| Lane A 来源方式 | 数量 | 说明 |
|---|---:|---|
| 真实 Docker 采集证据 | 24 | 实际运行生产采集脚本/collector 和 formatter 后固化证据 |
| 官方 SDK 边界 Mock | 33 | Mock 官方 SDK 网络响应，真实运行父 collector 和 formatter |
| 私有 API 边界 Mock | 6 | Mock 私有云/存储 API 网络边界，真实运行父 collector 和 formatter |
| 命令或设备边界 Mock | 16 | 特殊 OS、设备或协议环境在最外层边界 Mock |

最终验证结果：

| 验证项 | 结果 |
|---|---|
| Stargazer 来源合同 | `482 passed, 79 warnings in 3.57s` |
| 工作簿与模型配置聚焦回归 | `11 passed in 6.49s` |
| 完整相关 Server 门禁 | `1107 passed, 91 skipped, 1 deselected in 94.50s` |
| 七类真实基础设施 smoke | `1 passed in 115.26s` |
| smoke 后 Docker 容器残留 | `0` |
| smoke 后 Docker 网络残留 | `0` |
| 最终规格复审 | `APPROVED` |
| 最终仓库标准复审 | `APPROVED`，无 Blocker、Important 或 Minor |

91 个 skip 来自历史归档、许可证阻塞或 placeholder 测试路径，不属于 79 个生产三元组。1 个 deselected 是组合回归中未显式设置 `CMDB_COLLECTION_SMOKE=1` 的真实 smoke 入口；该入口已另行实际运行并通过。

## 2. 验证范围和判定标准

Lane A：

```text
Stargazer 原始采集结果
  → 真实父 collector / formatter
  → Prometheus 文本
  → influxdb_client.Point / Line Protocol
  → NATS 发布边界
```

Lane B：

```text
VictoriaMetrics vector 响应
  → 真实 Collection.query / prom_sql
  → 真实注册 collection plugin
  → 字段清洗、类型转换、模型映射和关联
  → 独立静态 CMDB Golden
  → MetricsCannula / Management
  → FalkorDB 节点和边写入意图
```

生产对象通过要求：

1. Evidence 的 provenance、raw、Prometheus、Line Protocol、VM response、CMDB Golden 和三类 schema 完整。
2. Lane A 必须运行真实生产转换；Mock 只能位于 SDK、API、命令或设备网络边界。
3. Lane B 只替换 VM HTTP 响应，真实执行 `prom_sql`、注册 plugin、formatter、mapping 和 freshness 判断。
4. 完整输出与独立 Golden 精确比较，同时使用生产模型反射校验 allowed、required、type 和 enum。
5. `0`、`False`、空字符串按业务语义保留。
6. 节点、边、关联和幂等写入必须精确验证，不能仅记录 warning。
7. 生产对象不得使用 `skip/xfail` 掩盖缺口。

## 3. 79 个生产三元组逐项结果

“真实 smoke”列只表示该对象被选入七类真实 NATS → Telegraf → VictoriaMetrics → CMDB → FalkorDB 代表烟测；未选中不影响其 Lane A/Lane B 合同通过。

| # | 任务类型 | 父采集对象 | 最终产出模型 | Lane A 来源 | Lane A | Lane B | 下游写入 | 真实 smoke |
|---:|---|---|---|---|---|---|---|---|
| 1 | `cloud` | `aliyun_account` | `aliyun_bucket` | 官方 SDK 边界 Mock（aliyun：ListBuckets+GetBucketInfo） | 通过 | 通过 | 图库意图通过 | — |
| 2 | `cloud` | `aliyun_account` | `aliyun_clb` | 官方 SDK 边界 Mock（aliyun：DescribeLoadBalancers） | 通过 | 通过 | 图库意图通过 | — |
| 3 | `cloud` | `aliyun_account` | `aliyun_ecs` | 官方 SDK 边界 Mock（aliyun：DescribeInstances） | 通过 | 通过 | 图库意图通过 | — |
| 4 | `cloud` | `aliyun_account` | `aliyun_kafka_inst` | 官方 SDK 边界 Mock（aliyun：GetInstanceList） | 通过 | 通过 | 图库意图通过 | — |
| 5 | `cloud` | `aliyun_account` | `aliyun_mongodb` | 官方 SDK 边界 Mock（aliyun：DescribeDBInstances） | 通过 | 通过 | 图库意图通过 | — |
| 6 | `cloud` | `aliyun_account` | `aliyun_mysql` | 官方 SDK 边界 Mock（aliyun：DescribeDBInstances） | 通过 | 通过 | 图库意图通过 | — |
| 7 | `cloud` | `aliyun_account` | `aliyun_pgsql` | 官方 SDK 边界 Mock（aliyun：DescribeDBInstances） | 通过 | 通过 | 图库意图通过 | — |
| 8 | `cloud` | `aliyun_account` | `aliyun_redis` | 官方 SDK 边界 Mock（aliyun：DescribeInstances） | 通过 | 通过 | 图库意图通过 | — |
| 9 | `cloud` | `fusioninsight` | `fusioninsight_cluster` | 私有 API 边界 Mock（fusioninsight：GET /web/api/v2/clusters） | 通过 | 通过 | 图库意图通过 | — |
| 10 | `cloud` | `fusioninsight` | `fusioninsight_host` | 私有 API 边界 Mock（fusioninsight：GET /web/api/v2/hosts） | 通过 | 通过 | 图库意图通过 | — |
| 11 | `cloud` | `hwcloud` | `hwcloud` | 官方 SDK 边界 Mock（hwcloud：parent_account_context） | 通过 | 通过 | 图库意图通过 | — |
| 12 | `cloud` | `hwcloud` | `hwcloud_dcs` | 官方 SDK 边界 Mock（hwcloud：ListInstances） | 通过 | 通过 | 图库意图通过 | — |
| 13 | `cloud` | `hwcloud` | `hwcloud_ecs` | 官方 SDK 边界 Mock（hwcloud：ListServersDetails） | 通过 | 通过 | 图库意图通过 | — |
| 14 | `cloud` | `hwcloud` | `hwcloud_eip` | 官方 SDK 边界 Mock（hwcloud：ListPublicips） | 通过 | 通过 | 图库意图通过 | — |
| 15 | `cloud` | `hwcloud` | `hwcloud_elb` | 官方 SDK 边界 Mock（hwcloud：ListLoadBalancers） | 通过 | 通过 | 图库意图通过 | — |
| 16 | `cloud` | `hwcloud` | `hwcloud_evs` | 官方 SDK 边界 Mock（hwcloud：ListVolumes） | 通过 | 通过 | 图库意图通过 | — |
| 17 | `cloud` | `hwcloud` | `hwcloud_obs` | 官方 SDK 边界 Mock（hwcloud：listBuckets + listObjects） | 通过 | 通过 | 图库意图通过 | — |
| 18 | `cloud` | `hwcloud` | `hwcloud_rds` | 官方 SDK 边界 Mock（hwcloud：ListInstances） | 通过 | 通过 | 图库意图通过 | — |
| 19 | `cloud` | `hwcloud` | `hwcloud_sg` | 官方 SDK 边界 Mock（hwcloud：ListSecurityGroups） | 通过 | 通过 | 图库意图通过 | — |
| 20 | `cloud` | `hwcloud` | `hwcloud_subnet` | 官方 SDK 边界 Mock（hwcloud：ListSubnets） | 通过 | 通过 | 图库意图通过 | — |
| 21 | `cloud` | `hwcloud` | `hwcloud_vpc` | 官方 SDK 边界 Mock（hwcloud：ListVpcs） | 通过 | 通过 | 图库意图通过 | — |
| 22 | `cloud` | `qcloud` | `qcloud_bucket` | 官方 SDK 边界 Mock（qcloud：list_buckets） | 通过 | 通过 | 图库意图通过 | — |
| 23 | `cloud` | `qcloud` | `qcloud_clb` | 官方 SDK 边界 Mock（qcloud：DescribeLoadBalancers） | 通过 | 通过 | 图库意图通过 | — |
| 24 | `cloud` | `qcloud` | `qcloud_cmq` | 官方 SDK 边界 Mock（qcloud：DescribeQueueDetail） | 通过 | 通过 | 图库意图通过 | — |
| 25 | `cloud` | `qcloud` | `qcloud_cmq_topic` | 官方 SDK 边界 Mock（qcloud：DescribeTopicDetail） | 通过 | 通过 | 图库意图通过 | — |
| 26 | `cloud` | `qcloud` | `qcloud_cvm` | 官方 SDK 边界 Mock（qcloud：DescribeInstances） | 通过 | 通过 | 图库意图通过 | 通过 |
| 27 | `cloud` | `qcloud` | `qcloud_domain` | 官方 SDK 边界 Mock（qcloud：DescribeDomainNameList） | 通过 | 通过 | 图库意图通过 | — |
| 28 | `cloud` | `qcloud` | `qcloud_eip` | 官方 SDK 边界 Mock（qcloud：DescribeAddresses） | 通过 | 通过 | 图库意图通过 | — |
| 29 | `cloud` | `qcloud` | `qcloud_filesystem` | 官方 SDK 边界 Mock（qcloud：DescribeCfsFileSystems） | 通过 | 通过 | 图库意图通过 | — |
| 30 | `cloud` | `qcloud` | `qcloud_mongodb` | 官方 SDK 边界 Mock（qcloud：DescribeDBInstances） | 通过 | 通过 | 图库意图通过 | — |
| 31 | `cloud` | `qcloud` | `qcloud_mysql` | 官方 SDK 边界 Mock（qcloud：DescribeDBInstances） | 通过 | 通过 | 图库意图通过 | — |
| 32 | `cloud` | `qcloud` | `qcloud_pgsql` | 官方 SDK 边界 Mock（qcloud：DescribeDBInstances） | 通过 | 通过 | 图库意图通过 | — |
| 33 | `cloud` | `qcloud` | `qcloud_plusar_cluster` | 官方 SDK 边界 Mock（qcloud：DescribeClusters） | 通过 | 通过 | 图库意图通过 | — |
| 34 | `cloud` | `qcloud` | `qcloud_redis` | 官方 SDK 边界 Mock（qcloud：DescribeInstances） | 通过 | 通过 | 图库意图通过 | — |
| 35 | `cloud` | `qcloud` | `qcloud_rocketmq` | 官方 SDK 边界 Mock（qcloud：DescribeRocketMQClusters） | 通过 | 通过 | 图库意图通过 | — |
| 36 | `cloud` | `storage` | `storage` | 私有 API 边界 Mock（OceanStor：聚合查询） | 通过 | 通过 | 图库意图通过 | — |
| 37 | `cloud` | `storage` | `storage_disk` | 私有 API 边界 Mock（OceanStor：disk API） | 通过 | 通过 | 图库意图通过 | — |
| 38 | `cloud` | `storage` | `storage_pool` | 私有 API 边界 Mock（OceanStor：storagepool API） | 通过 | 通过 | 图库意图通过 | — |
| 39 | `cloud` | `storage` | `storage_volume` | 私有 API 边界 Mock（OceanStor：lun API） | 通过 | 通过 | 图库意图通过 | — |
| 40 | `db` | `es` | `es` | 真实 Docker 采集证据 | 通过 | 通过 | 图库意图通过 | — |
| 41 | `db` | `hbase` | `hbase` | 命令/设备边界 Mock | 通过 | 通过 | 图库意图通过 | — |
| 42 | `db` | `mongodb` | `mongodb` | 真实 Docker 采集证据 | 通过 | 通过 | 图库意图通过 | — |
| 43 | `db` | `postgresql` | `postgresql` | 真实 Docker 采集证据 | 通过 | 通过 | 图库意图通过 | — |
| 44 | `db` | `redis` | `redis` | 真实 Docker 采集证据 | 通过 | 通过 | 图库意图通过 | — |
| 45 | `host` | `host` | `host` | 真实 Docker 采集证据 | 通过 | 通过 | 图库意图通过 | 通过 |
| 46 | `host` | `host` | `host_proc_usage` | 真实 Docker 采集证据 | 通过 | 通过 | 图库意图通过 | — |
| 47 | `host` | `physcial_server` | `disk` | 命令/设备边界 Mock | 通过 | 通过 | 图库意图通过 | — |
| 48 | `host` | `physcial_server` | `gpu` | 命令/设备边界 Mock | 通过 | 通过 | 图库意图通过 | — |
| 49 | `host` | `physcial_server` | `memory` | 命令/设备边界 Mock | 通过 | 通过 | 图库意图通过 | — |
| 50 | `host` | `physcial_server` | `nic` | 命令/设备边界 Mock | 通过 | 通过 | 图库意图通过 | — |
| 51 | `host` | `physcial_server` | `physcial_server` | 命令/设备边界 Mock | 通过 | 通过 | 图库意图通过 | — |
| 52 | `ip` | `ip` | `ip` | 真实 Docker 采集证据 | 通过 | 通过 | IPAM 服务合同通过 | — |
| 53 | `middleware` | `activemq` | `activemq` | 真实 Docker 采集证据 | 通过 | 通过 | 图库意图通过 | — |
| 54 | `middleware` | `apache` | `apache` | 真实 Docker 采集证据 | 通过 | 通过 | 图库意图通过 | — |
| 55 | `middleware` | `consul` | `consul` | 真实 Docker 采集证据 | 通过 | 通过 | 图库意图通过 | — |
| 56 | `middleware` | `docker` | `docker` | 真实 Docker 采集证据 | 通过 | 通过 | 图库意图通过 | — |
| 57 | `middleware` | `etcd` | `etcd` | 真实 Docker 采集证据 | 通过 | 通过 | 图库意图通过 | — |
| 58 | `middleware` | `haproxy` | `haproxy` | 真实 Docker 采集证据 | 通过 | 通过 | 图库意图通过 | — |
| 59 | `middleware` | `iis` | `iis` | 命令/设备边界 Mock | 通过 | 通过 | 图库意图通过 | — |
| 60 | `middleware` | `kafka` | `kafka` | 真实 Docker 采集证据 | 通过 | 通过 | 图库意图通过 | — |
| 61 | `middleware` | `keepalive` | `keepalived` | 命令/设备边界 Mock | 通过 | 通过 | 图库意图通过 | — |
| 62 | `middleware` | `memcached` | `memcached` | 真实 Docker 采集证据 | 通过 | 通过 | 图库意图通过 | — |
| 63 | `middleware` | `minio` | `minio` | 真实 Docker 采集证据 | 通过 | 通过 | 图库意图通过 | — |
| 64 | `middleware` | `nginx` | `nginx` | 真实 Docker 采集证据 | 通过 | 通过 | 图库意图通过 | 通过 |
| 65 | `middleware` | `openresty` | `openresty` | 命令/设备边界 Mock | 通过 | 通过 | 图库意图通过 | — |
| 66 | `middleware` | `rabbitmq` | `rabbitmq` | 真实 Docker 采集证据 | 通过 | 通过 | 图库意图通过 | — |
| 67 | `middleware` | `rocketmq` | `rocketmq` | 命令/设备边界 Mock | 通过 | 通过 | 图库意图通过 | — |
| 68 | `middleware` | `spark` | `spark` | 命令/设备边界 Mock | 通过 | 通过 | 图库意图通过 | — |
| 69 | `middleware` | `squid` | `squid` | 真实 Docker 采集证据 | 通过 | 通过 | 图库意图通过 | — |
| 70 | `middleware` | `tomcat` | `tomcat` | 真实 Docker 采集证据 | 通过 | 通过 | 图库意图通过 | — |
| 71 | `middleware` | `zookeeper` | `zookeeper` | 真实 Docker 采集证据 | 通过 | 通过 | 图库意图通过 | — |
| 72 | `protocol` | `influxdb` | `influxdb` | 真实 Docker 采集证据 | 通过 | 通过 | 图库意图通过 | 通过 |
| 73 | `protocol` | `mssql` | `mssql` | 命令/设备边界 Mock | 通过 | 通过 | 图库意图通过 | — |
| 74 | `protocol` | `mysql` | `mysql` | 真实 Docker 采集证据 | 通过 | 通过 | 图库意图通过 | 通过 |
| 75 | `protocol` | `oracle` | `oracle` | 命令/设备边界 Mock | 通过 | 通过 | 图库意图通过 | — |
| 76 | `protocol` | `physcial_server` | `physcial_server` | 命令/设备边界 Mock | 通过 | 通过 | 图库意图通过 | — |
| 77 | `protocol` | `postgresql` | `postgresql` | 真实 Docker 采集证据 | 通过 | 通过 | 图库意图通过 | — |
| 78 | `snmp` | `network` | `network` | 设备/SNMP 边界 Mock | 通过 | 通过 | 图库模型 `switch` 写入通过 | 通过 |
| 79 | `vm` | `vmware_vc` | `vmware_vc` | 私有 API 边界 Mock（pyVmomi SmartConnect） | 通过 | 通过 | 图库意图通过 | 通过 |

注意：

- 第 51 项 fixture/case_id 为 `host_physcial_server`，最终产出模型为现有生产拼写 `physcial_server`。
- 第 61 项父采集对象为 `keepalive`，最终模型为 `keepalived`。
- 第 77 项 fixture/case_id 为 `protocol_postgresql`，父对象和最终模型均为 `postgresql`。
- 第 78 项来源模型为 `network`，最终图库模型为 `switch`。
- `qcloud_plusar_cluster` 保留现有 CMDB 历史拼写；Stargazer 来源指标别名已由合同显式绑定。

## 4. 生产豁免

| 任务类型 | 父采集对象 | 最终模型 | Lane A | Lane B | 结论 |
|---|---|---|---|---|---|
| `k8s` | `k8s_cluster` | `k8s_cluster` | 不执行 | 不执行 | 用户批准豁免：外部 kube-state-metrics 直接写入 VM，不经过 Stargazer；未计入 79 个通过项 |

## 5. 24 个非生产/归档对象逐项结果

这些条目通过的是“注册表分区和归档声明合同”，不是生产 Lane A/Lane B。它们不会被统计为生产 E2E 通过。

| # | 任务类型 | 对象 | 名称 | 未执行生产 E2E 的原因 | 结果 |
|---:|---|---|---|---|---|
| 1 | `cloud` | `h3c_cas` | H3C CAS 私有云 | 生产插件为 stub | 归档分区合同通过；未执行生产 Lane A/B |
| 2 | `cloud` | `zstack` | ZStack 私有云 | 生产插件为 stub | 归档分区合同通过；未执行生产 Lane A/B |
| 3 | `host` | `domestic_linux` | 国产 Linux（统信 UOS/麒麟） | 需特定操作系统平台 | 归档分区合同通过；未执行生产 Lane A/B |
| 4 | `host` | `mycat` | MyCat 数据库中间件 | 需复杂集群环境 | 归档分区合同通过；未执行生产 Lane A/B |
| 5 | `middleware` | `apusic` | 东方通 Apusic 应用服务器 | 缺少商业许可证 | 归档分区合同通过；未执行生产 Lane A/B |
| 6 | `middleware` | `bes` | 宝兰德 BES 中间件 | 缺少商业许可证 | 归档分区合同通过；未执行生产 Lane A/B |
| 7 | `middleware` | `couchbase` | Couchbase NoSQL | 缺少商业许可证 | 归档分区合同通过；未执行生产 Lane A/B |
| 8 | `middleware` | `ihs` | IBM HTTP Server | 缺少商业许可证 | 归档分区合同通过；未执行生产 Lane A/B |
| 9 | `middleware` | `informix` | IBM Informix 数据库 | 缺少商业许可证 | 归档分区合同通过；未执行生产 Lane A/B |
| 10 | `middleware` | `inforsuite_as` | 浪潮 InforSuite AS | 缺少商业许可证 | 归档分区合同通过；未执行生产 Lane A/B |
| 11 | `middleware` | `iris` | InterSystems IRIS | 缺少商业许可证 | 归档分区合同通过；未执行生产 Lane A/B |
| 12 | `middleware` | `oceanbase` | 蚂蚁 OceanBase 分布式数据库 | 缺少商业许可证 | 归档分区合同通过；未执行生产 Lane A/B |
| 13 | `middleware` | `oscar` | 神舟通用 Oscar 数据库 | 缺少商业许可证 | 归档分区合同通过；未执行生产 Lane A/B |
| 14 | `middleware` | `sap_hana` | SAP HANA 内存数据库 | 缺少商业许可证 | 归档分区合同通过；未执行生产 Lane A/B |
| 15 | `middleware` | `sybase` | Sybase 数据库 | 缺少商业许可证 | 归档分区合同通过；未执行生产 Lane A/B |
| 16 | `middleware` | `tonggtp` | 东方通 TongGTP 消息中间件 | 缺少商业许可证 | 归档分区合同通过；未执行生产 Lane A/B |
| 17 | `middleware` | `tonglinkq` | 东方通 TongLinkQ 消息中间件 | 缺少商业许可证 | 归档分区合同通过；未执行生产 Lane A/B |
| 18 | `middleware` | `tongrds` | 东方通 TongRDS 关系数据库 | 缺少商业许可证 | 归档分区合同通过；未执行生产 Lane A/B |
| 19 | `middleware` | `weblogic` | Oracle WebLogic 应用服务器 | 缺少商业许可证 | 归档分区合同通过；未执行生产 Lane A/B |
| 20 | `middleware` | `websphere` | IBM WebSphere 应用服务器 | 缺少商业许可证 | 归档分区合同通过；未执行生产 Lane A/B |
| 21 | `protocol` | `hdfs` | HDFS 分布式文件系统 | 需复杂集群环境 | 归档分区合同通过；未执行生产 Lane A/B |
| 22 | `protocol` | `storm` | Apache Storm 流处理 | 需复杂集群环境 | 归档分区合同通过；未执行生产 Lane A/B |
| 23 | `protocol` | `yarn` | Hadoop YARN 资源调度 | 需复杂集群环境 | 归档分区合同通过；未执行生产 Lane A/B |
| 24 | `middleware` | `tuxedo` | Oracle Tuxedo 交易中间件 | 缺少商业许可证 | 归档分区合同通过；未执行生产 Lane A/B |

归档原因统计：

- 缺少商业许可证：17
- 复杂集群环境：4
- 特定操作系统平台：1
- 生产插件为 stub：2

## 6. 七类真实基础设施 smoke 结果

实际链路：

```text
已验证的 Stargazer 来源合同
  → 注入唯一 run_id/task_id/新鲜时间的 Line Protocol
  → 真实 NATS
  → 真实 Telegraf
  → 真实 VictoriaMetrics
  → 真实 CMDB Collection.query / plugin
  → 真实 MetricsCannula / Management
  → 真实 FalkorDB 节点和边
  → 独立 Lane B Golden 精确断言
  → 精确清理
```

| 代表对象 | 上游来源方式 | NATS/Telegraf/VM | CMDB 清洗 | FalkorDB | 结果 |
|---|---|---|---|---|---|
| `host` | 真实 Docker 采集证据 | 真实 | 真实 | 真实 | 通过 |
| `mysql` | 真实 Docker 采集证据 | 真实 | 真实 | 真实 | 通过 |
| `influxdb` | 真实 Docker 采集证据 | 真实 | 真实 | 真实 | 通过 |
| `nginx` | 真实 Docker 采集证据 | 真实 | 真实 | 真实 | 通过 |
| `qcloud_cvm` | 官方 SDK 网络边界 Mock | 真实 | 真实 | 真实 | 通过 |
| `vmware_vc` | pyVmomi 私有 API 边界 Mock | 真实 | 真实 | 真实 | 通过 |
| `network` | 设备/SNMP 边界 Mock | 真实 | 真实 | 真实 | 通过 |

真实 smoke 使用：

- `nats:2.10.26-alpine`
- `telegraf:1.34.0-alpine`
- `victoriametrics/victoria-metrics:v1.115.0`
- `falkordb/falkordb:v4.4.0`
- Compose project：`cmdb-collection-7f29bca1`
- 运行结果：`1 passed in 115.26s`
- 完成后容器残留：`0`
- 完成后网络残留：`0`

## 7. 发现并修复的生产缺陷

测试过程中按 RED → 最小修复 → GREEN 收敛了以下问题：

1. NATS 在尚未成功投递任何记录时，获取连接或首条发布失败会被错误处理；现已支持安全重试，第二条或 flush 失败仍保守停止以避免重复投递。
2. Prometheus/Line Protocol 转换会因 truthiness 过滤丢失 `0`、`False` 和空字符串；现已按业务语义保留。
3. Aliyun 分页多请求处理存在缺口；已补分页回归。
4. IP scanner 的生产模块路径失效；已修复真实 `PluginExecutor` 导入路径。
5. Host、VMware、PostgreSQL、Redis、Keepalived、Network 等字段映射与生产模型不一致；已逐项对齐并由完整 Golden 锁定。
6. VictoriaMetrics 联合查询的 range selector 位置和缓存会导致新鲜样本查询不稳定；已修复并增加回归。
7. Huawei VPC 布尔字段合同不一致；已对齐生产模型。
8. `model_config.xlsx` 曾出现非目标 sheet 漂移及错误 `"684"` 元数据；现仅保留批准的 Consul、Redis、Docker 变更，344 个非目标 worksheet XML 与基线逐字节一致。

## 8. 证据位置

| 证据 | 位置 |
|---|---|
| 三集合合同清单 | `server/apps/cmdb/tests/e2e/contract_manifest.json` |
| 每个对象 provenance 和六阶段制品 | `server/apps/cmdb/tests/e2e/fixtures/<case_id>/` |
| 每个对象 schema | `server/apps/cmdb/tests/e2e/schemas/<case_id>/` |
| Lane A 来源合同 | `agents/stargazer/tests/collection_contract/` |
| Lane B 合同 | `server/apps/cmdb/tests/e2e/test_lane_b_contract.py` |
| 图库写入意图合同 | `server/apps/cmdb/tests/e2e/test_graph_intent_contract.py` |
| smoke 安全框架与 workload | `server/apps/cmdb/tests/smoke/collection_chain/` |
| 工作簿漂移门禁 | `server/apps/cmdb/tests/test_model_config_workbook_drift.py` |
| 最终交接与验收摘要 | `docs/plans/2026-07-28-cmdb-collection-chain-validation-handoff.md` |

## 9. 最终结论

在已批准范围内：

- 79 个可测试生产三元组全部完成 Lane A、Lane B 和下游写入验证。
- 7 个代表对象完成真实 NATS、Telegraf、VictoriaMetrics、CMDB 和 FalkorDB 烟测。
- 1 个 K8s 对象按用户决定明确豁免，没有冒充通过。
- 24 个非生产/归档对象完成分区与归档声明验证，没有计入生产 E2E。
- 特殊环境对象仅在 SDK/API/命令/设备最外层边界 Mock，下游生产转换和写入链路保持真实。
- 最终双轴评审批准，无剩余 Blocker、Important 或 Minor。

因此，本次 CMDB 配置采集对象全链路验证在批准范围内判定为通过。
