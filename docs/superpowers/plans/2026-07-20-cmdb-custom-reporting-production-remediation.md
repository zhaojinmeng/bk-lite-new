# CMDB 自定义上报 24 项缺陷修复实施计划

> **执行要求：** 使用 `subagent-driven-development` 或 `executing-plans` 逐任务执行；每个缺陷严格遵守 TDD，先运行单个 RED 用例确认失败原因，再写最小生产代码。完成声明前必须使用 `verification-before-completion`。

**目标：** 修复标准模型和快速模型自定义上报已确认的 F01–F24，并建立可恢复的权限、Schema、所有权、Operation/Outbox/Reconciler 和资源预算边界。

**架构：** Enterprise 自定义上报新增专用持久化操作状态机。入口先构造 `CallerContext`、编译 `CompiledReportingSchema`、生成 `OwnedInstanceScope/Ref`，跨 DB/FalkorDB/Broker 副作用由 `CustomReportingOperation`、`CustomReportingOutbox`、`CustomReportingReconciler` 和 `PendingRelationDelivery` 承担。所有条件推进使用 ORM CAS，不使用原生 SQL。

**技术栈：** Python 3.12、Django 4.2、DRF、Celery、FalkorDB、pytest、pytest-django。

**设计规格：** `docs/superpowers/specs/2026-07-20-cmdb-custom-reporting-production-remediation-design.md`

---

## 统一执行约束

每个任务重复以下微循环：

1. 对所有将修改的文件调用 `precheck_file(path)`。
2. 写一个行为级测试或移除该用例的 strict-xfail 标记。
3. 仅运行该测试，确认它因目标缺陷失败，而不是导入、环境或 mock 错误。
4. 写最小生产代码。
5. 重跑单测确认 GREEN，再跑同域测试。
6. 调用 `record_attempt(..., outcome="worked|partial|failed")`；只有验证充分才 `record_fix(issue_id=...)`。
7. 一个逻辑批次一个中文提交，禁止把多个未验证尝试揉成一个提交。

所有 pytest 命令在 `server/` 执行并使用：

```bash
MINIO_ENDPOINT=localhost:9000 \
MINIO_ACCESS_KEY=test \
MINIO_SECRET_KEY=test \
MINIO_USE_HTTPS=false \
INSTALL_APPS=system_mgmt,node_mgmt,cmdb,cmdb_enterprise \
DB_ENGINE=sqlite DB_NAME=:memory: SECRET_KEY=test ENABLE_CELERY=true \
uv run pytest -q -o addopts='' --nomigrations
```

聚焦用例命令在上述前缀后追加测试文件或 `file.py::test_name`。涉及迁移/真实 ORM 约束时移除 `--nomigrations`。

## Task 1：建立 F17–F24 正式 RED 契约

**文件：**

- 修改：`server/validation/custom_reporting/tests/test_failure_boundaries.py`
- 修改：`server/validation/custom_reporting/tests/test_runtime_contracts.py`
- 新增：`server/validation/custom_reporting/tests/test_recovery_contracts.py`
- 新增：`server/validation/custom_reporting/tests/test_resource_budget_contracts.py`

**RED 用例：**

- F17：空 snapshot 未声明 authoritative 时零副作用；声明后强制审核。
- F18：删除前事实查询异常时不得调用图删除。
- F19：快速任务创建每个图/DB 写点失败后均存在可恢复 operation，不产生无主图事实。
- F20：图组织同步失败时 effective team 不改变，并保留 retryable desired state。
- F21：并发 approve 只有一个 CAS winner、一次删除。
- F22：重复 identity 在任何图写前整批拒绝。
- F23：poison pending 被隔离/dead-letter，不使新 ingest 失败。
- F24：process/backfill 接受 ID 0，并拒绝 bool/负数。

先运行这四个文件并确认新增用例全部以目标坏状态失败。此任务只新增测试，不修改生产代码。

**提交：**

```bash
git add server/validation/custom_reporting/tests
git commit -m "test(cmdb): 固化自定义上报F17至F24失败契约"
```

## Task 2：统一调用者上下文和控制面权限（F01、F02、F14、F16）

**文件：**

- 修改：`server/apps/cmdb/views/custom_reporting.py`
- 修改：`server/apps/cmdb_enterprise/custom_reporting/provider.py`
- 修改：`server/apps/cmdb_enterprise/custom_reporting/services/ingest_service.py`
- 修改：`server/apps/core/backends.py` 或复用现有 group normalizer 的消费端
- 新增：`server/apps/cmdb_enterprise/custom_reporting/services/caller_context.py`
- 修改：`server/validation/custom_reporting/tests/test_runtime_contracts.py`
- 修改/新增：`server/apps/cmdb_enterprise/tests/test_custom_reporting_authz.py`

