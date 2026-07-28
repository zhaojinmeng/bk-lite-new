# CMDB 配置采集全链路验证 AI 交接文档

更新时间：2026-07-28（Asia/Shanghai）

## 1. 交接目标

继续完成 CMDB 配置采集对象的两段数据链路验证，并在全部离线合同、图库意图合同、真实基础设施 smoke 和最终回归通过后创建 PR。

本任务验证的是生产数据链路，不是只检查 fixture 是否存在：

```text
Lane A
Stargazer 原始采集结果
  → 真实 formatter
  → Prometheus 文本
  → influxdb_client.Point / Line Protocol
  → NATS 发布边界

Lane B
VictoriaMetrics vector 响应
  → 真实 Collection.query / prom_sql
  → 真实注册 collection plugin
  → 字段清洗、模型映射、关联字段
  → 独立静态 CMDB Golden

Task 7
Lane B 清洗结果
  → MetricsCannula
  → Management
  → FalkorDB 节点/边写入意图

Task 9
代表对象
  → 真实 NATS
  → 真实 Telegraf
  → 真实 VictoriaMetrics
  → 真实 CMDB 清洗
  → 真实 FalkorDB
```

## 2. 必须从这里继续

仓库根目录：

```text
/Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite
```

实际实施 worktree：

```text
/Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/.worktrees/cmdb-collection-chain-tests
```

分支：

```text
codex/cmdb-collection-chain-tests
```

Task 6 当前未提交实现所基于的代码基线：

```text
caa5e6363 fix(cmdb): 补齐Redis与Docker采集字段模型
```

交接文档本身已经单独提交在该基线之后；接手时以 `git log -3 --oneline`
显示的当前 HEAD 为准。

必须直接使用上述现有 worktree。当前 Task 6 有重要未提交改动；不要在仓库主工作区重新开始，不要执行 `git reset`、`git checkout --` 或清理工作区。

进入现场后先运行：

```bash
cd /Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/.worktrees/cmdb-collection-chain-tests
git branch --show-current
git status --short
git log -12 --oneline
```

## 3. 用户已经锁定的决策

以下决策不再重新讨论：

1. 采用“所有对象分层合同 + 代表对象真实基础设施 smoke”。
2. K8s 完全不做本次验证：
   - 不跑 Lane A；
   - 不跑 Lane B；
   - 不进入真实基础设施 smoke；
   - 必须作为用户批准的生产对象豁免单独记录，不能冒充已通过或非生产对象。
3. 可测试生产范围为 79 个最终三元组：
   - 三元组为 `(task_type, supported_model_id, emitted_model_id)`；
   - 生产注册表、产出模型和 evidence manifest 必须双向一致。
4. 特殊环境对象允许 Mock：
   - 云厂商：官方 API/SDK 网络边界 Mock；
   - 私有云、专有设备、特殊集群：明确的 boundary/private API Mock；
   - Mock 必须运行真实父 collector、formatter 和 CMDB plugin，不能替换内部 formatter/mapping 冒充覆盖。
5. 云 Mock 必须来源可追溯：
   - 官方文档 URL；
   - API/SDK 版本；
   - 读取日期；
   - 测试运行时完全离线。
6. 能本地拉 Docker 的对象优先真实尝试：
   - 成功则保存真实采集证据；
   - 失败则保存精确命令、退出码、清理结果和降级依据；
   - 不允许伪造“真实 Docker 已通过”。
7. Lane B 只 Mock VM HTTP 查询：
   - `prom_sql()`、plugin registry、formatter、mapping 必须真实执行；
   - 冻结墙上时钟可以作为非确定性隔离，但生产 freshness predicate 必须真实执行。
8. 独立 Golden 与模型反射双重断言：
   - Golden 是静态字面量，不得由测试运行时生成或回写；
   - 对完整业务输出精确比较，不允许退回 subset-only；
   - 生产 workbook 负责 allowed、required、type、enum；
   - `0`、`False`、`""` 必须按业务语义保留；
   - 厂商新增但未映射的可选字段进入确定性 drift，不阻断；
   - 已映射字段缺失、错误重命名、错误类型必须失败。
