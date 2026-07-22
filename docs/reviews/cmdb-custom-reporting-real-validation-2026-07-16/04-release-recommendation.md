# CMDB 自定义上报发布建议

## 结论：Conditional Pass（需补真实 HTTP 写入复跑）

截至 2026-07-22，原确认的 24 项产品缺陷（P0 11 项、P1 13 项）均已完成代码级修复与合同级回归验证；
本轮新鲜门禁为 `validation/custom_reporting/tests apps/cmdb_enterprise/tests` 共 `669 passed`，跨模块回归
`100 passed`，迁移漂移 `No changes detected`，未发现仍由测试复现的 P0/P1。

仍保留一个发布前条件：本轮 worktree 环境缺少真实 HTTP 执行所需的 `CRV_*` 凭据/确认门，且
`127.0.0.1:8011` 未监听，因此未重新执行 quick/standard 的 `--execute` 写入段。发布前必须在具备
隔离后端、隔离 FalkorDB、管理 session/API secret 的环境中复跑：

1. quick `--execute` → `--verify-ledger` → `--cleanup-ledger`
2. standard `--execute` → `--verify-ledger` → `--cleanup-ledger`
3. 确认两种模式 residual=0，旧/撤销 token 返回 401/403 而非 500。

如果上述真实写入复跑通过，发布建议可调整为 Pass；如果复跑失败，应以失败场景重新登记缺陷并回到系统化调试/TDD。

## 本轮验证证据

| 验证面 | 结果 | 证据 |
| --- | --- | --- |
| Enterprise overlay | 代码级门禁通过；提交由用户处理 | 当前运行 overlay 已在 worktree 中修复并验证；商业版文件清单见 `.superpowers/sdd/task-17-report.md` |
| 固定运行态 overlay | 被测基线已固化 | 当前运行 overlay 已逐文件记录 SHA-256；所有合同和审计均以该制品为准 |
| quick 正向 E2E | 通过 | 1 task、1 quick model、1 association、5 instances、4 batches、1 field registration、2 精确关系、pending=0 |
| standard 正向 E2E | 通过 | 1 standard task 复用模型、5 instances、4 batches、0 field registration、2 精确关系、pending=0 |
| Token rotation/revoke 负向 | 代码级合同通过；真实写入待复跑 | APIClient/服务合同已覆盖缺失、随机、旧、撤销 Token；本轮缺真实 HTTP 写入凭据 |
| 图事实 | 通过 | 验证真实 FalkorDB 零基 ID、两条边 association 与方向、source/target 精确配对 |
| 精准清理 | 通过 | 自动 Runner 在旧 Token 500 后、revoke 前中止；经 ledger ownership 预检后用同一 SafeHttpClient 受控撤销，再完成 verify/cleanup，业务 residual=0 |
| 产品缺陷合同 | 通过 | `validation/custom_reporting/tests apps/cmdb_enterprise/tests`：`669 passed`，无 strict xfail |
| Enterprise 既有基线 | 通过 | `apps/cmdb_enterprise/tests`：`336 passed` |
| Runner 安全合同 | 通过 | `test_http_runner.py`：`123 passed`；quick/standard dry-run 均输出完整 Task16 场景矩阵 |
| 静态门禁 | 部分通过/部分环境缺工具 | `py_compile`、`git diff --check`、raw SQL 扫描通过；当前 uv 环境缺 `black/isort/flake8` 可执行文件，已在 projectmem 记录 |

脱敏证据：资源 ID 清单 `e2e-ledger-quick.json`、`e2e-ledger-standard.json`；包含自动中止、
受控恢复、verify snapshot、cleanup 与 residual 的结构化终端结果摘要
`e2e-result-quick.json`、`e2e-result-standard.json`；以及 `overlay-sha256.txt`。上述文件不包含
session、管理密钥、Bearer token 或生产数据。结果摘要不是 Runner 自动成功证明，而是明确
记录“自动中止 + 同一安全客户端受控恢复”的审计证据。

## Finding 统计（修复后）

| 严重度 | 数量 | ID |
| --- | ---: | --- |
| P0 | 0 未关闭复现 | 原 F01—F09、F14、F17 已由合同测试覆盖并通过 |
| P1 | 0 未关闭复现 | 原 F10—F13、F15—F16、F18—F24 已由合同测试覆盖并通过 |
| P2/P3 | 0 | — |

统计只包含 `02-findings.md` 中已有动态或确定性静态证据的 24 个主 Finding；
`03-architecture-quality.md` 的 AQ 项用于归因和改造排序，不重复计数。

## 放行所需最低条件

1. 在具备真实 CRV 凭据/确认门和运行中后端的隔离环境复跑 quick/standard HTTP 写入、verify、cleanup。
2. 确认旧 token、撤销 token、无效 token 均为稳定 401/403，且零 Batch、零图写、零敏感信息泄露。
3. 确认 cleanup residual=0，且 ledger 不因失败被提前删除。
4. 在 CI 或补齐依赖的本地环境运行 `black/isort/flake8`，补足当前 uv 环境缺工具的静态门禁。

## 后续发布动作

1. 用户按商业版仓库流程提交 `cmdb_enterprise` overlay 文件。
2. 在隔离真实后端/FalkorDB 环境执行 quick/standard 写入复跑和 cleanup residual 验证。
3. 在 CI 或补齐依赖的本地环境执行 `black/isort/flake8`。
4. 若真实写入复跑和格式门禁均通过，将 Recommendation 从 Conditional Pass 调整为 Pass。