**顺序：**

1. 逐个运行 F01、F02、F14、F16 现有 strict-xfail，确认仍命中越权、缺权限、group TypeError、500 映射。
2. 先实现 `CallerContext` 和 group 规范化测试，不接业务入口。
3. 接入 create/update 的目标 team 子集校验，确保副作用 mock 为零。
4. 为全部控制面 action 建权限矩阵并应用 `HasPermission`。
5. token resolver 改用 typed 401 异常，验证四种无效 token 均零 Batch/零图写。
6. 跑 API Secret 现状测试，确保合法 secret 仍绑定单一 team。

**聚焦验证：**

```bash
uv run pytest -q -o addopts='' --nomigrations \
  validation/custom_reporting/tests/test_runtime_contracts.py \
  apps/core/tests/test_api_secret_hash_auth.py \
  apps/cmdb_enterprise/tests/test_custom_reporting_authz.py
```

**提交：** `fix(cmdb): 收紧自定义上报调用者与权限边界`

## Task 3：编译任务和实例 Schema（F03、F04、F05、F22）

**文件：**

- 新增：`server/apps/cmdb_enterprise/custom_reporting/services/schema_service.py`
- 修改：`server/apps/cmdb_enterprise/custom_reporting/services/task_service.py`
- 修改：`server/apps/cmdb_enterprise/custom_reporting/services/ingest_service.py`
- 修改：`server/apps/cmdb_enterprise/custom_reporting/services/model_service.py`
- 修改：`server/apps/cmdb_enterprise/custom_reporting/services/merge_service.py`
- 修改：`server/validation/custom_reporting/tests/test_runtime_contracts.py`
- 修改：`server/validation/custom_reporting/tests/test_failure_boundaries.py`

**顺序：**

1. RED：空/空白/保留 identity，standard 未知/保留字段，quick `_id`，重复 identity。
2. 实现纯函数 `compile_task_schema()`，验证 identity 非空、唯一、非保留。
3. 实现 `normalize_instances()`：standard 拒绝未知字段；quick 登记和 merge 共用规范化字段；服务端覆盖时间戳。
4. identity 类型规范化后批次查重，发现重复立即拒绝。
5. task create/update 和 ingest 双边接入，保护存量坏配置。

**最小 GREEN 标准：** 所有失败都发生在字段登记、Batch 和 GraphClient 之前；有效双模式基线不变。

**提交：** `fix(cmdb): 在图写前编译自定义上报Schema`

## Task 4：关系 Schema 和 GraphId（F06、F15、F24）

**文件：**

- 修改：`server/apps/cmdb/views/model.py`
- 修改：`server/apps/cmdb/services/model.py`
- 修改：`server/apps/cmdb/services/instance.py`
- 新增：`server/apps/cmdb_enterprise/custom_reporting/services/value_objects.py`
- 修改：`server/apps/cmdb_enterprise/custom_reporting/services/relation_service.py`
- 修改：`server/apps/cmdb/tests/test_model_views.py`
- 修改：`server/validation/custom_reporting/tests/test_failure_boundaries.py`
- 修改：`server/validation/custom_reporting/tests/test_recovery_contracts.py`

**顺序：**

1. RED：source model mismatch、关联缺/非法 mapping、legacy mapping 缺失、ID 0、bool、负数。
2. 模型关联写入口使用 required `ChoiceField` 或等价 serializer；Service 重复校验内部调用。
3. `GraphId` 仅接受非 bool 的非负 int；所有缺失判断改为 `is None`。
4. relation plan 在 pending/图写前验证 task model、association endpoints 和 mapping。
5. 存量无 mapping 关联返回明确业务异常，禁止实例边写入。

**提交：** `fix(cmdb): 统一关系Schema与零基图ID语义`

## Task 5：OwnedInstanceScope 和关系端点归属（F08、F09）

**文件：**

- 新增：`server/apps/cmdb_enterprise/custom_reporting/services/ownership_service.py`
- 修改：`server/apps/cmdb_enterprise/custom_reporting/services/merge_service.py`
- 修改：`server/apps/cmdb_enterprise/custom_reporting/services/relation_service.py`
- 修改：`server/apps/cmdb_enterprise/custom_reporting/services/cleanup_service.py`
- 修改：`server/validation/custom_reporting/tests/test_failure_boundaries.py`