9. 关联字段必须精确验证：
   - Lane B 验证清洗结果中的 `assos`；
   - Task 7 验证最终 FalkorDB edge intent；
   - warning 不能当作 PASS。
10. 真实 smoke 代表对象固定为：
    - `host`
    - `mysql`
    - `influxdb`
    - `nginx`
    - `qcloud`
    - `vmware`
    - `network`
11. 普通 CI 只跑离线合同；真实基础设施 smoke 手动/nightly 运行，但创建 PR 前必须实际跑完。
12. 发现生产缺陷时在同一分支按 TDD 修复：
    - 稳定复现；
    - 保留 RED；
    - 最小生产修复；
    - GREEN；
    - 不用 `skip` 或 `xfail` 掩盖。
13. 全部完成后才创建 PR。
14. 不使用或恢复本任务的 Superpowers 产物：
    - 不创建 `.superpowers/**`；
    - 不创建或恢复 `docs/superpowers/**` 下的本任务 plan/spec/verification；
    - 本交接文档位于普通 `docs/plans/`。

## 4. 当前完成度

按最终门禁估算约 70%–75%。

| Task | 状态 | 结论 |
|---|---|---|
| Task 1：生产三元组真相源 | 完成、正式评审通过 | 生产、生产豁免、非生产三集合严格分区 |
| Task 2：Evidence loader/schema/敏感门禁 | 完成、正式评审通过 | 严格缺失汇总、官方来源域名、敏感键/值扫描 |
| Task 3：Lane A 语义与 NATS 发布边界 | 完成、正式评审通过 | 真实转换、解析、时间精度、零投递安全重试 |
| Task 4：合法假值 | 完成、正式评审通过 | `0`、`False`、空字符串不被 truthiness 丢弃 |
| Task 5：79/79 Lane A | 完成、正式双轴评审通过 | 40 个真实父 collector，79 个最终三元组 |
| Task 6：79/79 VM → CMDB | 接近完成，尚未提交/复审 | 最新聚焦测试 319 passed、1 个过期断言失败 |
| Task 7：图库写入意图 | 未开始 | 需要 GraphIntentSpy + MetricsCannula/Management |
| Task 8：真实 smoke 安全框架 | 完成、正式双轴评审通过 | 53/53 safety tests；尚未运行真实业务 smoke |
| Task 9：七类真实基础设施 smoke | 未开始 | `test_collection_chain.py` 尚不存在 |
| Task 10：最终全量回归和 PR | 未开始 | 只有全部门禁通过后才执行 |

## 5. 已完成的关键提交

### Task 1–4

```text
Task 1: 19c1289..01b32f5
Task 2: 967068f..5a70f36
Task 3: 605e37e..b00b55a
Task 4: 6517de7
```

其中 Task 3 修复了真实 NATS 缺陷：获取连接或首条发布在零成功行时可以安全重试；第二条或 flush 失败仍保守停止，避免重复投递。

### Task 5 / Lane A

Task 5 最终状态：

- 79/79 三元组存在真实 adapter binding；
- 40 个生产父 collector 实际执行；
- 云 SDK Mock 下沉到官方 SDK 网络方法；
- 8 个特殊对象保存真实 Docker attempt；
- boundary drift report 可机器审计；
- K8s 豁免不计入通过。

末端关键提交：

```text
a166e4d32 test(cmdb): 固化Docker清理原始证据
28d145d9c test(cmdb): 限定场景支持合同应用范围
3c41bd9d6 test(cmdb): 固化边界字段漂移审计
```

Task 6 评审期间发现原 Lane A 的 `instance_id=cmdb-...` 不符合生产查询约定，已按根因回补：

```text
0761f357f test(cmdb): 修复LaneA任务身份证据链
```

现在：

- `02_prometheus.txt` 本来不携带 task identity；
- `03_line_protocol.txt` 由真实 publish params 注入稳定数字 `cmdb_<task_id>`；
- `04_vm_response.json` 从 `03` 原样传播；
- 禁止使用连字符形态；
- `prom_sql` 必须查询同一 identity。

### Task 8 / smoke 安全框架

