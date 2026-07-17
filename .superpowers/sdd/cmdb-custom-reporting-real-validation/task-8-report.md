# Task 8 安全复审修正报告：真实自定义上报 HTTP 合同

## 修正范围

- 重写 `server/validation/custom_reporting/http_runner.py`，只调用生产已注册的 `tasks/`、`tasks/{id}/`、`rotate_credential/`、`revoke_credential/` 与 `ingest/`。
- 扩展 `server/validation/custom_reporting/ledger.py`，保留既有 `run_id_...` 名称合同，同时支持并严格校验 `run_id:<真实正整数 id>`。
- 重写 FakeTransport 合同测试，使用真实 WebUtils envelope、真实 task/config/quick_model 载荷、会话 Cookie、组织 Cookie 与凭据轮换数据流。
- 未修改生产业务逻辑，未发起真实网络请求或写入。

## 根因与真实 RED

首版驱动器使用了生产不存在的 `models/`、`tasks/{id}/fields/`、`tasks/{id}/ingest/`、`relations/`、`token/rotate/`、`token/revoke/` 与 `residuals/`，并把 CLI 与 programmatic 执行门绑定到同一 flag。

审查回归首次运行：

```text
17 failed, 34 deselected
```

失败覆盖真实接口、严格 envelope、loopback SSRF、重定向、会话/组织认证、真实 ID 账本、standard seed 模型归属、原子账本占位、独立三门与真实残留扫描。

## 最终安全合同

- 默认 dry-run，零 transport 调用；执行必须同时满足 CLI `--execute`、独立 `CRV_EXECUTE_CONFIRMED=1`、`CRV_ALLOW_WRITE=1`。
- 执行 URL 必须为 allowlist 精确匹配的 IP literal，且只能是 `127.0.0.0/8` 或 `::1`；禁止 DNS hostname，从源头消除 rebinding。所有 HTTP 重定向直接拒绝，不携带凭据重放。
- 管理 API 使用 `CRV_SESSION_COOKIE` 并追加 `current_team=<CRV_ORG_ID>`；ingest 仅使用任务签发/轮换后的 Bearer token。
- quick 创建发送真实 `team/config/quick_model/is_enabled`；standard 从 quick seed 返回的 `config.model_id` 取模型 ID，按 seed 真实 task ID 删除后再创建 standard。
- 严格解码 `{result,data,message}`；缺字段、类型错误、`result=false`、非 JSON、非 2xx、超限响应均 fail-closed。
- 内部保留 raw token 以完成 rotate 后 ingest 与 revoke；plan/result/error/ledger 统一按敏感键子串和已知 secret 值递归脱敏。
- 首个 POST 前以 `open("x")` 原子创建有效 JSON 空账本；后续用唯一临时文件、fsync、replace 更新；已有路径和并发占位直接拒绝。
- task/credential/batch 用 `run_id:<真实 int id>` 记账；清理只严格解析账本 task ID 并调用真实 `DELETE tasks/{id}/`。
- 删除虚构 residual endpoint；清理后用真实 task list 按 run_id 扫描，严格校验分页结构并限制最多 20 页。
- quick 隐式创建的模型无真实自定义上报删除/残留 API；cleanup 不假成功，保留 ledger 并抛 `CleanupIncompleteError`，交 Task 9 verifier 核验模型图资源。
- `--verify-ledger`、`--cleanup-ledger` 继续明确拒绝，未伪装实现。

## TDD 与门禁证据

最终聚焦命令：

```bash
PYTHONPATH=. .venv/bin/pytest -p no:django \
  --confcutdir=validation/custom_reporting -c pytest.ini -q -o addopts='' \
  --cov=validation.custom_reporting.http_runner --cov-report=term \
  --cov-fail-under=90 \
  validation/custom_reporting/tests/test_http_runner.py \
  validation/custom_reporting/tests/test_ledger.py
```

结果：

```text
103 passed in 0.52s
validation/custom_reporting/http_runner.py  337 statements  30 missed  91%
Total coverage: 91.10%
```

四个触及 Python 文件通过 `black --check`、`isort --check-only`、`flake8`。测试使用 FakeTransport，未真实联网。
