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
  `collect_task=cr_<task.id>` 与任务 organization 范围；organization 合同使用仓库注册的
  `list[]`，并由参数化 formatter 验证为 `ALL(x IN $list1 WHERE x IN n.organization)`。
  查询结果随后整体成为 old_data，old IDs 会原样进入 snapshot。
- Impact：一个任务可把同模型下其他任务、其他组织、人工或其他采集来源的实例纳入覆盖、
  更新或删除候选，破坏 owner 边界并产生跨组织数据损坏风险。
- Root Cause：虽然 `Management` 写入配置带 `task_id` 与 `organization`，读取 old_data 的
  Graph 查询只按 model_id 过滤；ingest 对 old_data 没有第二道 owner/team 裁剪。
- Why Existing Tests Missed It：既有 merge 测试的 fake graph 固定返回空集，只断言
  collect_time 类型；snapshot 测试直接传入已假定安全的 old_ids。
- Required Tests：`test_merge_query_is_scoped_by_owner_and_team`；当前以
  `xfail(strict=True, raises=KnownProductDefect, reason="CRV-F08")` 固化。
- Projectmem：#0329（open；本验证任务不修改生产逻辑）。

## CRV-F09：直接关系与 pending 回填的 target 查询缺 owner/组织范围

- Severity：P0
- Location：`server/apps/cmdb_enterprise/custom_reporting/services/relation_service.py:82-100,120-136`
- Trigger：任务 team 为 `[1]`；payload 不声明 organization。真实 target 查询只带
  model+identity，RecordingGraph 因此返回 organization `[2]` 且
  `collect_task=cr_<另一任务 id>` 的现有节点。
- Evidence：直接路径和 pending/backfill 都调用真实 `_resolve_instance`，记录到的 filters
  仅为 target model+identity，缺 `collect_task` 与 `organization`；该外部节点随后进入
  `_create_edge(1, 2, ...)`，backfill 还删除 pending。direct source `_id=1` 全程没有图查询，
  只能精确证明调用方 ID 未经归属验证即传给 edge，不能据此虚构 source 的实际组织。
- Impact：持有任务 Token 的调用方可把目标身份解析到其他任务、其他组织的实例，并在
  直接路径或后续回填中创建跨 owner/team 边；回填还会删除唯一 pending 证据。source
  direct ID 同时缺少归属验证，但本证据不声称该 source 必然跨组织。
- Root Cause：`process()` 对 source direct `_id` 直接信任，对 target 仅以
  model+identity 解析；`backfill()` 复用同样逻辑。两条路径都未给真实 target 查询增加
  task owner/org filters，也未在 `_create_edge` 前校验已解析实例的归属。
- Why Existing Tests Missed It：既有关系测试只覆盖目标存在、目标缺失和正常 backfill，
  端点均未携带跨组织状态；Task 5 的 CRV-F06 只覆盖 source.model_id 错配。
- Required Tests：
  `test_direct_relation_does_not_link_foreign_target_or_trust_source_id`、
  `test_pending_backfill_does_not_link_foreign_target`；当前均以
  `xfail(strict=True, raises=KnownProductDefect, reason="CRV-F09")` 固化。
- Projectmem：#0330（open；本验证任务不修改生产逻辑）。

## CRV-F10：审核图删除成功后 DB 保存失败会暂态不一致且缺自动补偿

- Severity：P1
- Location：`server/apps/cmdb_enterprise/custom_reporting/services/cleanup_service.py:127-153`
- Trigger：pending cleanup review 的图删除成功，随后第一次保存 APPROVED 审核状态时
  注入一次性 DB 异常。
- Evidence：故障返回后，删除副作用为 `[10,11]`，数据库审核状态暂时仍为 `pending`；
  故障解除后的普通第二次 approve 能推进 `approved`，同时删除调用累计为
  `[10,11,10,11]`。反向注入图删除失败时，审核保持 pending 且 reviewed_at 为空。
- Impact：故障到人工/调用方重试之间，活动页与图状态不一致；普通重试会重复发起删除，
  是否安全依赖底层删除幂等性。当前没有自动补偿使其自行收敛，但普通重试可以恢复。
- Root Cause：`approve()` 先执行不可回滚的图删除，再保存关系库 APPROVED；两者之间无
  outbox/operation 状态、幂等代次或补偿 reconciler，数据库事务也无法回滚图副作用。
