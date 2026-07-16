# CMDB 自定义上报真实测试矩阵

## 结论摘要

| 测试面 | 归属基线 | 原始命令真实结果 | 补充环境复验 | 结论 |
| --- | --- | --- | --- | --- |
| Community 默认扩展与模型委托 | Community，无 overlay | 6 passed / 0 failed，0.15s | 不需要 | 通过 |
| Enterprise 自定义上报完整选择器 | 固定运行态 overlay | 78 passed / 3 failed，3.15s；失败态不完整覆盖率 85% | 81 passed / 0 failed，1.80s；完整覆盖率 83% | 原命令被测试环境合同阻断；补齐测试环境后通过 |

测试执行于 2026-07-16 08:59—09:02（Asia/Shanghai），工作树 HEAD 为
`2162d7658`。所有数据库用例均使用 SQLite 内存库和 `--nomigrations`；没有连接
生产、复用现有业务数据或执行真实 HTTP 写入。

## overlay 完整性前置门禁

测试前执行了三层只读核验：

1. `overlay-sha256.txt` 除元数据头外为 78 条记录；
2. `server/apps/cmdb_enterprise` 排除 `__pycache__` 和 `*.pyc` 后实际为
   78 个文件，路径集合与清单完全一致；
3. 78 个文件逐项 SHA-256 全部成功，清单正文重新聚合为
   `f7ece8164af8fdf3b9ef96e26438bf44991b7e426f01de1b6830faa456c02e42`。

因此本次测试的运行态制品没有相对 Task 1 固定清单漂移。

## 可复现命令

### Community 原始基线

```bash
MINIO_ENDPOINT=localhost:9000 \
MINIO_ACCESS_KEY=test \
MINIO_SECRET_KEY=test \
MINIO_USE_HTTPS=false \
DB_ENGINE=sqlite \
DB_NAME=:memory: \
INSTALL_APPS=system_mgmt,node_mgmt,cmdb \
/Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/server/.venv/bin/pytest \
  -q -o addopts='' --nomigrations \
  apps/cmdb/tests/test_custom_reporting_extension.py \
  apps/cmdb/tests/test_model_custom_reporting_delegation.py
```

真实输出摘要：`6 passed in 0.15s`，退出码 0。

### overlay 原始基线

```bash
MINIO_ENDPOINT=localhost:9000 \
MINIO_ACCESS_KEY=test \
MINIO_SECRET_KEY=test \
MINIO_USE_HTTPS=false \
DB_ENGINE=sqlite \
DB_NAME=:memory: \
INSTALL_APPS=system_mgmt,node_mgmt,cmdb,cmdb_enterprise \
/Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/server/.venv/bin/pytest \
  -q -o addopts='' --nomigrations \
  apps/cmdb_enterprise/tests/test_custom_reporting_*.py \
  apps/cmdb_enterprise/tests/bdd/test_custom_reporting_bdd.py \
  --cov=apps.cmdb_enterprise.custom_reporting \
  --cov=apps.cmdb.custom_reporting \
  --cov=apps.cmdb.views.custom_reporting \
  --cov=apps.cmdb.serializers.custom_reporting \
  --cov-report=term-missing
```

真实输出摘要：`3 failed, 78 passed in 3.15s`，退出码 1。失败项为：

- `test_custom_reporting_views.py::test_list_tasks_delegates`
- `test_custom_reporting_views.py::test_create_task_delegates`
- `test_custom_reporting_views.py::test_ingest_delegates_bearer_token`

失败首先稳定复现为 `django_celery_beat` 模型未加入
`INSTALLED_APPS`。仅补 `ENABLE_CELERY=true` 后，三项仍因空
`SECRET_KEY` 失败，证明两项都是测试环境合同的一部分，而非单个产品断言失败。

### overlay 补充环境完整复跑

以下命令只比原始命令增加 `ENABLE_CELERY=true` 和固定非敏感测试值
`SECRET_KEY=test-secret-key`，测试选择器、数据库、应用范围和覆盖率参数不变：

```bash
MINIO_ENDPOINT=localhost:9000 \
MINIO_ACCESS_KEY=test \
MINIO_SECRET_KEY=test \
MINIO_USE_HTTPS=false \
SECRET_KEY=test-secret-key \
DB_ENGINE=sqlite \
DB_NAME=:memory: \
ENABLE_CELERY=true \
INSTALL_APPS=system_mgmt,node_mgmt,cmdb,cmdb_enterprise \
/Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/server/.venv/bin/pytest \
  -q -o addopts='' --nomigrations \
  apps/cmdb_enterprise/tests/test_custom_reporting_*.py \
  apps/cmdb_enterprise/tests/bdd/test_custom_reporting_bdd.py \
  --cov=apps.cmdb_enterprise.custom_reporting \
  --cov=apps.cmdb.custom_reporting \
  --cov=apps.cmdb.views.custom_reporting \
  --cov=apps.cmdb.serializers.custom_reporting \
  --cov-report=term-missing
```

