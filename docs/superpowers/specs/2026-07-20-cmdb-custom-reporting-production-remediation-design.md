# CMDB 自定义上报 24 项生产缺陷修复设计

## 1. 背景与决策

自定义上报同时支持标准模型和快速模型。真实 HTTP、FalkorDB、SQLite/Celery 故障注入及契约测试确认了 24 项产品缺陷，其中 P0 11 项、P1 13 项。现有实现把关系库、FalkorDB 和 Broker 的多步副作用串在一个同步请求中，但没有稳定的调用者上下文、入站 Schema、资产所有权引用或持久化操作状态机。

本设计采用已经确认的方案 A-1：允许新增数据库迁移，并为 Enterprise 自定义上报建立专用 `CustomReportingOperation`、`CustomReportingOutbox` 和 `CustomReportingReconciler`。不采用仅靠 `transaction.atomic()`、同步补偿、吞异常或把全部状态硬塞进通用 `CmdbOperation` 的方案。

## 2. 目标与非目标

### 2.1 目标

- 两种接入模式在任何图写、关系库写或 Broker 派发前，共用一套可信调用者、Schema 和所有权校验。
- 每个跨存储操作都有稳定身份、请求摘要、代次、租约、已落事实和可恢复终态。
- 图成功而 DB/Broker 失败、DB 成功而图失败时，客户端能得到确定语义，后台能够续跑或人工处置。
- snapshot、清理审核和 pending relation 默认 fail closed，不因空输入、查询异常或并发重复执行而删除错误资产。
- 所有入口和后台扫描都有可测试的资源预算。
- 24 项缺陷逐项执行 RED→确认失败→最小修复→GREEN，并保留真实 E2E 验证。

### 2.2 非目标

- 不改变 Django ORM、FalkorDB、Celery 的基础选型。
- 不自动删除存量任务、模型或资产；无法证明归属的数据只进入隔离/人工修复清单。
- 不为缺失的关联 `mapping` 猜测默认语义。
- 不进行无关 CMDB ViewSet、图驱动或前端页面重构。
- 不将“删除上报任务是否级联删除资产”这一开放产品问题混入本次 24 项修复。

## 3. 根因与修复域映射

| 缺陷 | 根因 | 主修复域 |
|---|---|---|
| F01 | 创建/更新未校验目标组织状态 | CallerContext + 目标状态授权 |
| F02 | 控制面 action 未执行功能权限 | 权限矩阵 |
| F03 | 非法身份键进入 merge | CompiledReportingSchema |
| F04 | 标准模式未执行模型字段校验 | CompiledReportingSchema |
| F05 | 快速模式保留字段仍进入图写 | 规范化实例计划 |
| F06 | relation source model 未校验且被 pending 改写 | 关系 Schema + OwnedInstanceRef |
| F07 | 部分 merge 失败仍继续危险阶段 | IngestOperation phase gate |
| F08 | old_data/snapshot 查询缺 owner scope | OwnedInstanceScope |
| F09 | relation 两端未校验 owner/team | OwnedInstanceRef |
| F10 | 先删图后保存审核状态 | CleanupOperation + Reconciler |
| F11 | Beat 任务未注册到 Worker | 任务注册合同 |
| F12 | 入口和扫描无资源上限 | ResourceBudget |
| F13 | 图写后同步派发 Broker | Outbox + 幂等请求 |
| F14 | API Secret 与 CMDB 组织结构不一致 | CallerContext |
| F15 | mapping 写入口与运行时合同分裂 | 关联 Schema |
| F16 | token 拒绝映射为 500 | typed authentication exception |
| F17 | 空 snapshot 被当作权威全量空集合 | 明示 authoritative-empty + 强制审核 |
| F18 | 删除前查询异常被降级为空结果 | fail-closed cleanup |
| F19 | 快速模型先写图后持久化意图 | ProvisionOperation + Reconciler |
| F20 | DB task 和图模型组织分叉 | UpdateOperation + generation/CAS |
| F21 | 并发审核无唯一执行者 | CAS + owner lease |
| F22 | 重复身份键静默覆盖 | 批次 Schema 编译查重 |
| F23 | poison pending 永久阻塞后续 ingest | PendingRelationDelivery 状态机 |
| F24 | 合法图 ID 0 被 truthiness 拒绝 | GraphId 值对象 |

## 4. 信任边界

### 4.1 CallerContext

管理面和开放上报面在进入 Provider 后立即构造只读 `CallerContext`：

