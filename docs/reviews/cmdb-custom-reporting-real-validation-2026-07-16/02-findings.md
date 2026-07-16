# CMDB 自定义上报真实验证 Findings

## CRV-F01：创建与更新可把任务绑定到请求方无权组织

- Severity：P0
- Location：`server/apps/cmdb_enterprise/custom_reporting/provider.py:61-62,79-81`；`server/apps/cmdb_enterprise/custom_reporting/services/task_service.py:211-263,279-310`
- Trigger：请求方允许组织为 `[1]`；创建时提交 `team=[2]`，或更新其已有
  `team=[1]` 任务为 `team=[2]`。
- Evidence：真实 API 分别返回 `(status=200, task_count=1)` 与
  `(status=200, persisted_team=[2])`，而合同要求 `(403, 0)` 与 `(403, [1])`。
- Impact：已认证用户可在无权组织创建带有效 Token 的上报任务，或把已有任务迁移到
  无权组织，进而获得向该组织写入资产数据的能力；同时污染组织范围与审计归属。
- Root Cause：`create_task()` 完全不读取 `_allowed_orgs(request)`；`update_task()` 只用
  `_require_task()` 校验更新前的任务组织，随后将 payload 中的新 `team` 原样持久化，
  没有对目标组织做 fail-closed 校验。
- Why Existing Tests Missed It：既有 provider 授权测试只覆盖“请求方能否访问当前
  task.team”，View 测试使用 fake overlay 且未断言真实落库副作用；没有覆盖创建目标
  组织和更新后目标组织。
- Required Tests：
  `test_create_rejects_team_outside_requester_scope`、
  `test_update_rejects_moving_task_outside_requester_scope`。当前均以
  `xfail(strict=True, reason="CRV-F01")` 固化。
- Projectmem：#0297（open；本验证任务不修改生产逻辑）。

## CRV-F02：控制面 list/create/update 未执行功能权限校验

- Severity：P0
- Location：`server/apps/cmdb/views/custom_reporting.py:11-36`
- Trigger：已认证但无 `model_management-View` 的用户访问列表；只有 View、没有
  `model_management-Add Model` 的用户创建；只有 View、没有
  `model_management-Edit Model` 的用户更新。
- Evidence：三个负向真实 API 请求均返回 200；create 确实新增任务，update 确实修改
  名称。对应正向权限请求也返回 200。
- Impact：任何通过全局认证的用户都能读取自定义上报控制面，并可创建任务和 Token、
  修改任务；功能角色配置不能形成预期授权边界。
- Root Cause：`CustomReportingTaskViewSet` 的控制面方法没有使用工程既有
  `@HasPermission` 装饰器。继承 `CmdbPermissionMixin` 不会自动执行 action 级功能权限，
  因而 `request.user.permission` 从未参与这些请求的判定。
- Why Existing Tests Missed It：既有 View 测试只验证 list/create 能委托给 fake overlay，
  未构造空权限或错权限主体；provider 级测试只覆盖组织 IDOR，不经过工程功能权限接口。
- Required Tests：list/create/update 各一组正负向真实 API 测试。三个负向测试当前均以
  `xfail(strict=True, reason="CRV-F02")` 固化，正向测试普通通过。
- Projectmem：#0298（open；本验证任务不修改生产逻辑）。

## 已验证无 Finding 的 Token 边界

测试工厂为每个用例新建唯一 `crval_...` 任务、模型、凭据和 Token。合法 Token 可进入
上报能力；轮换后旧 Token 失效且新 Token 有效；吊销后原 Token 失效。三项均普通通过，
未复用或修改任何既有任务/Token。

## CRV-F03：空 identity_keys 未在图写前拒绝

- Severity：P0
- Location：`server/apps/cmdb_enterprise/custom_reporting/services/merge_service.py:59-66,90-99`
- Trigger：创建 `identity_keys=[]`、`[""]` 或 `["_id"]` 的 standard 任务，并一次上报
  两个不同 `inst_name` 的实例。