真实输出摘要：`81 passed in 1.80s`，退出码 0。

## 完整覆盖率

下表来自补充环境后的全绿完整复跑；这是本次可比较的覆盖率口径。

| 模块 | Stmts | Miss | Cover | Missing |
| --- | ---: | ---: | ---: | --- |
| `apps/cmdb/custom_reporting/__init__.py` | 0 | 0 | 100% | — |
| `apps/cmdb/custom_reporting/extensions.py` | 51 | 22 | 57% | 16, 19, 22, 25, 28, 31, 34, 41, 44, 47, 50, 53, 56, 59, 62, 65, 68, 71, 74, 77, 80, 83 |
| `apps/cmdb/serializers/custom_reporting.py` | 13 | 0 | 100% | — |
| `apps/cmdb/views/custom_reporting.py` | 64 | 22 | 66% | 17, 27, 30-32, 35-36, 40, 46, 50, 54, 58-61, 65-68, 72, 76, 88 |
| `apps/cmdb_enterprise/custom_reporting/__init__.py` | 0 | 0 | 100% | — |
| `apps/cmdb_enterprise/custom_reporting/models.py` | 177 | 3 | 98% | 82, 160, 309 |
| `apps/cmdb_enterprise/custom_reporting/provider.py` | 76 | 23 | 70% | 18, 56, 59, 62, 70, 73-77, 81, 93, 97, 101, 109, 116-117, 121, 125, 132, 135, 139-141 |
| `apps/cmdb_enterprise/custom_reporting/services/__init__.py` | 0 | 0 | 100% | — |
| `apps/cmdb_enterprise/custom_reporting/services/activity_service.py` | 43 | 6 | 86% | 82-83, 103-104, 136-137 |
| `apps/cmdb_enterprise/custom_reporting/services/cleanup_service.py` | 79 | 15 | 81% | 35-62, 123-124, 221 |
| `apps/cmdb_enterprise/custom_reporting/services/credential_service.py` | 22 | 2 | 91% | 18-19 |
| `apps/cmdb_enterprise/custom_reporting/services/document_service.py` | 15 | 0 | 100% | — |
| `apps/cmdb_enterprise/custom_reporting/services/field_service.py` | 37 | 0 | 100% | — |
| `apps/cmdb_enterprise/custom_reporting/services/ingest_service.py` | 50 | 11 | 78% | 28, 86, 91-101, 123-127 |
| `apps/cmdb_enterprise/custom_reporting/services/merge_service.py` | 70 | 19 | 73% | 82-87, 109-112, 123-126, 150-154 |
| `apps/cmdb_enterprise/custom_reporting/services/model_service.py` | 50 | 3 | 94% | 28, 39, 81 |
| `apps/cmdb_enterprise/custom_reporting/services/relation_service.py` | 55 | 9 | 84% | 20-21, 33, 46-49, 127-128 |
| `apps/cmdb_enterprise/custom_reporting/services/task_service.py` | 129 | 15 | 88% | 109, 177-178, 183-184, 258-259, 276, 290-291, 296, 300, 307-308, 312 |
| `apps/cmdb_enterprise/custom_reporting/tasks.py` | 5 | 5 | 0% | 3-11 |
| **TOTAL** | **936** | **155** | **83%** | — |

原始失败态报告为 859 statements、133 missed、85%。由于三个 View 用例在
URL/请求环境初始化阶段失败，`apps/cmdb/views/custom_reporting.py` 与
`apps/cmdb/serializers/custom_reporting.py` 没有形成完整统计；该 85% 只用于保留
首跑证据，不是完整基线，也不能与上表直接比较。

## 未覆盖核心路径

以下是覆盖率证据揭示的后续重点，不等同于已确认产品缺陷：

- Celery 过期清理入口 `custom_reporting/tasks.py` 为 0%，尚未覆盖任务注册及
  `expire_cleanup()` 调用链。
- HTTP View 仅覆盖 list、create 和 Bearer ingest；stats、retrieve、update、
  destroy、字段/活动/文档、凭据轮换与吊销、审核批准/拒绝，以及非 Bearer
  Authorization 分支仍缺端到端请求覆盖。
