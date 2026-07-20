# CMDB 自定义上报发布建议

## 结论：Block

不建议把当前自定义上报作为生产可用能力发布或扩大接入。quick 与 standard 的真实正向
数据链路均已在隔离 SQLite + FalkorDB + HTTP 环境走通，精准清理也证明可收敛到零残留；
但这只能证明 happy path 可运行，不能抵消 11 个 P0、13 个 P1 已确认缺陷。

发布阻断的首要原因是组织授权/所有权隔离（F01、F02、F08、F09、F14），其次是非法
schema/身份/保留字段（F03—F06、F15）、部分提交和清理一致性（F07、F10、F13）、
运行任务缺失与资源无界（F11、F12），真实双模式都复现的认证错误协议（F16），以及
空快照删除、跨存储分叉、重复 identity、poison pending 和零基图 ID（F17—F24）。

## 本轮验证证据

| 验证面 | 结果 | 证据 |
| --- | --- | --- |
| 锁定 Enterprise gitlink | 功能未完整交付，Block | gitlink 基线已固定，但不包含当前运行 overlay 的完整自定义上报实现，不能作为可发布制品 |
| 固定运行态 overlay | 被测基线已固化 | 当前运行 overlay 已复制到隔离 worktree 并逐文件记录 SHA-256；所有合同、真实 E2E 和审计均以该制品为准 |
| quick 正向 E2E | 通过 | 1 task、1 quick model、1 association、5 instances、4 batches、1 field registration、2 精确关系、pending=0 |
| standard 正向 E2E | 通过 | 1 standard task 复用模型、5 instances、4 batches、0 field registration、2 精确关系、pending=0 |
| Token rotation/revoke 负向 | 失败/阻断 | 两种模式的旧 Token 请求均返回 HTTP 500，而非稳定认证拒绝；CRV-F16 strict xfail |
| 图事实 | 通过 | 验证真实 FalkorDB 零基 ID、两条边 association 与方向、source/target 精确配对 |
| 精准清理 | 通过 | 自动 Runner 在旧 Token 500 后、revoke 前中止；经 ledger ownership 预检后用同一 SafeHttpClient 受控撤销，再完成 verify/cleanup，业务 residual=0 |
| 产品缺陷合同 | 通过（缺陷仍开放） | `12 passed, 22 strict xfailed`；F16 参数化四种 token；strict XPASS、普通异常和第三种坏行为都会使门禁失败 |
| Enterprise 既有基线 | 通过 | `81 passed`；目标模块覆盖率 83% |
| Runner 安全合同 | 通过 | Runner/ledger/artifact `198 passed`；`http_runner.py` 覆盖率 81%，三模块合计 84% |
| 静态门禁 | 通过 | `black --check`、`isort --check-only`、`flake8` 对整个 `validation/custom_reporting` 全绿 |

脱敏证据：资源 ID 清单 `e2e-ledger-quick.json`、`e2e-ledger-standard.json`；包含自动中止、
受控恢复、verify snapshot、cleanup 与 residual 的结构化终端结果摘要
`e2e-result-quick.json`、`e2e-result-standard.json`；以及 `overlay-sha256.txt`。上述文件不包含
session、管理密钥、Bearer token 或生产数据。结果摘要不是 Runner 自动成功证明，而是明确
记录“自动中止 + 同一安全客户端受控恢复”的审计证据。

## Finding 统计

| 严重度 | 数量 | ID |
| --- | ---: | --- |
| P0 | 11 | F01—F09、F14、F17 |
| P1 | 13 | F10—F13、F15—F16、F18—F24 |
| P2/P3 | 0 | — |

统计只包含 `02-findings.md` 中已有动态或确定性静态证据的 24 个主 Finding；
`03-architecture-quality.md` 的 AQ 项用于归因和改造排序，不重复计数。

## 放行所需最低条件

1. 关闭全部 P0：管理面功能权限、组织绑定、old_data/snapshot owner scope、关系两端
   owner scope、API Secret caller context 和空 snapshot 删除均有普通通过的负向/正向合同测试。
2. 关闭 F03—F07、F10、F13、F15、F18—F24：入口 schema 完整拒绝非法输入；图/DB/消息部分失败
   具备可证明、幂等、可恢复状态，不再依赖客户端盲重试。
3. 关闭 F11/F12：Beat/Worker 注册一致；入口、查询、pending、历史活动和清理作业都有
   固定预算、分页/chunk、deadline 与续跑游标。
4. 关闭 F16：缺失、随机、旧、撤销 Token 均稳定返回统一 401/403，零 Batch、零图写、
   零敏感信息泄露；有效新 Token 仍成功。
5. 在与生产拓扑等价但隔离的数据源上重跑两种模式，包括 Celery broker 可用/不可用、
   并发相同 identity、空快照、部分图故障、cleanup 重试和 task 删除生命周期。
6. 所有 strict xfail 在产品修复后转为普通测试并通过；触及代码覆盖率不少于 75%，执行
   server 对应模块完整门禁且无新增未解释 warning。

## 建议修复批次

- 批次 A（安全与数据保护）：F01、F02、F08、F09、F14、F17、F18。
- 批次 B（输入与数据正确性）：F03—F07、F15、F16、F22、F24。
- 批次 C（一致性）：F10、F13、F19—F21、F23，引入 operation/outbox 与状态化 cleanup。
- 批次 D（运行与规模）：F11、F12，补注册合同和 ResourceBudget。

每个批次应独立走系统化调试和 TDD，不建议在一个大提交中同时重写授权、图存储和异步
架构。修复后再做独立代码审查及双模式真实 E2E，届时才能把 Recommendation 从 Block
调整为 Conditional Pass 或 Pass。
