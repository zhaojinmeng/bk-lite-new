# Task16：真实双模式 E2E 与故障注入验证记录

日期：2026-07-22

## 根因

Task16 暴露的 runner 级缺口是：`validation/custom_reporting/http_runner.py` 已能执行 quick/standard 的成功主链，并能通过真实 ORM/FalkorDB backend 做严格账本校验和安全清理，但执行计划没有显式暴露 24 项修复所需的负向与故障注入矩阵。因此 dry-run/执行报告只能证明成功链，不能证明“无效 token、权限/跨 team、重复 identity、非法字段/映射、空 snapshot、partial merge、ID 0、broker/DB/Graph 故障、lease/pending/budget/checkpoint”等场景被纳入本轮验证口径。

## 修复

- `ExecutionPlan` 增加 `validation_scenarios`。
- quick/standard 两种模式均输出同一套 30 项验证场景，按证据类型标记为：
  - `http_success`：真实 HTTP 成功主链覆盖。
  - `http_negative`：真实入口负向语义覆盖。
  - `state_backend`：真实 ORM/FalkorDB 快照或 cleanup preflight 覆盖。
  - `service_contract`：服务层故障注入/并发/恢复合同覆盖。
- 所有场景均标记 `destructive_cleanup=false`；runner cleanup 仍只保留原来的安全路径：preflight → HTTP DELETE task/association → backend residual=0 后删除 ledger。未增加强制图清理或绕过 ledger 的危险路径。

## TDD 证据

- RED：
  - `validation/custom_reporting/tests/test_http_runner.py::test_task16_plan_exposes_complete_e2e_and_fault_injection_matrix`
  - 失败原因：`AttributeError: 'ExecutionPlan' object has no attribute 'validation_scenarios'`
- GREEN：
  - 同一测试通过：`1 passed`
- Runner 回归：
  - `validation/custom_reporting/tests/test_http_runner.py`
  - 结果：`123 passed`

## 本轮可执行验证

- `validation/custom_reporting/tests`
  - 环境：`SECRET_KEY=test-secret ENABLE_CELERY=true DB_ENGINE=sqlite DB_NAME=:memory: INSTALL_APPS=system_mgmt,node_mgmt,cmdb,cmdb_enterprise`
  - 结果：`333 passed`
  - 备注：曾先后暴露三类环境问题并已定位：缺 `ENABLE_CELERY=true`、未指定 SQLite DB、未设置测试 `SECRET_KEY`。
- `apps/cmdb_enterprise/tests`
  - 环境同上。
  - 结果：`336 passed`
- Runner CLI dry-run：
  - quick：输出 `dry_run=true`、`requests_sent=0`、完整 `validation_scenarios`。
  - standard：输出 `dry_run=true`、`requests_sent=0`、完整 `validation_scenarios`。

## 真实 HTTP 写入条件

本轮环境探测：

- `CRV_BASE_URL=UNSET`
- `CRV_ALLOWED_HOSTS=UNSET`
- `CRV_ORG_ID=UNSET`
- `CRV_SESSION_COOKIE=UNSET`
- `CRV_MANAGEMENT_API_SECRET=UNSET`
- `CRV_EXECUTE_CONFIRMED=UNSET`
- `CRV_ALLOW_WRITE=UNSET`
- `127.0.0.1:8011=CLOSED`
- `127.0.0.1:6379=OPEN`

结论：当前 worktree 环境没有运行中的后端服务、管理会话 cookie、管理 API secret 和执行确认门。按 runner 的三重执行门与安全边界，不能伪造真实 HTTP 写入结果；本轮只执行 dry-run、FakeTransport 合同、Django APIClient 合同和服务层故障注入合同。

## Task16 场景矩阵

| 场景 | 证据类型 |
|---|---|
| create_task | http_success |
| issue_token | http_success |
| initial_ingest | http_success |
| incremental_ingest | http_success |
| relation_immediate | http_success |
| relation_pending_backfill | http_success |
| snapshot_cleanup_review | state_backend |
| expire_cleanup_review | state_backend |
| credential_rotate_reject_old_token | http_success |
| credential_revoke_reject_revoked_token | http_success |
| invalid_token | http_negative |
| permission_denied | http_negative |
| cross_team_scope | http_negative |
| duplicate_identity | http_negative |
| illegal_field | http_negative |
| illegal_mapping | http_negative |
| empty_snapshot_noop | service_contract |
| empty_snapshot_requires_review | service_contract |
| partial_merge_zero_relation_snapshot | service_contract |
| zero_graph_id | state_backend |
| broker_unavailable | service_contract |
| graph_success_db_finalize_fail | service_contract |
| db_desired_graph_fail | service_contract |
| concurrent_approve_single_owner | service_contract |
| lease_takeover | service_contract |
| stale_owner_finalize | service_contract |
| poison_pending_dead_letter | service_contract |
| pending_recovery | service_contract |
| over_budget | service_contract |
| checkpoint_resume | service_contract |