- Evidence：真实 `merge_instances()` 没有抛出身份键异常，且 `Management.add_inst`、
  `Management.update_inst` 各被调用 1 次；合同要求明确拒绝且两个图写入口调用均为 0。
- Impact：任务失去稳定实例身份，多个实例可能被错误归类为同一无唯一键批次，产生重复、
  覆盖或不可预测的 merge 结果。
- Root Cause：`task.config.get("identity_keys") or []` 将显式空列表作为合法配置继续处理，
  `Management` 仍以 `unique_keys=[]` 构造并执行 add/update，没有 fail-closed 前置校验。
- Why Existing Tests Missed It：既有 merge 测试只覆盖非空 identity 强转和正常 upsert，
  没有同时断言异常与 add/update 两个入口均无副作用。
- Required Tests：`test_empty_or_invalid_identity_keys_rejected_before_graph_write`；当前以
  `xfail(strict=True, raises=KnownProductDefect, reason="CRV-F03")` 固化。
- Projectmem：#0312（open；本验证任务不修改生产逻辑）。

## CRV-F04：standard 模式未校验未知字段与保留字段

- Severity：P0
- Location：`server/apps/cmdb_enterprise/custom_reporting/provider.py:15-18`；
  `server/apps/cmdb_enterprise/custom_reporting/services/ingest_service.py:63-83`
- Trigger：standard 任务分别上报未声明字段 `crval_unknown` 和保留字段 `_id`。
- Evidence：两种载荷均未被拒绝，且原始实例字典完整进入 `merge_instances`；合同要求在
  merge/图写之前拒绝。Enterprise provider 没有覆盖 Community 的 no-op
  `validate_instance_fields`，ingest 也没有调用校验门面。
- Impact：standard 模式无法保证既有模型 schema，调用方可把未声明或系统保留字段带入
  图写链路，造成 schema 漂移、系统字段污染或标识伪造。
- Root Cause：商业 provider 只实现字段登记委托，没有实现实例/关系 schema 校验；
  ingest 在 standard 分支直接调用 merge。
- Why Existing Tests Missed It：Community 测试只证明默认扩展 no-op 可调用；Enterprise
  测试聚焦 quick 登记和 merge 结果，没有通过真实 ingest 对 standard 负向载荷断言
  拒绝及无 merge 副作用。
- Required Tests：参数化
  `test_standard_schema_rejects_unknown_and_reserved_fields_before_merge`；当前以
  `xfail(strict=True, raises=KnownProductDefect, reason="CRV-F04")` 固化。
- Projectmem：#0313（open；本验证任务不修改生产逻辑）。

## CRV-F05：quick 保留 `_id` 未登记但仍进入图写载荷

- Severity：P0
- Location：`server/apps/cmdb_enterprise/custom_reporting/services/model_service.py:104-136`；
  `server/apps/cmdb_enterprise/custom_reporting/services/merge_service.py:62-72,90-99`
- Trigger：quick 任务同时上报合法新字段 `crval_owner`、`_id` 和调用方控制的
  `cr_last_reported_at`。
- Evidence：测试执行真实 ingest 与真实 `merge_instances()`，捕获
  `Management.add_inst` 的最终入参：字段登记只创建 `crval_owner`，服务端生成的
  `cr_last_reported_at` 已覆盖 caller 值，但 `_id=9001` 仍存在于图写载荷。
- Impact：调用方可把内部图 ID 带入图实体创建契约，形成标识冲突或内部标识伪造风险；
  当前证据不宣称图存储已接受或持久化该 `_id`。
- Root Cause：`register_model_fields()` 在属性创建阶段跳过 `_id`，但 merge 构造
  `new_data` 时只覆盖上报时间戳，没有移除 `_id`，`Management.contrast()` 继续把它放入
  `add_list`。
- Why Existing Tests Missed It：既有 model_service 测试验证“未创建保留属性”，没有继续
  追踪同一载荷是否进入 merge；ingest quick 测试把登记函数替换为 no-op。