- Why Existing Tests Missed It：既有 approve 测试把删除替换为必成功列表追加，并让 DB
  保存始终成功；未对两个跨存储边界分别注入故障、重读数据库状态及验证普通重试。
- Required Tests：
  `test_review_approval_does_not_delete_without_durable_approved_state`、
  `test_review_approval_retry_advances_after_transient_db_failure`、
  `test_review_graph_failure_keeps_review_pending`；首项以
  `xfail(strict=True, raises=KnownProductDefect, reason="CRV-F10")` 固化，后两项普通通过。
- Projectmem：#0331（open；本验证任务不修改生产逻辑）。

## CRV-F11：Beat 配置的过期清理任务未进入 Worker 注册表

- Severity：P1
- Location：`server/apps/cmdb_enterprise/config.py:10-12`；
  `server/apps/cmdb_enterprise/custom_reporting/tasks.py:8-11`；
  `server/apps/cmdb_enterprise/tasks/__init__.py:2`
- Trigger：启用 Celery 与 Enterprise app，加载 Celery 默认模块后，对
  `CELERY_BEAT_SCHEDULE` 的每个 task path 检查 `app.tasks`。
- Evidence：`test_every_enterprise_beat_task_is_registered` 真实 RED 唯一列出完整任务名
  `apps.cmdb_enterprise.custom_reporting.tasks.custom_reporting_expire_cleanup`；app 级
  `apps.cmdb_enterprise.tasks` 只显式导入 attachment cleanup。确认 RED 后以
  `xfail(strict=True, raises=KnownProductDefect, reason="CRV-F11")` 精确固化，只有
  `missing` 集合精确等于该完整任务名时才抛 `KnownProductDefect`；其他缺失仍普通失败。
- Impact：Beat 可按日生成一个 Worker 不认识的任务名，过期自定义上报实例清理不会按
  计划执行，陈旧资产持续保留并造成数据准确性与存储增长风险。
- Root Cause：Celery autodiscovery 只发现 app 级 `tasks` 模块；过期任务定义在嵌套包
  `custom_reporting/tasks.py`，却没有从 app 级 tasks 包导入注册。
- Why Existing Tests Missed It：既有测试直接调用 `expire_cleanup()` 或任务函数，没有把
  Enterprise Beat task path 与 Worker 的实际注册表做集合一致性检查。
- Required Tests：保留 `test_every_enterprise_beat_task_is_registered`；修复后必须从
  strict xfail 变为普通通过，并确保任意新增或额外缺失 task path 均普通失败。
- Projectmem：#0383（open；本验证任务不修改生产逻辑）。

## CRV-F12：上报与清理链路缺少统一请求及扫描资源预算

- Severity：P1
- Location：`server/apps/cmdb_enterprise/custom_reporting/services/ingest_service.py:30-32,60-86`；
  `merge_service.py:63-81`；`relation_service.py:86-100,120-135`；
  `cleanup_service.py:208-240`；`activity_service.py:92-148`
- Trigger：合法 Token 提交高基数 `instances`/`relations`，任务积累大量
  pending/batch/review，或多个 expire 任务对应大模型。本轮只做指定源码范围的有界静态
  审计，未发送大请求、未构造高基数数据、未执行压力测试或破坏性测试。
- Evidence：已有上限为控制面任务列表 `page_size` 被裁剪至 1..200，以及 task detail 的
  `recent_batches` 输出切片；但后者切片前仍全量物化全部 batch/review。缺失上限包括：
  ingest 未限制 `instances`/`relations` 基数；每次请求遍历全部启用凭据逐 token 匹配；
  merge 按模型全量 `query_entity`；关系处理逐项查询并全量 backfill 当前任务 pending；
  expire cleanup 遍历全部启用任务且逐模型全量图扫描；batch activity/detail 全量物化历史
  batch/review。“缺失”仅指本模块源码未发现显式预算，不推断部署层限制。
- Impact：单请求或单次日清理可使 CPU、内存、数据库/图查询与副作用次数随租户历史规模
  无界增长，阻塞同步请求或 Celery worker，并放大超时、重复执行及部分完成风险。