**顺序：**

1. RED：old_data 查询必须包含 model/collect_task/organization；direct 和 pending 不得连接 foreign source/target。
2. 实现不可变 `OwnedInstanceScope` 和 `OwnedInstanceRef`。
3. bulk old_data 查询把联合 scope 下推到 GraphClient。
4. `_id` 和 identity 统一走 owner resolver；process/backfill 共用。
5. snapshot/delete 前对候选再次验证 scope。

**提交：** `fix(cmdb): 隔离自定义上报资产与关系所有权`

## Task 6：部分失败 phase gate 和空快照安全（F07、F17、F18）

**文件：**

- 修改：`server/apps/cmdb/serializers/custom_reporting.py`
- 修改：`server/apps/cmdb_enterprise/custom_reporting/services/ingest_service.py`
- 修改：`server/apps/cmdb_enterprise/custom_reporting/services/cleanup_service.py`
- 修改：`server/validation/custom_reporting/tests/test_failure_boundaries.py`
- 修改：`server/validation/custom_reporting/tests/test_recovery_contracts.py`

**顺序：**

1. RED：partial merge 禁止 relation/backfill/snapshot；空 snapshot 两态；事实查询异常零删除。
2. merge `errors>0` 立即进入失败 phase，禁止所有危险后续阶段。
3. serializer 区分缺失 instances、显式空和 `authoritative_empty`。
4. 未声明权威空返回 400；声明权威空无条件进入审核。
5. `_delete_instances` 查询异常直接 fail closed；只有成功查询为空才允许幂等跳过。

**提交：** `fix(cmdb): 阻断部分失败与不安全空快照清理`

## Task 7：新增 Operation、Outbox 和迁移骨架

**文件：**

- 修改：`server/apps/cmdb_enterprise/custom_reporting/models.py`
- 新增：`server/apps/cmdb_enterprise/migrations/0004_custom_reporting_operations.py`
- 新增：`server/apps/cmdb_enterprise/custom_reporting/services/operation_service.py`
- 新增：`server/apps/cmdb_enterprise/tests/test_custom_reporting_operation_service.py`

**RED 合同：**

- 相同 scope/idempotency/request hash 返回同一 operation；摘要不同冲突。
- 同一 generation 只有一个 lease owner。
- 旧 owner、过期 generation 不能 finalize。
- Outbox `(operation,event_type,dedupe_key)` 唯一。
- retry/backoff/lease 接管保持可恢复。

先写真实 ORM RED，再新增最小模型、约束和 CAS Service。迁移使用 Django ORM，运行 `makemigrations --check --dry-run` 验证没有遗漏。

**提交：** `feat(cmdb): 建立自定义上报持久化操作状态机`

## Task 8：快速任务 provision/update 恢复（F19、F20）

**文件：**

- 修改：`server/apps/cmdb_enterprise/custom_reporting/models.py`
- 修改：`server/apps/cmdb_enterprise/migrations/0004_custom_reporting_operations.py` 或新增后续迁移
- 修改：`server/apps/cmdb_enterprise/custom_reporting/services/task_service.py`
- 修改：`server/apps/cmdb_enterprise/custom_reporting/services/model_service.py`
- 新增：`server/apps/cmdb_enterprise/custom_reporting/services/reconcile_service.py`
- 新增：`server/apps/cmdb_enterprise/tests/test_custom_reporting_task_recovery.py`
- 修改：`server/validation/custom_reporting/tests/test_recovery_contracts.py`

**顺序：**

1. RED：对 model、subordinate、field group、attr、task、credential 每个写点故障注入。
2. task 增加 state_version/effective/desired/sync status。
3. 创建先持久化 desired operation，再以自然键/operation marker 续写图事实。
4. task/credential 未完成前不暴露 active token。
5. 更新保持 effective team，图成功后 generation CAS 提升 desired。
6. Reconciler 核对事实续跑；未知归属进入 manual_failed，不盲删。

**提交：** `fix(cmdb): 使快速任务创建更新可恢复`

## Task 9：清理审核单执行者和恢复（F10、F21）

**文件：**

- 修改：`server/apps/cmdb_enterprise/custom_reporting/services/cleanup_service.py`
- 修改：`server/apps/cmdb_enterprise/custom_reporting/services/operation_service.py`
- 修改：`server/apps/cmdb_enterprise/custom_reporting/services/reconcile_service.py`
- 修改：`server/apps/cmdb_enterprise/tests/test_custom_reporting_cleanup_service.py`
- 修改：`server/validation/custom_reporting/tests/test_recovery_contracts.py`

