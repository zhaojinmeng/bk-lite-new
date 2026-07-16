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