- Root Cause：模块没有统一 `ResourceBudget` 契约；API、同步 service、图扫描和周期任务
  各自直接消费完整集合，缺少入口拒绝、游标分页、chunk、每轮截止时间与可续跑状态。
- Why Existing Tests Missed It：现有用例使用少量内存夹具并 mock 图查询，主要断言业务
  结果；未断言最大列表基数、查询 limit/cursor、chunk 大小、每轮处理上限或续跑游标。
- Required Tests：增加入口 `instances`/`relations` 上限与超限零副作用测试；凭据查找
  索引化合同；merge/expire 图查询游标与最大页大小测试；pending、batch/review 分页测试；
  周期清理每轮 deadline/最大处理量及幂等续跑测试。
- Projectmem：#0384（open；本验证任务不修改生产逻辑）。

## CRV-F13：图写成功后同步 Celery 投递失败会返回 500 并留下部分提交

- Severity：P1
- Location：`server/apps/cmdb/services/auto_relation_reconcile.py:43-49`；
  `server/apps/cmdb_enterprise/custom_reporting/services/merge_service.py:104-105`；
  `ingest_service.py:63-127`
- Trigger：真实 quick ingest 写入实例后，自动关系对账同步调用 `current_app.send_task`，
  RabbitMQ 不可用时投递异常冒泡。
- Evidence：隔离真实 HTTP 首次运行收到 500，但图实例和 Batch 已存在；Batch 只能在外围
  catch 中标记 failed，已完成的图写没有回滚。Task 9 为继续验证显式使用 DEBUG 直执行，
  没有把该环境绕行当作产品修复。
- Impact：客户端按 500 重试可能重复执行图/审计副作用；服务端呈现“请求失败”，实际数据
  已部分生效，破坏可判定性和幂等恢复。
- Root Cause：图写、关系库状态与消息投递没有 operation/outbox 边界；同步投递被放在业务
  请求关键路径中。
- Required Tests：故障注入 `send_task`，断言 HTTP 结果、Batch 状态和图事实具备一致且可恢复
  的合同；修复应使用持久化 outbox/operation 状态，不以吞异常伪装成功。
- Projectmem：#0468（open）。

## CRV-F14：API Secret 认证主体的组织结构与 CMDB 消费合同不兼容

- Severity：P0
- Location：`server/apps/core/backends.py:66-78`；`server/apps/cmdb/views/instance.py:70-82`
- Trigger：使用合法 `Api-Authorization` 调用 CMDB 管理 API。
- Evidence：`APISecretAuthBackend` 把 `user.group_list` 设为整数 team ID 列表；
  `_get_allowed_org_ids` 却无条件执行 `[i["id"] for i in request.user.group_list]`。真实预检
  在管理 API 上得到 500。Task 9 最终使用隔离 session + 当前团队完成业务 E2E，不能消除
  API Secret 入口自身的合同缺陷。
- Impact：合法机器凭据无法可靠调用 CMDB 管理接口，且错误表现为 500；自动化接入、审计
  主体和组织授权边界同时失真。
- Root Cause：认证层复用了持久化用户对象并写入与普通登录不同形态的动态属性，没有稳定的
  caller context 类型或跨认证方式合同测试。
- Required Tests：分别以 session、平台 token、API Secret 构造同组织授权主体，断言统一
  `group_list`/组织范围语义；非法组织必须 fail-closed，合法 API Secret 不得 500。
- Projectmem：#0463（open）。

## CRV-F15：模型关联创建允许缺失 mapping，后续真实上报才以 KeyError 500 失败

- Severity：P1
- Location：`server/apps/cmdb/views/model.py:267-326`；
  `server/apps/cmdb/services/instance.py`（`check_asso_mapping`）；
  `server/validation/custom_reporting/http_runner.py`（关联创建与预检）
- Trigger：创建没有 `mapping` 的模型关联后，通过自定义上报建立实例关系。
- Evidence：管理 API 接受并持久化畸形关联；真实 relation ingest 在
  `check_asso_mapping` 读取 mapping 时触发 `KeyError` 500。Runner 已改为显式创建
  `mapping="n:n"` 并在写入前校验响应，证明健康路径可运行，但生产 API 仍接受坏配置。
- Impact：配置创建成功、业务运行时才失败；错误关联可长期潜伏，导致上报批次部分成功、
  重试和清理复杂化。
