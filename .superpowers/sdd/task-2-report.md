# Task 2 实施报告：建立安全运行账本与清理边界

## 状态

已完成实现、TDD、自审与提交前门禁验证。

## 交付

- 新增 `ResourceRef` 与 `ValidationLedger`，默认生成带 UTC 时间和随机 nonce 的
  唯一 `run_id`，并支持测试用确定性时间与 nonce。
- `record()` 只接受固定九类资源；`task`、`association`、`model` 名称必须包含
  当前 `run_id`，未知类型与非当前运行名称均 fail-closed。
- 重复记录幂等；`cleanup_plan()` 只返回已记录的 `ResourceRef`，不执行或生成
  删除动作。
- 清理计划按 `edge, instance, review, pending, batch, credential, task,
  association, model` 的依赖安全优先级返回，同类型资源按记录逆序（LIFO）清理。
- `to_json()` / `from_json()` 往返后保留 `run_id`、资源集合和清理计划。

## TDD 证据

- 首轮 RED：测试先落盘，聚焦 pytest 在收集期得到预期
  `ModuleNotFoundError: No module named 'validation.custom_reporting.ledger'`。
- 首轮 GREEN：最小实现后 17 项测试通过。
- 自审 RED：新增同类型逆序清理测试后得到 `1 failed`，明确旧实现返回
  `instance 101, 102`，不满足 LIFO。
- 最终 GREEN：改为资源种类优先级加同类型逆记录序后，18 项全部通过。

## 最终验证

- 聚焦测试与覆盖率：`18 passed in 0.14s`；`ledger.py` 47 条语句零遗漏，
  行覆盖率 `100%`，高于 90% 目标。
- `black --check`：2 个触及文件无需修改。
- `isort --check-only`：通过。
- `flake8`：0 项问题。

测试使用最小环境 `INSTALL_APPS=system_mgmt,node_mgmt,cmdb` 和测试专用 MinIO
变量，并启用 server 锁定的 `dev` extra，避免仓库 `.env` 扩展应用污染。

## 自审

- 变更仅新增简报指定的账本、测试和本报告，不修改生产业务逻辑。
- 账本无网络、数据库、文件删除或生产写入能力；清理接口只返回不可执行的计划。
- 名称型资源不能混入既有生产名称；未知资源类型拒绝；JSON 恢复复用同一
  `record()` 安全校验。
- 未复用现有任务、模型或凭据，未轮换 token。
- 无剩余 Critical / Important 问题。

## 环境记录

- 首次 RED 被 uv 用户缓存权限阻断；受控读取缓存后取得真实 RED，projectmem
  #0274 已关闭。
- uv 环境首次未安装可选 `dev` extra，导致缺少 pytest-django 的 `settings`
  fixture；按 `server/pyproject.toml` 恢复锁定 dev 依赖后解除，projectmem #0275
  已关闭。
- 格式化与同类型 LIFO 自审问题分别记录并关闭为 projectmem #0276、#0278。
