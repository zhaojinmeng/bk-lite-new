# Task 5 报告：standard / quick Schema 与身份契约

## 范围

本任务只扩展验证工厂、运行态合同测试与审查文档，没有修改生产逻辑。测试资源均使用
唯一 `crval_` 前缀，并只写入 SQLite 内存测试库。

## 真实调用链

- standard：`ingest_service.ingest` 直接把原始实例交给
  `merge_service.merge_instances`；Enterprise provider 未实现 Community 门面的
  `validate_instance_fields` / `validate_relation_fields`。
- quick：`ingest_service.ingest` 先经
  `ModelManage.register_custom_reporting_model_fields` 登记字段，再把原始实例交给 merge。
- identity：`merge_instances` 从 `task.config` 读取 identity keys，构造 `Management` 后
  调用 `add_inst` / `update_inst`。
- relation：ingest 调用 `relation_service.process`，存在关系或 pending 时同次调用
  `backfill`；错配 source model 会在回填时被任务模型替代。

## 真实 RED

聚焦选择器在未加 xfail 时输出：

```text
5 failed, 1 passed, 13 deselected in 0.34s
```

- CRV-F03：空 identity 未拒绝，`Management.add_inst` 与 `update_inst` 均调用 1 次；
  补充非法 identity `[""]` / `["_id"]` 的观察值相同。
- CRV-F04：standard 的未知字段与 `_id` 均未拒绝，原样进入 merge。
- quick 正向：统一事件列表严格证明合法新字段 `crval_owner` 的 register/create 事件先于
  merge，普通通过。
- CRV-F05：复审测试执行真实 merge 并捕获 `Management.add_inst` 入参；caller
  `cr_last_reported_at` 已被服务端覆盖，但 `_id=9001` 仍进入图写载荷。精确
  `KnownProductDefect` 只匹配该 `_id` 坏行为，不匹配时间戳。
- CRV-F06：source model 错配未拒绝，process 产生 pending，随后同次 backfill 清掉
  pending 并调用图写 1 次。

首轮曾有两项 quick 测试错误地 patch 函数内 import 的模块属性；修正为直接 patch
真实 `field_service` 后，错误消失并得到上述纯产品 RED。该装配修正未改变产品期望。

复审加固时再次移除 CRV-F03/F05 marker，真实 RED 为：

```text
4 failed, 1 passed, 16 deselected in 0.56s
```

这次 RED 精确证明 identity 的 add/update 双调用和 `_id` 最终图写载荷；quick 字段登记
顺序与 caller 时间戳被服务端覆盖均为普通正向断言。

## 安全收口

四个 Finding 已登记 projectmem #0312—#0315，并写入 `02-findings.md` 的完整七字段。
缺陷测试仅在观察值精确匹配已记录坏行为时抛 `KnownProductDefect`，marker 全部使用
`xfail(strict=True, raises=KnownProductDefect, reason="CRV-Fxx")`。聚焦收口输出：

最终聚焦与整文件结果见下方 fresh 验证。

环境异常、500、普通 AssertionError 或第三种行为不会被 xfail 吞掉；生产修复后会形成
strict XPASS 并使测试转红。

## Fresh 验证

- 聚焦选择器：`1 passed, 13 deselected, 7 xfailed in 1.04s`，包含 CRV-F05。
- 整个 `test_runtime_contracts.py`：`9 passed, 12 xfailed in 3.36s`。
- `black --check`：2 files unchanged，退出码 0。
- `isort --check-only`：退出码 0。
- `flake8`：0 条告警，退出码 0。
- `git diff --check`：退出码 0。
