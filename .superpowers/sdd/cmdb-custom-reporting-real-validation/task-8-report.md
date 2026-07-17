# Task 8 实施报告：默认 dry-run 的安全 HTTP E2E 驱动器

## 交付范围

- 新增 `server/validation/custom_reporting/http_runner.py`，提供 quick/standard 可执行计划、安全 HTTP 客户端、可注入 transport、账本持久化与精准 cleanup。
- 新增 `server/validation/custom_reporting/tests/test_http_runner.py`，所有 HTTP 与 DNS 行为均由 fake transport/fake resolver 验证，未真实联网。
- 未修改生产业务逻辑。

## TDD 证据

首次有效 RED（排除 Django/uv 环境阻断后）：

```text
ModuleNotFoundError: No module named 'validation.custom_reporting.http_runner'
```

自审阶段另以单测复现已有 ledger 被覆盖风险：执行在拒绝前进入首个 POST，失败于 `HttpProtocolError: HTTP transport failed`。随后增加执行前 ledger 路径存在性栅栏，确认网络请求数为 0 且原文件不变。

## 安全合同实现

- 写请求必须同时满足 `execute=True`、`cli_execute=True`（CLI `--execute`）和 `CRV_ALLOW_WRITE=1`；默认输出 dry-run 计划且 `requests_sent=0`。
- 初始化和每次请求/重定向均验证 http/https、无 userinfo、精确 host allowlist；DNS 必须解析到非空公网 IP，自动重定向关闭并显式限制为最多 3 次。
- Authorization/Cookie/Token 等秘密不进入计划、返回结果、异常或账本；transport 异常统一收敛，不回显底层敏感上下文。
- connect/read timeout 固定为 3/10 秒，响应体上限 1 MiB；非 2xx、超限、非 JSON、非 JSON object 均立即失败并停止后续写。
- 所有名称绑定唯一 `ledger.run_id`；创建成功立即原子写 ledger，已有 ledger 路径拒绝覆盖。
- standard 只复用本 run quick seed model，先删除 seed task，再创建 standard task。
- cleanup 仅迭代 `ledger.cleanup_plan()`，删除路径由固定 kind 映射生成；残留扫描只携带本 run_id 和账本 identifier。失败保留 ledger 并抛 `CleanupIncompleteError`。
- CLI 支持默认 `--dry-run`、`--execute`、`--ledger`；预留 `--verify-ledger`、`--cleanup-ledger` 会明确报“尚未实现”并拒绝执行。

## 验证结果

聚焦测试与覆盖率：

```text
92 passed in 0.46s
validation/custom_reporting/http_runner.py  277 statements  17 missed  94%
Total coverage: 93.86%
```

验证命令：

```bash
PYTHONPATH=. .venv/bin/pytest -p no:django --confcutdir=validation/custom_reporting \
  -c pytest.ini -q -o addopts='' \
  --cov=validation.custom_reporting.http_runner --cov-report=term --cov-fail-under=90 \
  validation/custom_reporting/tests/test_http_runner.py \
  validation/custom_reporting/tests/test_ledger.py
```

静态门禁：两个新增 Python 文件均通过 `black --check`、`isort --check-only`、`flake8`；`git diff --check` 通过。危险全表删除、按非 run_id 名称扫描删除和直接 `requests.get/post/...` 调用均为零命中。

## 自审结论

逐项复核任务简报 10 条强制安全合同，未发现未处置的 Critical/Important 问题。真实联网与真实写入均未发生；Task 9 预留命令保持 fail-closed。