- `actor_id`、`actor_name`；
- `auth_kind`：session、platform、api_secret；
- `allowed_team_ids: frozenset[int]`；
- `permission_codes: frozenset[str]`；
- `credential_id` 和 `task_id`（仅开放上报）；
- 审计关联 ID。

所有 group 输入通过现有兼容 normalizer 统一转换，禁止业务 Service 继续读取形态不稳定的 `request.user.group_list`。API Secret 的绑定 team 只能收窄范围，不能扩展 session/platform 权限。

控制面权限矩阵至少为：

- list/retrieve/activity：`model_management-View`；
- create：`model_management-Add Model`；
- update：`model_management-Edit Model`；
- destroy：`model_management-Delete Model`；
- credential issue/rotate/revoke：对应查看或编辑权限，并再次校验 task team；
- cleanup approve/reject：编辑权限，并校验 review 所属 task/team。

create/update 必须在任何模型、任务、凭据副作用前，验证合并后的目标 team 是 `allowed_team_ids` 的非空子集。

### 4.2 认证错误合同

缺失、随机、已轮换和已吊销 token 统一抛 typed authentication exception，HTTP 返回 401；不得暴露 token、摘要、凭据数量或内部异常。合法但无目标组织权限返回 403。认证拒绝路径不得创建 Batch、Operation 或图事实。

## 5. 入站 Schema 编译

### 5.1 CompiledReportingSchema

任务创建/更新时编译并验证配置；每次 ingest 再次编译或读取带版本的已编译结果，以保护存量坏配置。编译产物包含：

- 模式、目标模型和 schema version；
- 非空、去重、非保留的 identity keys；
- standard 模式允许字段及类型；
- quick 模式可登记业务字段和禁止字段；
- association src/dst model、mapping 和允许的 identity；
- owner scope 规则；
- 资源预算。

编译失败必须发生在 Batch、字段登记、图写和 pending 创建前。

### 5.2 规范化实例计划

原始 instances 先生成不可变 `NormalizedInstancePlan`：

- 服务端覆盖 `cr_last_reported_at`；
- standard 拒绝未知和所有系统保留字段；
- quick 对登记与图写使用同一份字段集合，移除调用方 `_id` 等保留字段；
- identity 类型规范化后构造 signature；
- 同批 signature 重复时整批拒绝，不允许 first-wins 或 last-wins；
- 缺失 identity 字段、空值或非法类型整批拒绝。

### 5.3 关系计划

每条 relation 在持久化或图写前编译为 `NormalizedRelationPlan`：

- source model 必须等于任务模型；
- association 的 src/dst model 与 mapping 必须与模型定义一致；
- mapping 为必填枚举；存量缺失 mapping 返回明确业务错误，不猜默认值；
- `_id` 必须通过 `GraphId` 校验：`type(value) is int and value >= 0`，缺失仅以 `is None` 判断，拒绝 bool 和负数。

## 6. 所有权隔离

### 6.1 OwnedInstanceScope

批量 old_data、snapshot 和 expire 查询统一携带：

- `model_id`；
- `collect_task=cr_<task.id>`；
- task 当前有效 team 范围。

图查询必须把这些条件下推，不能先全模型加载再在 Python 过滤。删除前再次校验候选事实仍属于同一 scope，避免查询与删除之间的归属漂移。

### 6.2 OwnedInstanceRef

direct relation 和 pending backfill 共用同一个解析器。无论输入是 `_id` 还是 identity，返回前都必须验证 model、collect_task 和 organization。解析失败只产生有界 pending 或明确拒绝，不得连接到外部 task/team 节点。

## 7. 持久化操作状态机

### 7.1 CustomReportingOperation

新增 Enterprise-owned 模型，至少保存：

- `operation_id`；
- `action`：task_provision、task_update、ingest、cleanup_review、expire_cleanup；
- `scope_key`、`idempotency_key`、`request_hash`；
- `generation`、`state`；
- `owner_token`、`lease_expires_at`；
- `desired_snapshot`、`fact_snapshot`、`result_summary`；
- `attempt_count`、`next_attempt_at`、`last_error`；
- 创建、更新时间。

同一 scope + 幂等键具有数据库唯一约束。相同幂等键但请求摘要不同返回冲突，不复用旧操作。

状态流为：

```text
pending → claimed → graph_writing → graph_applied
        → db_committed → post_actions_pending → completed
                      ↘ retry / compensating / manual_failed
```

