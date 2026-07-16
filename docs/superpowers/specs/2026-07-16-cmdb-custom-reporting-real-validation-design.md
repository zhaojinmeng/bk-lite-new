# CMDB 自定义上报双模式真实验证设计

## 1. 目标

对 CMDB 自定义上报的标准模式与快速模式执行生产级验证，不以“现有测试通过”替代真实结论。验证同时覆盖：

- 从任务控制面到公开上报数据面的完整调用链；
- 真实关系库、真实 FalkorDB、真实 HTTP 接口及 Celery/Beat 注册；
- 权限、组织隔离、凭据、Schema、幂等、并发、清理与故障恢复；
- 架构边界、代码质量、测试质量和发布可追溯性；
- 缺陷分级、可重复证据、根因与修复验收条件。

验证阶段不修改生产逻辑。发现缺陷后先记录、复现和确认根因；是否进入修复由后续计划单独安排，修复必须遵守 TDD。

## 2. 已确认事实与基线

### 2.1 社区层

社区层保留稳定 URL、序列化器和 `CustomReportingExtension` 契约。未安装企业 overlay 时，列表与统计返回空结果，任务写入、凭据、审核和 ingest 明确拒绝。隔离 worktree 的相关基线为 6 项测试通过。

### 2.2 锁定的 Enterprise 交付基线

主仓当前 gitlink 锁定 `enterprise@1e9c3d2d0ea6b95fa8d57aa326b511187d48350e`。该提交包含自定义上报模型和少量模型测试，但不包含 provider、任务服务、ingest、merge、relation、cleanup 等完整行为实现，因此不能独立重建主工作区当前运行态能力。

这不是测试环境细节，而是发布可追溯性与可重建性缺陷。验证报告必须分别陈述“锁定交付物能证明什么”和“运行态 overlay 能证明什么”，禁止把两者混为同一版本。

### 2.3 当前运行态 overlay

主工作区存在被 Git 忽略的 `server/apps/cmdb_enterprise/`，包含当前自定义上报行为实现。现有完整定向测试在显式 SQLite 内存库下为 81 项通过，相关汇总覆盖率为 83%；该结果只能证明现有断言，不足以覆盖已确认的跨组织、部分失败、空身份键和清理边界。

### 2.4 现有 E2E 脚本不可直接使用

`server/scripts/custom_reporting_e2e_test.py` 会复用已有任务、重新签发已有凭据、创建共享模型关联，并在 cleanup 中删除全部 `CustomReportingPendingRelation`。它不满足测试隔离与精准回滚要求，验证不得直接执行该脚本。

## 3. 双基线策略

### 基线 A：可追溯交付基线

验证主仓提交、gitlink、Enterprise 提交、文件清单和安装结构是否能够独立构建自定义上报。若锁定提交缺少核心实现，结论直接记为交付阻断；不得从主工作区复制文件后宣称 gitlink 已通过。

### 基线 B：当前运行态行为基线

将主工作区运行态 overlay 作为独立测试制品导入隔离 worktree，导入前后执行稳定逐文件 SHA-256：

- 排除 `__pycache__`、`.pyc`、缓存、覆盖率和运行产物；
- 记录源文件相对路径、单文件哈希、聚合哈希和采集时间；
- 确认导入前后清单完全一致；
- 报告中将其标记为“固定运行态制品”，不赋予不存在的 Git 提交身份。

两套基线分别出结论，最终发布建议取更严格结果。

## 4. 验证架构

验证分三层，避免用 mock 通过掩盖真实系统失败。

### 4.1 L1：契约与故障注入测试

使用隔离关系库和可控图客户端，验证难以在真实环境稳定制造的失败：单条新增失败、单条更新失败、关系创建失败、图删除后数据库写入失败、并发审核、重复请求和批次状态迁移。

本层允许在外部边界使用 fake，但必须断言真实服务编排、数据库状态和副作用；不允许只测试 mock 调用次数。

### 4.2 L2：Django 集成测试

通过真实 serializer、ViewSet、provider、ORM、凭据摘要、扩展注册和 Celery app，验证：

- 控制面登录态授权与组织裁剪；
- 公开 ingest 的 Bearer/raw token 认证；
- 标准/快速任务创建、更新、停用、轮换和作废；
- Batch、Scope、Credential、PendingRelation、CleanupReview 的持久化；
- Celery autodiscover 与 Beat task name 一致性。

### 4.3 L3：真实 HTTP + 真实 FalkorDB E2E

对允许写入的开发/测试环境执行真实链路：

```text
登录态 HTTP
  -> 创建隔离模型/任务
  -> 签发一次性 Token
  -> 公开 ingest HTTP
  -> Batch/字段登记/实例 upsert
  -> FalkorDB 节点与关系
  -> 待关联与回填
  -> snapshot/expire/审核
  -> 查询 API 与最终数据核对
```

E2E 不直接调用 `ingest_service` 代替 HTTP，不复用已有任务或凭据，不以 fake graph 代替 FalkorDB。

## 5. 测试数据隔离与安全

每次运行生成唯一 `run_id`，所有资源使用统一前缀，例如 `crval_<UTC时间>_<随机后缀>`。运行账本记录本次创建的：

- 模型、模型属性与模型关联 ID；
- 标准任务、快速任务、Scope、Credential 和 Batch ID；
- 图实例、关系、PendingRelation 与 CleanupReview ID；
- 测试请求指纹和 snapshot generation。

清理遵循逆序和精准 ID：先关系，再实例，再审核/待关联/批次/凭据/任务，最后删除测试模型与关联定义。禁止全表删除、按宽泛前缀删除、轮换非本次凭据或接管历史无 owner 数据。