- Required Tests：正向 `test_quick_mode_registers_new_business_field_before_merge` 用统一事件
  列表严格证明字段创建先于 merge；负向
  `test_quick_mode_reserved_id_field_is_not_registered_or_written` 当前以
  `xfail(strict=True, raises=KnownProductDefect, reason="CRV-F05")` 固化。
- Projectmem：#0314（open；本验证任务不修改生产逻辑）。

## CRV-F06：关系源模型错配可经 backfill 创建错误边

- Severity：P0
- Location：`server/apps/cmdb_enterprise/custom_reporting/services/relation_service.py:52-68,75-108,111-145`；
  `server/apps/cmdb_enterprise/custom_reporting/services/ingest_service.py:84-88`
- Trigger：任务目标模型为 A，关系专用载荷的 `source.model_id` 指向不同模型 B，目标实例
  可解析。
- Evidence：真实 ingest 未拒绝并返回 `pending_relations=1`；随后同次 backfill 使用任务
  模型 A 解析 source，清掉 pending 并调用 `_create_edge` 1 次。合同要求拒绝、pending=0、
  图写=0。
- Impact：调用方声明的源模型与实际解析模型不一致，可能把 A 的实例错误连接到目标实例，
  形成跨模型错误拓扑和不可置信关系审计。
- Root Cause：`process()` 不校验 `source.model_id == task.config.model_id`；pending 记录把
  `source_model_id` 固定成任务模型，`backfill()` 随后忽略原载荷的错配模型并用该固定值
  查询 source。
- Why Existing Tests Missed It：既有关系测试只覆盖匹配模型的批次索引、pending 与回填，
  没有构造 source 模型错配，也没有跨 process/backfill 断言零图写副作用。
- Required Tests：`test_relation_endpoint_rejects_source_model_mismatch_without_side_effects`；
  当前以 `xfail(strict=True, raises=KnownProductDefect, reason="CRV-F06")` 固化。
- Projectmem：#0315（open；本验证任务不修改生产逻辑）。

## CRV-F07：部分 merge 仍标记成功并启动 snapshot

- Severity：P0
- Location：`server/apps/cmdb_enterprise/custom_reporting/services/ingest_service.py:81-115`
- Trigger：snapshot 任务的 `merge_instances()` 返回 `created=1`、`errors=1`、
  `covered_ids=[1]` 与两个旧实例。
- Evidence：真实 ingest 没有报告部分失败；最终观察值为
  `(rejected=False, batch.status="success", snapshot.call_count=1)`，而合同要求
  `(True, "failed", 0)`。
- Impact：调用方收到完整成功，且系统可把本轮写失败的实例误判为未覆盖并删除或送审，
  将单条写失败放大为资产丢失风险。
- Root Cause：ingest 读取 merge 摘要后未检查 `errors`，无条件继续关系、backfill、
  snapshot，并在末尾无条件把 Batch 保存为 SUCCESS。
- Why Existing Tests Missed It：既有 ingest 测试只返回 `errors=0`；cleanup 测试直接调用
  snapshot，未把部分 merge、Batch 终态与零 cleanup 副作用串联验证。
- Required Tests：`test_partial_merge_marks_batch_failed_and_skips_snapshot`；当前以
  `xfail(strict=True, raises=KnownProductDefect, reason="CRV-F07")` 固化。
- Projectmem：#0328（open；本验证任务不修改生产逻辑）。

## CRV-F08：同模型 old_data 未按 owner 与组织隔离

- Severity：P0
- Location：`server/apps/cmdb_enterprise/custom_reporting/services/merge_service.py:72-102`；
  `server/apps/cmdb_enterprise/custom_reporting/services/ingest_service.py:88-100`
- Trigger：同一模型存在不同自定义上报任务或不同组织的数据，当前任务执行 merge 或
  snapshot。
- Evidence：记录真实 `GraphClient.query_entity` 入参，只出现
  `{field: model_id, type: str=, value: <model>}`；合同要求同时出现
  `collect_task=cr_<task.id>` 与任务 organization 范围。查询结果随后整体成为 old_data，
  old IDs 会原样进入 snapshot。