```text
8387376a8 test(cmdb): 增加采集链路烟测安全框架
b795e2891 fix(cmdb): 收紧烟测资源与清理边界
dc9e1d260 fix(cmdb): 限制烟测全局清理预算
6641a4ee8 fix(cmdb): 保留烟测外层截止语义
```

安全框架位置：

```text
server/apps/cmdb/tests/smoke/collection_chain/
```

已经完成：

- 固定版本镜像；
- 唯一 `run_id` / Compose project；
- 随机回环端口；
- 条件健康检查；
- NATS → Telegraf → VM canary；
- 有界 startup/workload/canary/cleanup/log/command；
- 精确资源 ledger；
- 精确 project 清理；
- 容器和网络零残留确认；
- 业务失败与清理失败同时保留。

尚未拉取镜像或执行七类真实业务 smoke。

### Task 6 已提交部分

```text
611823a6f fix(cmdb): 修复采集字段清洗与实例标识
b465d5078 fix(cmdb): 对齐华为云与Redis模型字段类型
a8b68bce4 test(cmdb): 覆盖全量VM到CMDB清洗合同
ae93ef230 fix(cmdb): 补齐Consul生产模型定义
49aceff29 test(cmdb): 收紧LaneB模型反射与敏感门禁
0761f357f test(cmdb): 修复LaneA任务身份证据链
8eb377cd9 test(cmdb): 重建LaneB VM身份与时间证据
caa5e6363 fix(cmdb): 补齐Redis与Docker采集字段模型
```

`model_config.xlsx` 使用 `@oai/artifact-tool` 修改并完成视觉、公式和语义验证：

- 新增 `attr-consul`；
- `attr-redis` 新增 `topo_mode/cluster_uuid/slaves/master`；
- `attr-docker` 新增 `status`；
- 348 个 sheet 无增删；
- 除目标 sheet 外既有非空单元格语义保持一致；
- 公式错误为 0。

## 6. Task 6 当前未提交现场

当前 `git status --short`：

- 共 168 个修改文件；
- 无 untracked 文件；
- 7 个生产文件；
- 78 个 `05_expected_cmdb.json`；
- 79 个 `cmdb.schema.json`；
- 4 个 Lane B loader/test 文件。

不要丢弃这些修改。

### 6.1 未提交生产文件

```text
server/apps/cmdb/collection/collect_plugin/host.py
server/apps/cmdb/collection/collect_plugin/vmware.py
server/apps/cmdb/collection/plugins/community/db/postgresql.py
server/apps/cmdb/collection/plugins/community/db/redis.py
server/apps/cmdb/collection/plugins/community/middleware/keepalived.py
server/apps/cmdb/collection/plugins/community/network/plugins.py
server/apps/cmdb/collection/plugins/community/vm/plugins.py
```

当前生产修复意图：

- Host：可选 int 缺失时不再写入空字符串；`cpu_core/memory/disk` 合法缺失仍为 `0`。
- VMware VC：补 `ip_addr`，来源为采集任务实例。
- PostgreSQL：模型字段名对齐：
  - `conf` → `config`
  - `max_conn` → `max_connect`
- Redis：
  - 保留 `cluster_uuid/slaves/master`；
  - 新增 `topo_mode` mapping；
  - 不得删除文档已经承诺的拓扑字段。
- Keepalived：移除把 transport 元标签 `bk_obj_id` 写入模型实例的错误 mapping。
- Network：移除把 transport `model_id` 写入 switch 业务模型的错误 mapping。

以上改动已经被 Lane B RED 暴露，但尚未独立提交、尚未完成正式代码评审。

### 6.2 未提交测试基础设施

```text
server/apps/cmdb/tests/e2e/contract_loader.py
server/apps/cmdb/tests/e2e/lane_b_loader.py
server/apps/cmdb/tests/e2e/test_contract_evidence.py
server/apps/cmdb/tests/e2e/test_lane_b_contract.py
```

已经实现：

- Lane B 调用完整 Evidence 敏感信息门禁；
- `inst_name` 的合成名称不误报；
- 私网 IP、真实主机名、token/password/secret 仍 fail-closed；
- 生产 workbook 反射：
  - 全空短尾行允许跳过；
  - 非空短行必须失败；
  - 校验 allowed、required、type、enum；