状态推进必须使用 state + generation + owner token 条件更新。租约过期后新 owner 可以接管；旧 owner 不得 finalize 新代次。

### 7.2 CustomReportingOutbox

Outbox 与 Operation 在关系库事务中共同持久化，使用 `(operation, event_type, dedupe_key)` 唯一约束，保存 payload、state、attempt、owner lease、next retry 和 last error。用途包括：

- 自动关系 reconcile；
- ChangeRecord/平台审计；
- 图/DB 后置同步；
- 人工失败通知。

Broker 不可用只使 Outbox 进入 retry，不把已经确认的图事实伪装成整个请求失败。

### 7.3 Reconciler

周期 Reconciler 使用固定 page、deadline 和 claim lease：

- 图事实缺失：在自然键/operation marker 下幂等续写；
- 图已应用、DB 未 finalize：核对 fact snapshot 后推进 DB；
- DB desired state 已存在、图未同步：续跑图同步；
- 状态不可判定：进入 manual_failed，不盲目补偿删除；
- Outbox 未完成：独立续投，不重复主图写。

## 8. 关键流程

### 8.1 快速任务创建

1. 校验权限、目标 team、配置和快速模型 Schema。
2. 以 Idempotency-Key 原子创建或读取 Operation，任务保持 provisioning，不签发可用凭据。
3. Reconciler/同步首轮按 operation marker 幂等创建模型、subordinate edge、字段组和属性，并记录事实。
4. 图事实核对完成后，在 DB 事务中创建/激活 task、scope、credential 和必要 Outbox。
5. Operation 完成后才返回可用 token；中断时同一幂等键继续原操作。

不能通过“失败后无条件删模型”补偿，因为模型可能已被后续事实引用；只有证明由当前 operation 独占时才允许补偿。

### 8.2 快速任务更新

task 增加 `state_version` 和同步状态，区分 effective state 与 desired state。更新请求使用 `If-Match` 或等价 generation：

1. 校验旧 effective state 和目标 desired state；
2. 创建 UpdateOperation，不立刻把授权切换到未同步 team；
3. 同步图模型 group；
4. 图达到 desired 后，以 generation CAS 提升为新的 effective state；
5. updating/degraded 期间危险写入 fail closed，旧 generation 不得覆盖新操作。

### 8.3 上报

1. token 认证、预算、Schema、重复 identity、relation 合同全部通过；
2. 根据 Idempotency-Key 创建 IngestOperation 和 Batch；
3. 按 OwnedInstanceScope 读取有界 old_data；
4. merge 图写并记录 fact snapshot；
5. 任一实例错误时进入失败/可恢复状态，禁止 relation、backfill 和 snapshot；
6. merge 完整成功后处理有界关系计划；
7. 自动关系等后置动作写 Outbox；
8. snapshot 仅在完整成功且满足安全条件时执行；
9. DB summary 与 Operation finalize 后返回确定结果。

### 8.4 空 snapshot

- `instances=[]` 且未显式 `authoritative_empty=true`：400，零 Batch、零图写、零删除。
- 显式权威空：即使清理阈值配置为 0，也必须创建人工审核，禁止自动直删。
- 审核候选必须先按 OwnedInstanceScope 收敛。

### 8.5 清理审核

1. 以 `(review_id, generation)` CAS 抢占 `pending→approving`，只有一个 winner。
2. 在 Operation 中保存候选及删除前事实；查询异常进入 retry，零图删。
3. 图删除按 operation 幂等执行并记录 fact。
4. 审核状态和审计通过 DB/Outbox finalize。
5. finalize 失败由 Reconciler 续跑，不再次执行不可判定的删除。

### 8.6 Pending relation

新增或扩展为独立 delivery 状态机：

- fingerprint 唯一去重；
- pending、processing、retry、dead_letter、success；
- attempt_count、next_retry_at、last_error、owner lease、generation；
- keyset 分页 claim，逐条异常隔离；
- 确定性非法 mapping/association 进入 dead_letter；
- pending 回填不再同步阻断新 ingest，可做有界 best-effort 或完全交给 Worker。

## 9. ResourceBudget

默认值由配置集中定义并可观测，至少覆盖：

- HTTP body bytes；
- 每批 instances、relations、实例字段数；
- 单次 old_data 页大小和最大页数；
- 单次 pending claim 数量；
- 单次 snapshot/expire 候选数量；
- 周期任务 deadline 和 checkpoint；
- token lookup 常数级索引路径与入口 throttle。