**顺序：**

1. RED：两个并发批准只有一个 winner；claim 失败零图删；图成功/DB finalize 失败不二次删除。
2. ORM 条件更新 `pending→approving` 并创建唯一 cleanup operation。
3. 保存候选和删除前 fact snapshot 后才允许图删。
4. 图结果写 operation fact；审核状态和审计由 finalize/outbox 推进。
5. Reconciler 对 graph_applied 状态只 finalize，不重放不可判定删除。

**提交：** `fix(cmdb): 让清理审核并发安全且可恢复`

## Task 10：上报 Outbox 与幂等恢复（F13）

**文件：**

- 修改：`server/apps/cmdb_enterprise/custom_reporting/services/ingest_service.py`
- 修改：`server/apps/cmdb/services/auto_relation_reconcile.py`
- 修改：`server/apps/cmdb_enterprise/custom_reporting/services/operation_service.py`
- 修改：`server/apps/cmdb_enterprise/custom_reporting/tasks.py`
- 新增：`server/apps/cmdb_enterprise/tests/test_custom_reporting_ingest_recovery.py`
- 修改：`server/validation/custom_reporting/tests/test_recovery_contracts.py`

**顺序：**

1. RED：broker failure 后 graph fact 唯一、Batch/Operation 可恢复、Outbox retry；同幂等键不重复图写。
2. 请求读取稳定 Idempotency-Key；相同键/摘要返回已有结果或当前状态。
3. 自动关系派发先写 Outbox，移除主图写路径里的同步 `send_task` 失败传播。
4. Outbox Worker 使用 lease/attempt/backoff；发送成功后幂等 finalize。
5. 故障注入验证 HTTP 不把已确认图事实包装成可盲重试的 500。

**提交：** `fix(cmdb): 通过Outbox恢复上报后置动作`

## Task 11：PendingRelationDelivery 状态机（F23）

**文件：**

- 修改：`server/apps/cmdb_enterprise/custom_reporting/models.py`
- 新增迁移：`server/apps/cmdb_enterprise/migrations/0005_pending_relation_delivery.py`
- 修改：`server/apps/cmdb_enterprise/custom_reporting/services/relation_service.py`
- 修改：`server/apps/cmdb_enterprise/custom_reporting/tasks.py`
- 新增：`server/apps/cmdb_enterprise/tests/test_pending_relation_delivery.py`
- 修改：`server/validation/custom_reporting/tests/test_recovery_contracts.py`

**顺序：**

1. RED：fingerprint 去重、poison 隔离、逐条继续、dead-letter 不重试、lease 接管。
2. 增加状态、attempt、next retry、last error、owner lease、generation 和唯一 fingerprint。
3. process 仅持久化规范化 pending；backfill 改为分页 claim Worker。
4. 瞬时错误退避；确定性 Schema/association 错误 dead-letter。
5. ingest 主路径不因历史 pending 失败。

**提交：** `fix(cmdb): 隔离并治理毒化待补关系`

## Task 12：任务注册和 Reconciler 调度（F11）

**文件：**

- 修改：`server/apps/cmdb_enterprise/tasks/__init__.py`
- 修改：`server/apps/cmdb_enterprise/custom_reporting/tasks.py`
- 修改：`server/apps/cmdb_enterprise/config.py`
- 修改：`server/validation/custom_reporting/tests/test_task_registration.py`

**顺序：**

1. RED：默认模块导入后，所有 Enterprise Beat task 必须在 registry。
2. app 级 tasks 入口显式导入 custom reporting tasks。
3. 为 operation/outbox/pending Reconciler 配置独立、有界 Beat entry。
4. 再次执行集合合同，任何 missing task 都失败。

**提交：** `fix(cmdb): 注册自定义上报周期恢复任务`

## Task 13：ResourceBudget 和常数级 token lookup（F12）

**文件：**

- 新增：`server/apps/cmdb_enterprise/custom_reporting/services/resource_budget.py`
- 修改：`server/apps/cmdb_enterprise/custom_reporting/models.py`
- 新增迁移：`server/apps/cmdb_enterprise/migrations/0006_custom_reporting_token_lookup.py`
- 修改：`server/apps/cmdb/views/custom_reporting.py`
- 修改：`server/apps/cmdb_enterprise/custom_reporting/services/ingest_service.py`
- 修改：`server/apps/cmdb_enterprise/custom_reporting/services/merge_service.py`
- 修改：`server/apps/cmdb_enterprise/custom_reporting/services/cleanup_service.py`
- 修改：`server/apps/cmdb_enterprise/custom_reporting/services/relation_service.py`
- 修改：`server/validation/custom_reporting/tests/test_resource_budget_contracts.py`