- `04` 从 `03` 严格传播 metric/model/instance/value/纳秒时间；
- 79 份紧凑 VM schema：
  - 锁定目标 metric/model/identity；
  - 拒绝 `9999999999` 未来时间；
  - 多模型父 collector 允许兄弟行，但目标行只能精确出现一次；
- 79 份独立静态完整 Golden：
  - 使用 `expected_instances`；
  - 禁止 `expected_instance_subset`；
  - 与真实 plugin 完整输出逐字段相等；
  - `optional_absent_fields` 必须精确等于 `mapping - actual`；
  - VM 已有非空 mapped 字段不得被列为 optional；
  - 实际关联对象包含精确 `assos`；
- 冻结 `collect_util.datetime.now()`，但继续运行真实 freshness predicate；
- 增加“两天前 VM 数据必须被过滤”的负向测试；
- IPAM 特例保留，但 `prom_sql` 和 identity 也必须精确匹配。

## 7. 最新可复现测试状态

2026-07-28 最新命令：

```bash
cd /Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/.worktrees/cmdb-collection-chain-tests/server

MINIO_ENDPOINT=localhost:9000 \
MINIO_ACCESS_KEY=test \
MINIO_SECRET_KEY=test \
MINIO_USE_HTTPS=false \
INSTALL_APPS=system_mgmt,node_mgmt,cmdb \
/Users/windyzhao/.local/bin/uv run pytest -q -o addopts='' \
  apps/cmdb/tests/e2e/test_contract_evidence.py \
  apps/cmdb/tests/e2e/test_lane_b_contract.py
```

结果：

```text
319 passed, 1 failed in 46.83s
```

唯一失败：

```text
apps/cmdb/tests/e2e/test_contract_evidence.py
test_生产缺口与非生产归档状态由结构化_audit_返回
```

失败原因：

```python
assert audit.incomplete_validation
```

这是 Task 2 阶段为了证明 evidence 尚有缺口而留下的旧断言。现在 79 个生产三元组已经全部 ready，所以正确最终合同应改为：

```python
assert audit.incomplete_validation == ()
```

同时保留以下断言：

- audit validation 集合精确等于生产三元组；
- non-production 集合和归档原因仍完整；
- K8s 豁免不进入 ready。

交接后的第一步就是按 RED/GREEN 更新此测试，再重跑同一命令；预期为 320 passed。

## 8. Task 6 收口步骤

### 8.1 修复唯一旧断言

只修改测试语义，不修改 audit 实现来制造假缺口。

### 8.2 重跑 Task 6 聚焦合同

使用第 7 节命令，目标：

```text
320 passed
```

### 8.3 重跑 Lane A，防止 Task 6 回补破坏生产转换

```bash
cd /Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/.worktrees/cmdb-collection-chain-tests/agents/stargazer

/Users/windyzhao/.local/bin/uv run pytest -q \
  tests/collection_contract
```

至少确认：

- 79/79 contract；
- `cmdb_<数字 task_id>`；
- `02 → 03 → 04` identity 不漂移；
- 所有真实父 collector 仍执行；
- Docker attempt 与 boundary drift audit 不回退。

### 8.4 重跑 Server 组合回归

```bash
cd /Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/.worktrees/cmdb-collection-chain-tests/server

MINIO_ENDPOINT=localhost:9000 \
MINIO_ACCESS_KEY=test \
MINIO_SECRET_KEY=test \
MINIO_USE_HTTPS=false \
INSTALL_APPS=system_mgmt,node_mgmt,cmdb \
/Users/windyzhao/.local/bin/uv run pytest -q -o addopts='' \
  apps/cmdb/tests/e2e/test_collection_contract_manifest.py \
  apps/cmdb/tests/e2e/test_contract_evidence.py \
  apps/cmdb/tests/e2e/test_lane_b_contract.py \
  apps/cmdb/tests/test_collection_field_type_alignment.py \
  apps/cmdb/tests/test_new_collect_objects_model_config.py \
  apps/cmdb/tests/test_new_collect_objects_registry.py \
  apps/cmdb/tests/test_new_collect_objects_formatters.py
```