超预算必须在尽可能早的边界返回明确 4xx；后台任务达到预算后保存 checkpoint 并安全续跑，不能静默截断成成功。

## 10. 数据迁移与存量治理

迁移顺序：

1. 新增 Operation、Outbox、Pending delivery 字段/表及索引，旧路径仍可读。
2. 为 task 增加 state version、effective/desired 同步状态，默认映射现有 active 状态。
3. 扫描存量 task config：非法 identity、缺 model、非法保留字段配置标记 degraded，禁止继续写。
4. 扫描关联：缺失/非法 mapping 进入报告和 manual_failed，不自动补默认值。
5. 存量 pending 计算 fingerprint；完全重复项保留稳定一条，其余归档，无法规范化项进入 dead_letter。
6. 仅在联合 model/collect_task/organization 可证明时回填 owner scope；无法证明的数据不删除。
7. 切换写路径，再启用 Reconciler/Outbox Worker 和 Beat 注册合同。

所有迁移仅使用 Django ORM，禁止原生 SQL。

## 11. 可观测性与审计

至少暴露：

- Operation 各状态数量、最大停留时间、接管次数；
- Outbox/pending backlog、重试、dead-letter；
- Schema/权限/owner 拒绝计数；
- snapshot 审核候选和实际删除计数；
- 预算拒绝与后台 checkpoint；
- Reconciler 成功恢复和 manual_failed 数量。

日志只记录 operation_id、task_id、batch_id 和错误分类，不记录明文 token 或完整敏感 payload。

## 12. TDD 分批策略

每个 Finding 必须独立保留行为级测试，固定流程为：

1. 写或取消 strict-xfail 形成 RED；
2. 单独运行并确认失败原因命中该缺陷；
3. 只写使该测试通过的最小代码；
4. 运行聚焦测试和同域回归；
5. 记录 fix attempt；仅在证据充分时关闭对应 issue。

实施批次：

- 批次一：F01–F06、F14–F16、F22、F24，先关闭权限和入站信任边界。
- 批次二：F07–F09、F17、F18，关闭所有权、部分失败和删除安全边界。
- 批次三：F10、F13、F19–F21、F23，建立 Operation/Outbox/Reconciler 与 pending delivery。
- 批次四：F11、F12，完成任务注册、预算、分页、checkpoint 和常数级 token lookup。

F17–F24 在写任何对应生产代码前，先补齐正式 RED 契约。并发测试必须使用真实 ORM 条件更新，不只断言 mock 调用；跨存储测试必须覆盖图成功/DB 失败、DB 成功/图失败、Broker 失败、租约接管和迟到 owner。

## 13. 验证门禁

完成声明前必须执行：

- 24 项聚焦缺陷测试，确认不再依赖 xfail；
- custom reporting 社区层与 Enterprise 层全量测试；
- CMDB 相关回归和迁移检查；
- Black、isort、flake8、`git diff --check`；
- 修改代码覆盖率不低于 75%，核心权限/状态机/幂等路径目标 90%；
- 标准模式和快速模式真实 HTTP + FalkorDB E2E；
- Broker 故障、图/DB 分叉、并发 approve、poison pending、空 snapshot、ID 0 故障注入；
- Worker 默认模块导入后 Beat task 全注册。

只有本轮新鲜输出可作为 `verification-before-completion` 的完成证据。

## 14. 发布与回滚

- 先发布向后兼容迁移和只读 Reconciler，再切换写路径。
- 每个批次独立提交并可单独回滚应用代码；新增表和字段在回滚时保留。
- 切换前监控存量 degraded/manual_failed 数量，禁止自动处理未知所有权数据。
- 回滚写路径时暂停新 Reconciler claim，但允许当前 owner 在租约内安全结束；不得直接删除 operation/outbox 记录。
- 新路径稳定一个观察周期后，另行提案清理旧同步路径。

## 15. 完成定义

- F01–F24 均有复现测试、精确代码路径、最小修复和新鲜 GREEN 证据。
- 两种接入模式在正常、鉴权失败、部分写失败、重试、并发和恢复场景下结果确定。
- Operation、Outbox、Reconciler、pending delivery 的状态与所有权可查询、可审计、可恢复。
- 无跨 task/team 读写或删除，无空 snapshot 自动全删，无无审计删除。
- 对应 Projectmem issue 在验证后逐项关闭；未验证项不得因整体测试部分通过而批量关闭。
- 服务端质量门禁、覆盖率和真实 E2E 全部通过后，才允许给出生产放行结论。
