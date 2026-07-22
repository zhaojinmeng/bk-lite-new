# Task 3 最终报告：严格 Schema 编译边界

## 最终状态

Task 3 的 F03、F04、F05、F22 及三轮审查补强均已完成。实现保持在纯 Schema 边界内，没有扩展处理其他 Finding，没有执行 `git add`、commit 或 push。

## 最终行为

- `mode` 必须显式为 `standard` 或 `quick`；create、update、ingest 在模型、任务、凭据、Batch 和图写副作用前 fail-closed。
- `identity_keys` 必须是非空、无重复、无首尾空白、非系统保留字段的字符串列表。
- standard 拒绝未知字段和系统字段；quick 在字段登记与 merge 前剥离系统字段。
- ingest 基于一次 attrs 快照构建唯一 `NormalizedInstancePlan`；字段登记复用 `declared_attr_ids`，merge 消费同一实例列表。直接 merge 仅在未提供 plan 时自行编译。
- attrs 必须包含每个 identity key，且 `attr_type` 必须为受支持的 `bool/float/int/integer/str/time`；缺失、空值或不支持类型即使在空批次也先拒绝。
- int 仅接受非 bool 的 int 和严格整数字符串，拒绝 float 截断；float 拒绝 bool、NaN 和正负 Infinity；time 拒绝 bool，其余值复用 `parse_cmdb_time`；str 仅接受基本标量，拒绝容器和非有限浮点，转换后仍不得为空。
- bool 仅接受 bool 或大小写规范化的 `true/false` 字符串。
- 所有 identity 在规范化后建立批内签名，`"1"`/`1`、`"1.0"`/`1.0`、bool 和等价时间仍能正确识别重复。
- `merge_service.coerce_identity` 文档已同步为严格规范化与异常合同，不再声称未知字段或失败转换会原样保留。

## TDD 证据

- 初始 F03/F04/F05/F22 RED 分别证明非法 identity、standard 未知/保留字段、quick caller `_id` 和规范化后重复可进入下游；修复后正式合同全部转为普通 GREEN。
- 第一轮审查的 identity/mode/单 plan 合同均先 RED 后 GREEN；真实 ingest→merge 测试证明旧实现重复读取 attrs 并可能在 Batch/字段登记后因快照漂移失败。
- 最后一轮严格输入域 RED：零副作用参数组首次为 `12 failed, 9 passed`，覆盖 bool-as-int、1.5→int、bool/NaN/Infinity→float、bool→time、list/dict/NaN→str 以及 identity 元数据缺失。
- 空批次 identity 元数据缺失独立 RED 为 `1 failed`，证明旧实现会继续进入凭据/Batch；提升元数据校验后转绿。
- 最终新增 identity/元数据/canonical 选择器：`39 passed`。拒绝路径均断言 credential `last_used_at`、Batch、字段登记、merge、GraphClient、Management 零副作用。

## 最终验证

测试环境：SQLite 内存库，`INSTALL_APPS=system_mgmt,node_mgmt,cmdb,cmdb_enterprise`，启用测试 `SECRET_KEY` 与 Celery 配置，统一使用 `--nomigrations`。

- Task 3 + Task 2 + recovery 综合回归：`138 passed, 22 xfailed in 12.36s`，退出码 0。22 项均为本任务范围外的既有 strict-xfail。
- 五个生产服务覆盖率：`105 passed, 15 xfailed in 19.22s`；432 statements、43 missed、总覆盖率 `90.05%`，通过 75% 门禁。
- 分文件覆盖率：schema 98%、ingest 91%、model 96%、task 89%、merge 68%。
- Flake8、五服务 `py_compile`、schema 单文件 Black、merge isort、`git diff --check` 均通过。
- schema 的 isort 与 Black 对模块级空行存在既有 #0497 配置冲突；最终以 Black + Flake8 为准，未做全仓格式化。

## 改动范围

生产 overlay：

- `server/apps/cmdb_enterprise/custom_reporting/services/schema_service.py`
- `server/apps/cmdb_enterprise/custom_reporting/services/task_service.py`
- `server/apps/cmdb_enterprise/custom_reporting/services/ingest_service.py`
- `server/apps/cmdb_enterprise/custom_reporting/services/model_service.py`
- `server/apps/cmdb_enterprise/custom_reporting/services/merge_service.py`

测试与合同：

- `server/apps/cmdb_enterprise/tests/test_custom_reporting_task_service.py`
- `server/apps/cmdb_enterprise/tests/test_custom_reporting_ingest_service.py`
- `server/apps/cmdb_enterprise/tests/test_custom_reporting_model_behavior.py`
- `server/apps/cmdb_enterprise/tests/test_custom_reporting_merge_service.py`
- `server/apps/cmdb_enterprise/tests/bdd/test_custom_reporting_bdd.py`
- `server/validation/custom_reporting/tests/test_runtime_contracts.py`
- `server/validation/custom_reporting/tests/test_failure_boundaries.py`
- `server/validation/custom_reporting/tests/test_resource_budget_contracts.py`
- `server/validation/custom_reporting/tests/test_recovery_contracts.py`

## projectmem 与疑虑

- 已关闭：#0312、#0313、#0314、#0486、#0501、#0502、#0503、#0504、#0505。
- #0497 继续跟踪 Black/isort 基线冲突。
- 商业 overlay 被根仓忽略，根仓 `git status` 不会完整列出生产 overlay 文件；交付时需按本报告清单核对商业仓差异。
- HTTP 401 回归期间仍有 system-manager 外部 DNS 记录失败日志，但响应语义与测试均通过，未扩展为本任务修复。