如果某些 Django 测试需要数据库，使用隔离 SQLite：

```text
DB_ENGINE=sqlite
DB_NAME=:memory:
```

不要连接共享数据库。

### 8.5 审查生产修复

逐项确认：

- Host 不会把未知可选 int 写成 `""`；
- VMware VC `ip_addr` 来源稳定且没有读取错误实例；
- PostgreSQL 字段名和生产 workbook 一致；
- Redis docs、Stargazer、CMDB plugin、workbook 四者字段一致；
- Keepalived/Network 不再污染 transport 元标签；
- 每个生产缺陷有对应 RED。

生产修复和 79 个 evidence/schema 最好拆成独立中文提交，不要一个巨型提交。

建议提交顺序：

1. `fix(cmdb): 对齐采集字段与生产模型`
2. `test(cmdb): 固化完整VM到CMDB实例合同`
3. `test(cmdb): 收紧模型反射与关联字段门禁`

### 8.6 Task 6 正式双轴评审

评审必须覆盖：

1. 规格符合性：
   - 79/79；
   - K8s 排除；
   - VM HTTP 是主要替身；
   - 完整 Golden；
   - 模型 required/type/enum；
   - exact associations；
   - deterministic drift；
   - 无 skip/xfail。
2. 代码质量：
   - 测试没有复制生产 mapping 形成同源假阳性；
   - schema 紧凑可维护；
   - 时间冻结没有绕过生产 freshness；
   - 敏感门禁无明显误报绕过；
   - 生产修复最小且有回归测试。

Task 6 未正式批准前不要进入 Task 7。

## 9. Task 7：图库写入意图合同

目标：证明 Lane B 清洗结果经过真实 `MetricsCannula` 和 `Management` 后，会产生正确的 FalkorDB 节点和边操作。

生产入口：

```text
server/apps/cmdb/collection/metrics_cannula.py
server/apps/cmdb/collection/common.py
```

推荐新增：

```text
server/apps/cmdb/tests/e2e/graph_intent_spy.py
server/apps/cmdb/tests/e2e/test_graph_intent_contract.py
```

GraphIntentSpy 至少记录：

- `query_entity`
- `create_entity`
- `set_entity_properties`
- `detach_delete_entity`
- `create_edge`

规则：

1. 运行真实 `MetricsCannula` / `Management`；
2. 只替换 FalkorDB I/O，不替换业务编排；
3. 每个 graph-entity case 精确断言：
   - model_id；
   - 唯一键；
   - 创建/更新/删除意图；
   - 最终属性；
4. 有关联的对象精确断言 edge：
   - source model/instance；
   - destination model/instance；
   - `asst_id`；
   - `model_asst_id`；
5. IPAM `write_mode=ipam_service` 按真实服务边界验证，不强行伪造成图库实体；
6. 不允许 warning 后继续 PASS；
7. 不允许 `skip/xfail`。

Task 7 完成后也要独立做规格与代码质量双轴评审。

## 10. Task 9：真实基础设施 smoke

安全框架说明：

```text
server/apps/cmdb/tests/smoke/collection_chain/README.md
```

Compose：

```text
server/apps/cmdb/tests/smoke/collection_chain/compose.yaml
```

现有固定镜像：

```text
nats:2.10.26-alpine
telegraf:1.34.0-alpine
victoriametrics/victoria-metrics:v1.115.0
falkordb/falkordb:v4.4.0
```

先跑离线 safety：

```bash
cd /Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/.worktrees/cmdb-collection-chain-tests/server

MINIO_ENDPOINT=localhost:9000 \
MINIO_ACCESS_KEY=test \
MINIO_SECRET_KEY=test \
MINIO_USE_HTTPS=false \
INSTALL_APPS=system_mgmt,node_mgmt,cmdb \
/Users/windyzhao/.local/bin/uv run pytest -q -o addopts='' \
  apps/cmdb/tests/smoke/collection_chain/test_safety_pure.py \
  apps/cmdb/tests/smoke/collection_chain/test_safety_limits_pure.py \
  apps/cmdb/tests/smoke/collection_chain/test_canary_protocol_pure.py
```

检查 Compose：