- 上报链路未覆盖 pending relation 回填、snapshot 清理和批次异常落库重抛；
  这些分别落在 `ingest_service.py` 的 86、91-101、123-127。
- 合并链路未覆盖旧实例 identity 强转、新增/更新变更记录和批次内关系索引构造，
  对应 `merge_service.py` 的 82-87、109-112、123-126、150-154。
- Community 门面和 Enterprise provider 的若干委托/默认拒绝分支未直接覆盖；完整
  覆盖率分别为 57% 和 70%。

## 双基线归属

- Community 结果只归属于社区默认扩展边界，不包含商业实现。
- Enterprise 测试结果只归属于 `overlay-sha256.txt` 固定的当前运行态 overlay。
- 锁定 `enterprise@1e9c3d2` 的自定义上报目录只有 `__init__.py`、`models.py`，
  且 URL 列表为空，不具备本次运行态行为；不得把 81 项测试或 83% 覆盖率归因于
  该 gitlink 交付。

## 异常分类与写入审计

- 环境问题：原始 overlay 命令缺少 `ENABLE_CELERY` 和测试 `SECRET_KEY`，记录为
  projectmem #0288，并由最小环境复验确认。
- 测试缺陷：Task 3 未确认。
- 产品缺陷：Task 3 未确认；Task 3 未修改生产逻辑。
- 所有写入仅为 SQLite 内存测试库、coverage 运行制品和本审查文档；没有生产写入、
  外部 HTTP 写入、真实凭据轮换或 xfail 掩盖。

## Task 4：控制面授权与 Token 能力边界

执行环境沿用已确认合同：SQLite 内存库、`--nomigrations`、测试 MinIO，显式
`ENABLE_CELERY=true`、`SECRET_KEY=test-secret-key`，应用集为
`system_mgmt,node_mgmt,cmdb,cmdb_enterprise`。工厂只创建带 `crval_` 前缀的唯一
任务、模型、凭据和原始 Token，没有读取、轮换或吊销任何既有资源。

选择器：

```bash
/Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/server/.venv/bin/pytest \
  -q -o addopts='' --nomigrations \
  validation/custom_reporting/tests/test_runtime_contracts.py
```

| 边界 | 测试 | 首轮 RED / 正向结果 | 最终状态 | Finding |
| --- | --- | --- | --- | --- |
| create 组织授权 | `test_create_rejects_team_outside_requester_scope` | 期望 `(403, 0)`，实际 `(200, 1)` | strict xfail | CRV-F01 |
| update 组织授权 | `test_update_rejects_moving_task_outside_requester_scope` | 期望 `(403, [1])`，实际 `(200, [2])` | strict xfail | CRV-F01 |
| list 功能权限 | `test_list_allows_*` / `test_list_rejects_*` | 正向 200；空权限负向实际 200 | 1 passed / 1 strict xfail | CRV-F02 |
| create 功能权限 | `test_create_allows_*` / `test_create_rejects_*` | 正向 200；仅 View 仍 200 且落库 | 1 passed / 1 strict xfail | CRV-F02 |
| update 功能权限 | `test_update_allows_*` / `test_update_rejects_*` | 正向 200；仅 View 仍 200 且改名 | 1 passed / 1 strict xfail | CRV-F02 |
| 合法 Token | `test_factory_token_is_accepted_by_ingest_capability` | 上报能力接受并更新任务时间 | passed | — |
| Token 轮换 | `test_rotating_factory_token_invalidates_old_and_accepts_new` | 旧 Token 拒绝，新 Token 接受 | passed | — |
| Token 吊销 | `test_revoking_factory_token_blocks_ingest_capability` | 吊销后拒绝 | passed | — |

未标记 xfail 的首轮结果为 `5 failed, 6 passed in 1.57s`。完整 RED 断言、HTTP
状态和副作用证据保存在 namespaced
`.superpowers/sdd/cmdb-custom-reporting-real-validation/task-4-report.md` 的“真实 RED”
章节。确认缺陷后只给对应测试增加 `xfail(strict=True, reason="CRV-Fxx")`，最终聚焦
结果经复审加固后为 `8 passed, 5 xfailed in 1.62s`。五个缺陷 marker 均限制
`raises=KnownProductDefect`，且只在观察值精确等于已记录坏行为时抛该异常；其他响应、
断言和 setup/DB/环境异常保持普通失败，不会被缺陷 marker 吞掉。