- Root Cause：模型关联写入口和关系执行入口没有共享同一完整 schema/invariant。
- Required Tests：创建/更新关联时拒绝缺失或非法 mapping；存量畸形关联必须可检测且禁止
  进入关系写路径；错误响应应为明确 4xx。
- Projectmem：#0471 的 Runner 防护已完成；生产入口缺陷仍需独立修复。

## CRV-F16：失效、轮换或撤销的上报 Token 被映射成 HTTP 500

- Severity：P1
- Location：`server/apps/cmdb/views/custom_reporting.py:79-89`；
  `server/apps/cmdb_enterprise/custom_reporting/services/ingest_service.py:15-34`
- Trigger：缺失、随机、已轮换旧 Token 或已撤销 Token 调用 open ingest。
- Evidence：quick 与 standard 两次真实 E2E 都在轮换后的旧 Token 负向请求得到 500；服务端
  日志显示 `_resolve_credential` 抛 `BaseAppException("上报令牌无效或已作废")`，View 直接
  包在 `response_success` 调用中，没有稳定的认证错误映射。严格 xfail 已参数化缺失、随机、
  轮换旧和撤销四类 token；只有 resolver 精确收到提交值、零 Batch 副作用且 HTTP=500 时才
  分类为 `KnownProductDefect`，其他 500 会普通失败。
- Impact：调用方无法区分凭据失效与服务器故障，会对不可恢复认证错误进行重试；500 还会
  误导告警、可用性统计和安全审计。
- Root Cause：`authentication_classes=[]` 的 open View 自行解析 Token，但没有把业务认证
  异常转换为 401/403 的统一 `ErrorEnvelope`。
- Required Tests：缺失/随机/旧/撤销 Token 都返回一致 401（或项目统一的 403）且零 Batch、
  零图写；有效新 Token 仍成功；异常响应不得包含 token 或内部栈。
- Projectmem：#0477（open）。

## CRV-F17：空 snapshot 可直接删除作用域内全部旧实例

- Severity：P0
- Location：`server/apps/cmdb_enterprise/custom_reporting/services/ingest_service.py:60,88-101`；
  `cleanup_service.py:70-108`
- Trigger：snapshot 任务提交合法空 `instances=[]`，且未设置正数人工审核阈值（默认 0）。
- Evidence：merge 仍返回 `old_data`，covered_ids 为空；`apply_snapshot` 把全部 old_ids 作为
  delete_ids，`if threshold` 在 0 时为假并直接删除。结合 F08，当前 old_ids 还未按 owner/org
  隔离。
- Impact：上游短暂空采集、过滤错误或恶意空请求都可能触发全量资产删除，并可跨任务/组织
  放大，属于数据丢失边界。
- Required Tests：空 snapshot 默认零副作用；只有显式确认的“authoritative empty snapshot”
  才能进入审核，且候选集必须先按 task/team owner 收敛。
- Projectmem：#0481（open）。

## CRV-F18：待删实例查询失败被吞后仍执行无审计图删除

- Severity：P1
- Location：`server/apps/cmdb_enterprise/custom_reporting/services/cleanup_service.py:38-62`
- Trigger：`query_entity_by_ids` 因连接、解码或服务异常失败，但后续图删除可用。
- Evidence：宽泛 `except Exception` 把 inst_list 置空，代码随后仍调用删除；因为审计循环消费
  空列表，资产删除不会生成 ChangeRecord。
- Impact：故障窗口中资产可消失且没有审计线索，恢复和责任追踪均失去事实基础。
- Required Tests：任何候选事实查询异常必须 fail-close 且零删除；仅明确“不存在”可幂等跳过。
- Projectmem：#0482（open）。

## CRV-F19：quick 模型先于 DB 事务写图，失败会留下孤儿/半模型

- Severity：P1
- Location：`server/apps/cmdb_enterprise/custom_reporting/services/task_service.py:226-257`；
  `model_service.py:31-69`
- Trigger：图模型/部分属性创建成功后，Task 或 Credential 创建失败。
- Evidence：quick bootstrap 在 `transaction.atomic()` 之前执行，且 bootstrap 本身包含模型与
  多个属性的多步图写；关系库回滚不能撤销这些图事实。
- Impact：控制面显示创建失败，图中却留下可冲突的模型、subordinate edge 或半 schema；重试
  可能失败或复用错误资源。