- Impact：一个任务可把同模型下其他任务、其他组织、人工或其他采集来源的实例纳入覆盖、
  更新或删除候选，破坏 owner 边界并产生跨组织数据损坏风险。
- Root Cause：虽然 `Management` 写入配置带 `task_id` 与 `organization`，读取 old_data 的
  Graph 查询只按 model_id 过滤；ingest 对 old_data 没有第二道 owner/team 裁剪。
- Why Existing Tests Missed It：既有 merge 测试的 fake graph 固定返回空集，只断言
  collect_time 类型；snapshot 测试直接传入已假定安全的 old_ids。
- Required Tests：`test_merge_query_is_scoped_by_owner_and_team`；当前以
  `xfail(strict=True, raises=KnownProductDefect, reason="CRV-F08")` 固化。
- Projectmem：#0329（open；本验证任务不修改生产逻辑）。

## CRV-F09：直接关系与 pending 回填未校验两端组织范围

- Severity：P0
- Location：`server/apps/cmdb_enterprise/custom_reporting/services/relation_service.py:82-100,120-136`
- Trigger：任务 team 为 `[1]`，直接关系的 source 或 target 声明 organization `[2]`；
  或 pending 关系的 source organization 为 `[2]`。
- Evidence：直接路径 source/target 两个参数均观察到
  `(rejected=False, resolve=1, edge=1)`；backfill 观察到
  `(False, resolve=1, edge=1, pending_remaining=0)`。合同要求越界端点在图写前拒绝且
  pending 保留。source model 错配是独立 CRV-F06，本 Finding 不重复该模型错配行为。
- Impact：持有任务 Token 的调用方可把任务组织之外的实例作为关系端点，并在直接路径或
  后续回填中创建跨组织边；回填还会删除唯一 pending 证据。
- Root Cause：`process()` 对 source direct `_id` 直接信任，对 target 仅以
  model+identity 解析；`backfill()` 复用同样逻辑。两条路径都未以 task.team 校验载荷或
  已解析实例，也未在 `_create_edge` 前执行 owner/org 栅栏。
- Why Existing Tests Missed It：既有关系测试只覆盖目标存在、目标缺失和正常 backfill，
  端点均未携带跨组织状态；Task 5 的 CRV-F06 只覆盖 source.model_id 错配。
- Required Tests：
  `test_direct_relation_rejects_endpoint_outside_task_team_before_side_effects`、
  `test_pending_backfill_rejects_endpoint_outside_task_team`；当前均以
  `xfail(strict=True, raises=KnownProductDefect, reason="CRV-F09")` 固化。
- Projectmem：#0330（open；本验证任务不修改生产逻辑）。

## CRV-F10：审核图删除成功后 DB 保存失败会永久分叉

- Severity：P0
- Location：`server/apps/cmdb_enterprise/custom_reporting/services/cleanup_service.py:127-153`
- Trigger：pending cleanup review 的图删除成功，随后保存 APPROVED 审核状态时注入 DB
  异常。
- Evidence：单次有界故障注入后，删除副作用为 `[10,11]`，数据库审核状态仍为
  `pending`。反向注入图删除失败时，审核保持 pending 且 reviewed_at 为空，正向测试通过。
- Impact：调用方重试 pending 审核可能再次删除或误报不存在；活动页持续展示待审核，
  关系库与图存储没有 durable operation 可判定真实完成状态。
- Root Cause：`approve()` 先执行不可回滚的图删除，再保存关系库 APPROVED；两者之间无
  outbox/operation 状态、幂等代次或补偿 reconciler，数据库事务也无法回滚图副作用。
- Why Existing Tests Missed It：既有 approve 测试把删除替换为必成功列表追加，并让 DB
  保存始终成功；未对两个跨存储边界分别注入故障并重读数据库状态。
- Required Tests：
  `test_review_approval_does_not_delete_without_durable_approved_state`、
  `test_review_graph_failure_keeps_review_pending`；前者以
  `xfail(strict=True, raises=KnownProductDefect, reason="CRV-F10")` 固化，后者普通通过。
- Projectmem：#0331（open；本验证任务不修改生产逻辑）。
