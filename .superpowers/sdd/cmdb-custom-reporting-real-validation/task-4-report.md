# Task 4 实施报告：控制面授权与 Token 能力边界

## 交付范围

- 新增只创建唯一 `crval_...` 数据的 `TokenTask` / `create_token_task()` 工厂。
- 新增真实 API 的 create/update 组织授权与 list/create/update 功能权限正负向测试。
- 新增合法 Token、轮换、吊销能力测试。
- 记录 CRV-F01、CRV-F02；未修改任何生产逻辑。

## 真实 RED

环境：SQLite `:memory:`、`--nomigrations`、测试 MinIO、
`ENABLE_CELERY=true`、`SECRET_KEY=test-secret-key`、
`INSTALL_APPS=system_mgmt,node_mgmt,cmdb,cmdb_enterprise`。

未加 xfail 的聚焦选择器退出码为 1，摘要为：

```text
5 failed, 6 passed in 1.57s
```

关键原始断言：

```text
CRV-F01 create: assert (200, 1) == (403, 0)
CRV-F01 update: assert (200, [2]) == (403, [1])
CRV-F02 list:   assert 200 == 403
CRV-F02 create: assert (200, True) == (403, False)
CRV-F02 update: assert (200, crval_updated_task_...) == (403, crval_task_...)
```

失败均为稳定的业务断言失败，不是收集、数据库、Celery、SECRET_KEY 或 MinIO 环境失败。
Token 合法、轮换与吊销三类能力在同一次首轮中均通过。

## strict xfail 收敛

只在保存真实 RED 后给五个已确认缺陷测试增加稳定 reason：组织越权为 CRV-F01，功能
权限绕过为 CRV-F02。重跑退出码 0：

```text
6 passed, 5 xfailed in 1.76s
```

所有 xfail 都使用 `strict=True`，未来生产修复使测试 XPASS 时会让分支变红。

## Finding 与根因

- CRV-F01 / projectmem #0297 / P0：create 不校验目标 team；update 只校验旧 team，
  随后允许持久化无权的新 team。真实副作用是新建任务并签发 Token、或把 team 从
  `[1]` 改为 `[2]`。
- CRV-F02 / projectmem #0298 / P0：控制面 ViewSet 未接入 `@HasPermission`；
  `CmdbPermissionMixin` 不提供自动 action 权限，因此空/错 `request.user.permission`
  仍可 list/create/update。

完整字段见 `docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/02-findings.md`。

## 自审

- 工厂任务、模型、凭据与 Token 均使用唯一 `crval_` 前缀。
- 每个 Token 用例新建自己的任务和凭据；没有复用、轮换或吊销既有资源。
- 组织与功能权限测试走真实 URL 和真实 provider/service；权限主体使用工程当前
  `request.user.permission = {"cmdb": {...}}` 合同。
- Token 测试仅在图存储合并边界注入无副作用结果，Token 解析、凭据状态、批次与任务
  时间副作用均走真实代码。
- 未改生产逻辑；未写或覆盖通用 `.superpowers/sdd/task-4-report.md`。
- 文档不包含原始 Token 值或其他秘密。

## 最终验证

- 聚焦 pytest：`6 passed, 5 xfailed in 1.76s`，退出码 0。
- `black --check`：2 个新增测试文件无需修改。
- `isort --check-only`：通过。
- `flake8`：通过，输出 `0`。
- `git diff --check`：通过，无输出。
- 最终提交 SHA 在提交完成后随任务回报提供。