**RED 合同：**

- body/instances/relations/fields 超预算在 Batch/图写前拒绝；
- token lookup 查询量与凭据总数无关；
- old_data 使用 ID keyset cursor、固定 page、`include_count=False`；
- pending/expire 遵守 page、deadline、checkpoint；
- 达到预算保存 continuation，不静默标 success。

实现集中 `ResourceBudget`，避免各 Service 使用不一致魔法数字。token 保存高熵摘要的设计不变，只增加可索引 lookup 字段/前缀并继续使用常量时间完整摘要比较。

**提交：** `fix(cmdb): 限制自定义上报入口与扫描资源预算`

## Task 14：存量数据治理迁移和管理命令

**文件：**

- 新增：`server/apps/cmdb_enterprise/management/commands/audit_custom_reporting_state.py`
- 新增：`server/apps/cmdb_enterprise/tests/test_audit_custom_reporting_state.py`
- 修改相关迁移文件

**RED 合同：**

- 非法 identity/config 只标 degraded，不改图；
- 缺 mapping 只报告/manual_failed，不填默认值；
- pending 只有完全相同 fingerprint 才去重；
- 无法证明 owner 的图事实不删除；
- dry-run 零写入，apply 幂等可重跑。

命令先输出 JSON 审计报告，再提供显式 `--apply-safe-fixes`，不得默认执行破坏性修复。

**提交：** `feat(cmdb): 增加自定义上报存量状态审计`

## Task 15：聚焦回归、覆盖率与迁移验证

**验证：**

```bash
uv run pytest -q -o addopts='' --nomigrations \
  validation/custom_reporting/tests \
  apps/cmdb_enterprise/tests

uv run pytest -q -o addopts='' --nomigrations \
  apps/cmdb/tests/test_model_views.py \
  apps/cmdb/tests/test_model_custom_reporting_delegation.py \
  apps/cmdb/tests/test_auto_relation_reconcile_svc.py \
  apps/core/tests/test_api_secret_hash_auth.py

uv run python manage.py makemigrations --check --dry-run
uv run python manage.py migrate --plan

uv run pytest -q -o addopts='' --nomigrations \
  --cov=apps.cmdb_enterprise.custom_reporting \
  --cov-report=term-missing \
  validation/custom_reporting/tests apps/cmdb_enterprise/tests

uv run black --check <本次触及的 Python 文件>
uv run isort --check-only <本次触及的 Python 文件>
uv run flake8 <本次触及的 Python 文件>
git diff --check
```

要求：24 项用例全部普通通过，不保留与这些缺陷对应的 xfail；修改代码覆盖率至少 75%，权限/状态机/幂等核心路径目标 90%。

## Task 16：真实双模式 E2E 与故障注入

**文件：**

- 修改：`server/validation/custom_reporting/http_runner.py`
- 修改：`server/validation/custom_reporting/tests/test_http_runner.py`
- 更新：`docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/`

依次运行标准模式和快速模式真实 HTTP + FalkorDB：

- 创建、签发 token、首次上报、增量、关系、snapshot、expire、凭据轮换/吊销；
- 无效 token、无权限、跨 team、重复 identity、非法字段、非法 mapping；
- 空 snapshot、部分 merge、ID 0；
- Broker 不可用、图成功/DB finalize 失败、DB desired/图失败；
- 并发 approve、lease 接管、旧 owner finalize；
- poison pending、dead-letter 和恢复；
- 超预算和 checkpoint 续跑。

Runner 清理必须继续保留 preflight、普通 DELETE 和失败账本，不得为了测试便利引入破坏性强制清理。

## Task 17：完成前独立验证与交付

1. 调用 `verification-before-completion`，重新运行 Task 15–16 的全部命令并逐项读取退出码和统计。
2. 调用 `requesting-code-review`，进行独立权限、并发、跨存储、迁移和测试有效性审查。
3. 对审查发现重复 RED→GREEN 循环，不直接按评论改代码。
4. 对 F01–F24 分别附测试、提交和验证证据，逐项 `record_fix`。
5. 仅当没有未解决 P0/P1、所有门禁为新鲜 GREEN 时，更新放行结论。

**最终提交：** `fix(cmdb): 完成自定义上报24项生产缺陷修复`