- Required Tests：逐写点故障注入；以 operation/reconciler 核对图事实并补偿或续跑，不以裸
  DB 事务宣称跨存储原子性。
- Projectmem：#0483（open）。

## CRV-F20：任务更新先提交 DB，再同步图模型组织导致授权分叉

- Severity：P1
- Location：`server/apps/cmdb_enterprise/custom_reporting/services/task_service.py:293-316`
- Trigger：task team/config 保存成功后，`sync_model_group` 图写失败。
- Evidence：DB `atomic` 已退出提交，模型组织同步随后才执行；异常返回 500 时 Task/Scope 已是
  新组织，而图模型仍属于旧 group。
- Impact：管理授权、任务配置和资产模型组织出现持久分叉，重试又会基于已变化的授权前提。
- Required Tests：图失败、超时和重试合同；用版本化 operation/CAS 让 DB 期望状态与图实际
  状态可观察、可对账。
- Projectmem：#0484（open）。

## CRV-F21：并发批准同一清理审核可重复删除和审计

- Severity：P1
- Location：`server/apps/cmdb_enterprise/custom_reporting/services/cleanup_service.py:116-151`
- Trigger：两个请求同时读取同一 pending review 并执行 approve。
- Evidence：普通读取后检查 status，没有 `select_for_update`、lease、generation 或条件更新；
  两个请求都能进入 `_delete_instances`。
- Impact：重复图删除、重复/缺失审计和状态竞争；与 F10 的“先删图后存 DB”窗口叠加后恢复
  结果不可判定。
- Required Tests：并发双 approve 只有一个 winner；删除 operation 幂等且最终状态由 CAS 推进。
- Projectmem：#0485（open）。

## CRV-F22：同批重复 identity 后写静默覆盖，summary 不报丢数据

- Severity：P1
- Location：`server/apps/cmdb/collection/common.py:54-73`；
  `server/apps/cmdb_enterprise/custom_reporting/services/merge_service.py:146-154`
- Trigger：同一 payload 提交两条 identity tuple 相同但业务字段不同的实例。
- Evidence：`new_map` 与批次 index 都用 dict 赋值，后项覆盖前项；后续只遍历 map，未增加
  errors。请求仍可报告 `instances_received=2`。
- Impact：客户端得到成功语义但一条输入静默丢失，且最终值依赖 payload 顺序。
- Required Tests：批内重复 identity 在任何图写前返回明确 4xx；不得以“最后写胜出”隐式处理。
- Projectmem：#0486（open）。

## CRV-F23：poison pending 可让所有后续 ingest 在部分写入后持续失败

- Severity：P1
- Location：`server/apps/cmdb_enterprise/custom_reporting/services/ingest_service.py:83-86,123-127`；
  `relation_service.py:120-136`
- Trigger：pending 的 association 被删除、mapping 畸形或 payload 损坏，之后 source/target 变得
  可解析。
- Evidence：每次 ingest 只要存在任一 pending 就同步遍历全表；backfill 没有逐条异常隔离、
  重试状态或 dead-letter，一条 `_create_edge` 异常会在实例已写后让整批失败。
- Impact：单条毒化记录可永久阻断任务后续上报，并反复制造“图已写、HTTP 500”的部分提交。
- Required Tests：单条失败不阻断其他 pending/当前批次；有限重试、错误状态、dead-letter 与
  分页预算必须可观察。
- Projectmem：#0487（open）。

## CRV-F24：生产关系逻辑把 FalkorDB 合法 ID=0 当作不存在

- Severity：P1
- Location：`server/apps/cmdb_enterprise/custom_reporting/services/relation_service.py:91-99,125-135`
- Trigger：source 是 FalkorDB 首个零基节点（`_id=0`）。
- Evidence：`process` 使用 `if src_id`，backfill 使用 `if not src_id`/`if src_id`；真实 Runner 已
  证明 FalkorDB node/edge ID 合法从 0 开始，但生产 relation service 没有同等合同。
- Impact：合法关系被写入 pending 且可能永久无法回填，表现随图内创建顺序变化。
- Required Tests：显式使用 `is None` 判断；覆盖 process/backfill 的 ID=0 正向与负数/布尔拒绝。
- Projectmem：#0488（open）。