```bash
docker compose \
  --file server/apps/cmdb/tests/smoke/collection_chain/compose.yaml \
  config --quiet
```

然后实现当前缺失的：

```text
server/apps/cmdb/tests/smoke/collection_chain/test_collection_chain.py
```

七类对象：

```text
host
mysql
influxdb
nginx
qcloud
vmware
network
```

要求：

- `host/mysql/influxdb/nginx` 尽量走本地真实可启动采集源；
- `qcloud/vmware/network` 可按用户决策使用明确 source boundary Mock；
- 下游必须真实经过 NATS、Telegraf、VM、CMDB 和 FalkorDB；
- 每个资源携带唯一 `run_id`；
- 创建后立即登记 ledger；
- 只能删除 owner 等于当前 `run_id` 的资源；
- 失败保留日志；
- 最终容器、网络、图节点、图边零残留。

真实运行形式：

```bash
CMDB_COLLECTION_SMOKE=1 \
CMDB_SMOKE_RUN_ID=cmdb-a1b2c3d4 \
/Users/windyzhao/.local/bin/uv run pytest -q -o addopts='' \
  apps/cmdb/tests/smoke/collection_chain/test_collection_chain.py
```

不要使用固定 `run_id` 重复执行；每次生成新的合法 ID。

## 11. Task 10：最终门禁

最终 PR 前必须完成：

1. Task 1–7 全部测试通过；
2. Task 8 safety tests 通过；
3. Task 9 七类真实 smoke 通过；
4. 79 个生产三元组 Lane A/Lane B 零缺口；
5. K8s 仍明确排除；
6. non-production/archived 分区仍完整；
7. boundary drift report 稳定；
8. 敏感信息门禁通过；
9. 无 `skip/xfail` 掩盖生产对象；
10. 容器、网络、VM 指标、FalkorDB 节点/边零残留；
11. 对最终完整 diff 做双轴评审；
12. 工作区只包含本任务改动；
13. 全部完成后才 push 和创建 PR。

## 12. 已知易踩坑

1. 不要把 `04_vm_response.json` 直接手写成与 `03` 无关的 dict。
2. 不要使用未来时间戳 `9999999999`。
3. 不要把 `instance_id` 写成 `cmdb-xxx`；生产格式是 `cmdb_<数字 task_id>`。
4. 不要用 set 比较吞掉重复指标。
5. 不要 Mock 云父 collector 内部 manager/formatter；Mock 要下沉到外层 SDK 网络方法。
6. 不要把生产 mapping 直接复制成 Golden 生成逻辑。
7. 不要只断言两个关键字段；当前合同要求完整 `expected_instances`。
8. 不要把 VM 已携带的 mapped 字段列为 optional。
9. 不要让关系缺失只打印 warning。
10. 不要用 lambda 替换 freshness predicate；冻结 `datetime.now()`。
11. 不要删除 Redis 文档承诺的拓扑字段来迁就旧 workbook。
12. 不要把 `bk_obj_id/model_id` 等 transport 标签写入业务模型。
13. 不要在真实 smoke 中使用 `docker system prune`、`down -v`、宽目录递归删除或固定宿主端口。
14. 不要恢复本任务旧的 Superpowers 文档或台账。

## 13. 交接后的第一条执行指令

把下面内容直接交给接手 AI：

> 在
> `/Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/.worktrees/cmdb-collection-chain-tests`
> 的 `codex/cmdb-collection-chain-tests` 分支继续。先阅读
> `docs/plans/2026-07-28-cmdb-collection-chain-validation-handoff.md`，
> 保留当前 168 个未提交文件，不执行 reset/checkout/clean。
> 第一项工作是修正
> `test_生产缺口与非生产归档状态由结构化_audit_返回`
> 的过期断言，使 79 个生产三元组零缺口成为最终合同；重跑文档第 7 节命令，目标
> 320 passed。随后按第 8 节完成 Task 6 回归、拆分提交和双轴评审。Task 6 正式批准后，
> 依次完成 Task 7、Task 9、Task 10。K8s 不测试，特殊环境对象使用明确边界 Mock，
> 不创建或恢复任何本任务 Superpowers 产物，全部通过后才创建 PR。
