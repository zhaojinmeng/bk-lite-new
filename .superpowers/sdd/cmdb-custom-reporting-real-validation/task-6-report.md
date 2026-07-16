# Task 6 报告：owner、部分失败、关系与清理一致性

## 范围

本任务只新增运行态合同测试和审查文档，不修改生产逻辑。所有数据库写入位于 SQLite
内存测试库；图查询、图写、snapshot 与审核异常均由有界记录器或单次故障注入观察，
没有真实外部写入或无界压力测试。

## 真实调用链与根因

- partial：ingest 收到 merge 的 `errors` 后未分支，继续 relation/backfill/snapshot，最终
  无条件保存 SUCCESS。
- owner：merge 查询 old_data 只按 `model_id`，写侧虽携带 `task_id=cr_<id>` 与 team，
  读侧没有 `collect_task` / `organization` 过滤；old_data 又直接成为 snapshot 候选。
- relation：process/backfill 信任 source direct `_id`，target 只按 model+identity 解析，
  两端均无 task.team 栅栏。Task 5 的 CRV-F06 是 source model 错配，本任务只记录组织
  越界，不重复 Finding。
- review：approve 先图删除、后保存 APPROVED；后半段失败无法回滚前半段，也没有 durable
  operation、outbox 或 reconciler。

## 真实 RED

未加 xfail 的整文件选择器输出：

```text
6 failed, 2 passed in 0.35s
```

- CRV-F07：`(rejected=False, batch="success", snapshot=1)`。
- CRV-F08：GraphClient filters 只有 model_id，缺 owner/team。
- CRV-F09：direct source/target 越界均 `(False, resolve=1, edge=1)`；pending/backfill 为
  `(False, resolve=1, edge=1, pending_remaining=0)`。
- CRV-F10：approved DB save 失败后 `deleted=[10,11]`、review=`pending`。
- 两项正向普通通过：图删除失败保持 pending；ratio 等于阈值直删、大于阈值送审、none
  策略不调用 snapshot。

## 安全收口

四项缺陷已登记 projectmem #0328—#0331，并在 Findings 中写入完整字段。缺陷测试仅在
观察值精确等于上述坏行为时抛 `KnownProductDefect`，marker 全部使用
`xfail(strict=True, raises=KnownProductDefect, reason="CRV-Fxx")`；其他断言、环境错误或
第三种行为保持真实失败。首轮收口输出：

```text
2 passed, 6 xfailed in 0.40s
```

## Fresh 验证

- Task 6 整文件：`2 passed, 6 xfailed in 0.37s`。
- Task 4—6 运行态合同组合：`11 passed, 18 xfailed in 1.80s`。
- `black --check`：1 file unchanged，退出码 0。
- `isort --check-only`：退出码 0。
- `flake8`：0 条告警，退出码 0。
- `git diff --check`：退出码 0。

默认 `uv run` 曾因用户缓存 `.git` 的沙箱读取权限失败；最终使用本机只读全局
black/isort/flake8 可执行文件完成相同门禁，未下载依赖或修改用户缓存。
