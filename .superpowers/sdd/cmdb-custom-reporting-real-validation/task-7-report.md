# Task 7：Celery 注册与资源预算验证报告

## 执行边界

- 仅执行 Beat 配置到 Worker 注册表的可执行契约，以及源码级有界资源预算审计。
- 未启动 Beat/Worker、未发送大请求、未构造高基数数据、未执行压力或破坏性测试。
- 未修改生产逻辑；确认缺陷只以精确 `KnownProductDefect` strict xfail 固化。

## Celery 注册契约

执行命令：

```bash
cd server
ENABLE_CELERY=true MINIO_ENDPOINT=localhost:9000 MINIO_ACCESS_KEY=test \
MINIO_SECRET_KEY=test MINIO_USE_HTTPS=false DB_ENGINE=sqlite DB_NAME=:memory: \
INSTALL_APPS=system_mgmt,node_mgmt,cmdb,cmdb_enterprise \
/Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/server/.venv/bin/pytest \
  -q -o addopts='' --nomigrations \
  validation/custom_reporting/tests/test_task_registration.py
```

真实 RED：`1 failed`，`missing` 精确为：

```text
['apps.cmdb_enterprise.custom_reporting.tasks.custom_reporting_expire_cleanup']
```

固化后以 `-rxX` 重跑：`1 xfailed in 0.46s`，退出码 0。测试仅在缺失集合精确等于上述完整任务名时抛 `KnownProductDefect`；出现其他缺失、额外缺失或意外状态时仍普通失败。

最终 fresh 门禁：

- `test_task_registration.py + test_runtime_contracts.py + test_failure_boundaries.py`：`12 passed, 18 xfailed in 1.83s`（`SECRET_KEY=test`，退出码 0）。
- 新增测试定向 `black --check`、`isort --check-only`、`flake8`：全部退出码 0。
- `git diff --check`：退出码 0。

## 有界静态资源预算审计

审计命令：

```bash
rg -n 'request\.data|query_entity|\.objects\.all\(\)|filter\(is_enabled=True\)|for .* in .*credentials|relations|instances' \
  server/apps/cmdb/views/custom_reporting.py \
  server/apps/cmdb_enterprise/custom_reporting
```

| 路径 | 静态证据 | 已有上限 | 结论 |
| --- | --- | --- | --- |
| 控制面任务列表 | `task_service.py:151-190` 使用 Django `Paginator` | `page_size` 被裁剪到 1..200 | 已有单页行数上限 |
| 任务详情 recent batches | `activity_service.py:142-148` | 输出仅取 `_RECENT_BATCHES_LIMIT` | 输出有上限，但取切片前已全量物化全部 batch/review |
| batch activity | `activity_service.py:106-121` | 未发现 | 全量物化任务历史 batch 与 review |
| 上报请求 | `views/custom_reporting.py:89`、`ingest_service.py:60-86` | 未发现本模块 body/list cardinality 上限 | `instances`、`relations` 直接进入同步处理链 |
| Token 匹配 | `ingest_service.py:30-32` | 未发现 | 每次请求遍历全部启用凭据并逐一执行 token 匹配 |
| 实例 merge | `merge_service.py:63-81` | 未发现 | 遍历全部上报实例，并按 model 拉取全量现有图实例到内存 |
| 关系处理/回填 | `relation_service.py:86-100,120-135` | 未发现 | 逐 relation 查询；只要有 relation 或 pending 即全量扫描该任务 pending |
| expire cleanup | `cleanup_service.py:208-240` | 未发现 | 遍历全部启用任务，每个 expire 模型全量 `query_entity` 后在 Python 过滤并聚合删除 ID |

“未发现”仅表示本次指定源码范围内没有显式预算；不推断反向代理、ASGI 容器或数据库的部署级限制。

## Finding CRV-F11 [P1]：Beat 配置的过期清理任务未进入 Worker 注册表

- Location：`server/apps/cmdb_enterprise/config.py:10-12`；`server/apps/cmdb_enterprise/custom_reporting/tasks.py:8-11`；`server/apps/cmdb_enterprise/tasks/__init__.py:2`
- Trigger：启用 Celery 与 Enterprise app，加载 Celery 默认模块后，对 `CELERY_BEAT_SCHEDULE` 的每个 task path 检查 `app.tasks`。
- Evidence：`test_every_enterprise_beat_task_is_registered` 真实 RED 唯一列出完整 expire task path；`apps.cmdb_enterprise.tasks` 只显式导入 attachment cleanup。projectmem #0383。
- Impact：Beat 可按日生成一个 Worker 不认识的任务名，过期自定义上报实例清理不会按计划执行，陈旧资产持续保留并造成数据准确性与存储增长风险。
- Root Cause：Celery autodiscovery 只发现 app 级 `tasks` 模块；过期任务定义在嵌套包 `custom_reporting/tasks.py`，却没有从 app 级 tasks 包导入注册。
- Why Existing Tests Missed It：既有测试直接调用 `expire_cleanup()` 或任务函数，没有把 Enterprise Beat task path 与 Worker 的实际注册表做集合一致性检查。
- Required Tests：保留 `test_every_enterprise_beat_task_is_registered`；修复后必须从 strict xfail 变为普通通过，并确保任意新增/额外缺失 task path 均普通失败。

## Finding CRV-F12 [P1]：上报与清理链路缺少统一请求及扫描资源预算

- Location：`server/apps/cmdb_enterprise/custom_reporting/services/ingest_service.py:30-32,60-86`；`merge_service.py:63-81`；`relation_service.py:86-100,120-135`；`cleanup_service.py:208-240`；`activity_service.py:92-148`
- Trigger：合法 Token 提交高基数 `instances`/`relations`，任务积累大量 pending/batch/review，或多个 expire 任务对应大模型；本轮只由静态控制流确认，不执行高负载输入。
- Evidence：除控制面任务列表单页 200 与 recent_batches 输出切片外，入口未限制列表基数；凭据、pending、batch/review、启用任务与图实体均存在无分页/无 chunk/无最大处理数的全量迭代或物化。projectmem #0384。
- Impact：单请求或单次日清理可使 CPU、内存、数据库/图查询与副作用次数随租户历史规模无界增长，阻塞同步请求或 Celery worker，并放大超时、重复执行及部分完成风险。
- Root Cause：模块没有统一 `ResourceBudget` 契约；API、同步 service、图扫描和周期任务各自直接消费完整集合，缺少入口拒绝、游标分页、chunk、每轮截止时间与可续跑状态。
- Why Existing Tests Missed It：现有用例使用少量内存夹具并 mock 图查询，主要断言业务结果；未断言最大列表基数、查询 limit/cursor、chunk 大小、每轮处理上限或续跑游标。
- Required Tests：增加入口 `instances`/`relations` 上限与超限零副作用测试；凭据查找索引化合同；merge/expire 图查询游标与最大页大小测试；pending、batch/review 分页测试；周期清理每轮 deadline/最大处理量及幂等续跑测试。

## 结论

- CRV-F11：确认缺陷，P1，已用精确 strict xfail 固化；本任务不修生产代码。
- CRV-F12：确认静态资源预算缺口，P1；本任务不执行压力攻击、不修生产代码。
- 两项完整 Finding 已同步到正式审查报告
  `docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/02-findings.md`。