执行前先 dry-run 输出计划创建/删除的资源类型；执行后做残留扫描。若清理失败，保留账本并停止后续高风险场景，不尝试扩大删除范围。

## 6. 两种模式的正向流程

### 6.1 标准模式

1. 创建专用标准模型，预先声明身份键和业务字段。
2. 以允许组织创建 standard 任务并签发 Token。
3. 上报多实例、多类型字段，确认只接受已声明字段。
4. 使用相同身份键重复上报，确认更新而非新增。
5. 验证服务端字段、organization、owner task 和变更记录。
6. 验证立即建边、目标缺失转 pending、目标出现后回填。
7. 验证停用、Token 轮换和作废后的拒收。

### 6.2 快速模式

1. 通过 quick task 创建专用模型与身份键。
2. 上报新业务字段，确认字段注册、类型建议和模型属性同步。
3. 重复字段、私有字段、保留字段和类型漂移分别验证。
4. 重复上报验证身份归一化、幂等更新和字段稳定性。
5. 验证组织组同步、任务更新和旧 Token 能力范围。
6. 复用关系、pending/backfill 和清理场景，确认行为与标准模式只有预期差异。

## 7. 负向、故障与安全场景

### 7.1 权限与组织隔离

- 无功能权限访问任务 CRUD、凭据和审核接口；
- 创建或更新到未授权组织、混合授权组织和空组织；
- 同模型、同身份、不同任务/不同组织不得互相覆盖；
- snapshot/expire 不得触碰人工、自动采集或其他自定义上报来源；
- relation source/target 必须同时属于允许模型、任务和组织。

### 7.2 Schema 与身份

- identity keys 缺失、空、重复、未知、值为空或类型不可转换；
- standard 未知字段、quick 新字段 allowlist、类型冲突；
- `_creator`、`_updater`、`_id` 等保留字段；
- 服务端覆盖字段 `model_id`、`organization`、`collect_task`、`auto_collect`、`collect_time`；
- 关联方向、关联定义和真实端点模型不一致。

拒绝场景必须断言零模型字段、零实例、零边、零 pending、零 cleanup 副作用。

### 7.3 一致性、幂等与恢复

- 单条 add/update 失败时 Batch 不得 SUCCESS，snapshot 不得启动；
- 相同 idempotency key、相同 payload 和重复 pending 不得产生重复副作用；
- 图写成功但关系库写失败、图删成功但审核状态未更新；
- 审核 approve/reject 并发、Worker 重试和进程中断；
- snapshot 阈值小于、等于、大于实际删除比例；
- last_reported_at 只在完整成功后推进。

### 7.4 资源边界

验证请求体字节数、实例数、关系数、字段数、字段名/值长度、分页、pending 扫描、模型全量扫描和处理时限。若实现没有明确上限，记录为缺陷，不以压力打满共享服务来证明风险。

## 8. 代码质量与架构审查

审查沿真实调用链逐层进行：

- **边界清晰度**：View、provider、task/ingest/merge/relation/cleanup 的职责是否单一；
- **授权一致性**：控制面组织权限、Token capability 与图写范围是否同源；
- **Schema 契约**：standard 与 quick 差异是否集中表达，校验是否在副作用前完成；
- **状态机**：Batch、cleanup review、pending relation 是否有阶段、幂等键和恢复点；
- **跨存储一致性**：关系库与 FalkorDB 是否有 durable operation/outbox/reconciler；
- **性能**：是否存在全表 Token 扫描、全模型物化、N+1、无分页和无预算循环；
- **安全**：Token 存储、日志脱敏、异常响应、跨租户和资源耗尽；
- **可维护性**：重复逻辑、隐式动态注册、浅合并配置、魔法字符串和不可测试耦合；
- **测试质量**：行为断言、真实边界、失败证明、覆盖率盲区和 mock 失真。

每个 Finding 使用 P0–P3，包含 Location、Trigger、Evidence、Impact、Root Cause、Why Existing Tests Missed It、Required Tests 和最小安全修复边界。相同根因只计一个主 Finding，其他位置作为影响面引用。

## 9. 验证门禁与结论规则

满足以下全部条件才允许给出通过建议：

- 标准模式和快速模式真实 E2E 全部通过且无测试残留；
- 权限、组织隔离、Token 生命周期和关系双端授权通过；
- 部分失败不会触发清理或标记完整成功；
- owner scope 能隔离同模型多任务/多组织数据；
- Celery/Beat 任务实际注册；
- 相关模块行覆盖率不低于 80%，核心 ingest/merge/relation/cleanup 不低于 90%；
- 无未处置 P0/P1；
- gitlink、overlay/制品版本和验证结果可以一一追溯。

任一 P0 未关闭，结论为 `Block`。P1 未关闭原则上同样 `Block`；只有明确不进入生产路径、存在可验证补偿且用户批准时，才可降为有条件通过。

## 10. 输出物

验证完成后交付：

- 双基线来源与 SHA-256 清单；
- 标准/快速模式场景矩阵和逐项结果；
- pytest、覆盖率、Celery 注册和真实 E2E 命令证据；
- 测试资源账本、清理结果与残留扫描；
- P0–P3 缺陷报告及代码行证据；
- 架构与代码质量总评；
- 发布建议与后续 TDD 修复清单。

## 11. 明确不在本轮自动执行的事项

- 不连接或写入生产环境；
- 不直接运行现有不安全 E2E 脚本；
- 不自动修复已发现缺陷；
- 不把运行态 ignored overlay 冒充锁定 Enterprise 提交；
- 不执行无资源上限的破坏性压力测试；
- 不清理任何不属于本次 `run_id` 的数据。
