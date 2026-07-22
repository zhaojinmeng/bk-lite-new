# Task 4：关系合同 F06 / F15 / F24 实施报告

## 结果

- F06：导入与补偿写边前编译并校验关系计划；源模型必须等于任务模型，关联定义的源/目标模型及可选 mapping 必须与请求一致。校验失败时不标记凭据、不创建批次、不写 pending、不写边。
- F15：模型关联 mapping 统一限定为 `n:n`、`n:1`、`1:n`、`1:1`。HTTP、服务、实例校验、模型复制及历史 pending 补偿均拒绝缺失或非法 mapping，不再猜测默认值。
- F24：新增不可变 `GraphId` 值对象，只接受非负 `int`，明确拒绝 `bool` 和负数；`0` 在直接写边与补偿路径均视为合法 ID。
- I2 association 漂移：`RelationPlanItem.association` 现保存已验证的 id/src/dst/mapping 快照，并一路传至实例关联 Service；Service 在最终图写前重新读取当前定义并全量比较，漂移时 fail closed 且零实例边。
- I3 不可哈希 mapping：所有入口先要求 mapping 是字符串再检查枚举；list/dict 在 HTTP 返回 400，在 ModelManage、legacy relation、copy 等内部路径统一抛 `BaseAppException`，不再泄漏 `TypeError`。
- 未扩展到 F09 owner scope、F23 状态机或 F12 资源预算；未使用原生 SQL。

## TDD 证据

RED 阶段：

- F15 新增 7 个合同断言，原实现分别出现 HTTP 返回 200、服务未抛错、历史 pending 继续写边、复制路径猜测 `1:n`。
- F24 原有 6 个 strict-xfail，并新增 4 个目标 ID 非法用例，原实现均未满足合同。
- F06 原有 1 个 strict-xfail，并新增源/目标端点不一致用例，原实现仍产生副作用。
- I2 新增“plan 首次读取合法、最终写边前定义漂移”用例；原实现未消费 plan 快照，继续裸查询并未产生明确业务错误。
- I3 新增 service/legacy 的 list/dict mapping 用例；原实现 4 项均抛出未捕获 `TypeError`，HTTP 层用例已能返回 400。

GREEN 阶段：

- F15 聚焦：`7 passed`。
- F24 聚焦：`10 passed`。
- F06 聚焦：`4 passed`。
- Task 4 聚焦组合：`29 passed, 72 deselected`。
- runtime + failure + recovery 合同：`75 passed, 14 xfailed`。
- 包含 Task 2/3 邻接回归：`74 passed, 1 xfailed`。
- 关系服务与验证合同覆盖率：`80 passed, 14 xfailed`；`relation_service.py` 90%、`value_objects.py` 100%、总覆盖率 90.68%，通过 75% 门槛。
- 最终精确回归（SQLite 内存库、`--nomigrations`）：`22 passed`。
- I2/I3 首轮聚焦：`12 passed`；I3 HTTP/service/legacy/copy 全路径：`14 passed`。
- `InstanceManage` 普通调用兼容性与 I2 漂移精确回归：`5 passed`；普通调用仍走原查询/校验入口，不要求 expected 快照。
- I2/I3 最终综合合同：`83 passed, 14 xfailed`。
- 最新覆盖率：`relation_service.py` 93%、`value_objects.py` 100%、合计 93%，通过 75% 门槛。

## 静态检查

- 企业版 Task 4 的 7 个实现/测试文件：`flake8` 通过（0 项）。
- 6 个生产文件：`python -m py_compile` 通过。
- `git diff --check` 通过。
- I2/I3 企业版实现及 validation 合同文件 `flake8` 通过；社区大文件以致命规则 `E9/F63/F7/F82` 检查通过；全部 I2/I3 触及 Python 文件 `py_compile` 通过。
- 全部触及文件的 Black / isort 未通过既有大文件格式基线（projectmem #0497）；按最小 diff 与“不格式化既有大文件”要求未批量改写。社区版 flake8 命中 `instance.py` 既有 E303/E125、`model.py` 视图既有 F401/E301，不属于本任务新增关系合同。

## 变更文件

- `server/apps/cmdb/services/model.py`
- `server/apps/cmdb/services/instance.py`
- `server/apps/cmdb/views/model.py`
- `server/apps/cmdb_enterprise/custom_reporting/services/ingest_service.py`
- `server/apps/cmdb_enterprise/custom_reporting/services/relation_service.py`
- `server/apps/cmdb_enterprise/custom_reporting/services/value_objects.py`（新增）
- `server/apps/cmdb/tests/test_model_views.py`
- `server/apps/cmdb/tests/test_model_service_methods.py`
- `server/apps/cmdb/tests/test_model_service_advanced.py`
- `server/apps/cmdb_enterprise/tests/test_custom_reporting_relation_service.py`
- `server/validation/custom_reporting/tests/test_runtime_contracts.py`
- `server/validation/custom_reporting/tests/test_failure_boundaries.py`
- `server/validation/custom_reporting/tests/test_recovery_contracts.py`

## 自检与已知项

- 关联 mapping 的唯一来源为 `ModelManage.ASSOCIATION_MAPPINGS`，HTTP serializer 与服务层共用该合同。
- 导入路径在凭据 `mark_used` 与 Batch 创建前完成关系计划编译；直接及补偿路径均在 pending/edge 副作用前完成校验。
- I1 与 F23/#0487 同根，依赖 durable pending 状态、逐条隔离及 dead-letter，明确延期到计划 Task 11。本任务没有提前扫描全量 pending，也没有改变 ingest 主路径；当前单条 backfill 仍保持“失败零边、保留 pending”。
- enterprise overlay 文件在仓库根 Git 视图中被忽略，已通过直接测试、flake8 与 py_compile 验证，交付时需由集成方按该 worktree 的 overlay 机制收集。
- 更广的既有模型测试中 `test_model_attr_delete_ok` 受 SQLite JSON contains 限制失败（projectmem #0094），与本任务无关，未旁修。
- 未执行 `git add`、`git commit` 或推送。
