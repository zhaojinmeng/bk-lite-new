# Task 5 报告：全部可测试生产对象 Lane A 与云 SDK/API 边界

## 状态

**IN PROGRESS / BLOCKED BY EXTERNAL EVIDENCE**。本轮已关闭正式评审提出的
5 个内部 Important；Task 5 仍不标记完成，也未开始 Task 6。

- `binding_coverage=79/79`：40 个真实生产 binding 精确展开为 79 个
  `(task_type, supported_model_id, emitted_model_id)` validation contracts。
- `executed_contracts=79/79`：每个真实 collector 用例自身携带 binding 身份，
  并在父输出、Prometheus 和 Line Protocol 均成功后断言其全部 contracts；
  覆盖不再依赖跨测试全局 set，单独运行和 xdist 均成立。
- `lane_a_file_ready=52/79`：Server evidence audit 实测 52 ready、
  27 missing_evidence。
- `strict_public_cloud_sdk_replay_ready=33/33`：QCloud 14、Aliyun 8、
  Huawei 11 均由同一冻结官方 SDK 响应运行真实父 collector，再将每个模型
  的**完整对象列表**与对应 `01_source_raw.json` 精确比较；alias 与 platform
  均由 provenance 显式绑定。
- K8s 按用户批准的 production validation exemption 单列，不进入 79 项分母。

## 本轮收口的五项 Important

### 1. 三家公有云父 collector 严格回放

- QCloud：真实 `TencentCloudManager → TencentClientProxy →
  CommonClient/CosS3Client`，只替换官方 SDK 网络方法；一次父执行覆盖 14 个
  case。`qcloud_plusar_cluster` 明确投影到源模型
  `qcloud_pulsar_cluster`。
- Aliyun：真实 `CwAliyun.list_all_resources()`，只替换 AcsClient 与官方
  生成式 SDK client 方法；一次父执行覆盖 8 个 case。
- Huawei：真实 `HuaweiCloudManager → CMPDriver → ResourceClient →
  CwHuaweicloud → 官方 Client`；一次父执行覆盖 11 个 case，
  `hwcloud` 平台对象来自父账户 endpoint 上下文。
- 三组都执行 `actual_parent_model_items == 01_source_raw.model_items`，
  不再只检查模型键、裁剪字段或把手写 source 直接送入
  `CollectionService`。完整 01 的静态 02/03 继续由真实转换语义测试校验。

严格回放额外发现并修复：

- QCloud Redis 未知字符串 RegionId 会被旧数字映射转成 `None`，现回退当前
  API region；
- QCloud CLB 官方 Status=1 的真实映射为“正常运行”，修正旧手写证据；
- Huawei CMP VM 输出 `inner_ip/public_ip` 列表和 `memory`，旧父适配器漏读，
  现显式取首个 IP 并兼容 `memory`。

### 2. Huawei VPC/Subnet/SecurityGroup marker 分页

三类官方 SDK 请求均使用 `marker + limit`。生产实现已：

- 读取完整第二页；
- 以末项 id 作为下一 marker；
- 缺 id/marker 不前进时安全停止；
- SDK 页失败保持显式失败；
- operation matrix 删除错误的 `not_applicable`。

分页、空集、缺可选字段、文档化错误和无进展场景均有可执行合同。

### 3. QCloud CMQ 错误传播

`DescribeQueueDetail` 与 `DescribeTopicDetail` 的文档化鉴权错误不再被吞成
空集。错误从资源方法显式传播，并使父 collector `success=false`。
RocketMQ 的官方 `UnsupportedRegion` 仍只跳过不支持地域，特殊语义未扩大到
CMQ。

### 4. 无顺序依赖的 79/79 门禁

删除 `EXECUTED_BINDING_KEYS` 进程全局副作用。每个真实 collector 用例根据
自身 binding 和真实父输出证明 contracts；独立静态集合门禁只比较
`PRODUCTION_ADAPTER_BINDINGS` 与 manifest，不依赖其他测试是否先运行。

验证：

- coverage test 单独运行：1 passed；
- `test_real_collector_execution.py`：41 passed；
- 全 suite `-n 2`：354 passed。

### 5. 报告与真实统计

本报告区分：

- 52 个文件制品 ready；
- 33 个公有云 case 同时达到严格 SDK replay ready；
- 6 个私有云 validation contracts 仍缺逐 case 官方文档/完整 evidence；
- 21 个非云 contracts 仍缺外部真实原始输入。

## 尚未关闭的外部缺口

### 私有云文档/evidence：6

`fusioninsight_cluster`、`fusioninsight_host`、`storage`、`storage_disk`、
`storage_pool`、`storage_volume`。

现有 FusionInsight/OceanStor HTTP 边界和五态 collector 合同不会冒充官方
operation 文档或完整 Lane A evidence。

### 非云真实输入：21

`es`、`hbase`、`host`、`host_proc_usage`、`disk`、`gpu`、`memory`、
`nic`、`host_physcial_server`、`ip`、`docker`、`iis`、`keepalived`、
`openresty`、`rocketmq`、`spark`、`mssql`、`oracle`、
`physcial_server`、`network`、`vmware_vc`。

这些对象缺少可审计的非空真实采集原始输出及采集时间/环境说明；不能用测试
构造值替代外部输入，也不能用 skip/xfail 冒充 ready。

## 新鲜验证

- Stargazer 串行：`354 passed`。
- Stargazer xdist `-n 2`：`354 passed`。
- `test_real_collector_execution.py`：`41 passed`；独立 coverage：
  `1 passed`。
- Server manifest/evidence：`68 passed`。
- Lane A evidence audit：`52 ready / 27 missing_evidence`。
- `git diff --check`：通过。

警告仅为第三方 `websockets/dateutil/obs` deprecation，不影响合同结论。

## 本轮提交

- `66eaa9d4f fix(stargazer): 显式传播腾讯云CMQ采集错误`
- `bd2c46f99 fix(stargazer): 补齐华为云网络资源marker分页`
- `c77c85190 test(stargazer): 移除采集合约顺序依赖`
- `6c1760f93 test(stargazer): 回放腾讯云父采集来源证据`
- `134bb9ad7 test(collection): 固化腾讯云完整父采集证据`
- `fa49761a3 test(collection): 固化阿里云完整父采集证据`
- `d6528c11a test(collection): 固化华为云完整父采集证据`
- `b542bb515 test(stargazer): 校正华为云安全组分页场景`

## 结论

正式评审提出的内部实现缺口已关闭；公有云严格 SDK replay 为 33/33。
Task 5 仍因 6 个私有云证据和 21 个非云真实输入缺失保持 pending。
`progress.md` 不改为 complete。
