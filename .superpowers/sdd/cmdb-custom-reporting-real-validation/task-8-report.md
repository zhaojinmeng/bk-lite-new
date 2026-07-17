# Task 8 安全复审修正报告：真实自定义上报 HTTP 合同

## 修正范围

- 重写 `server/validation/custom_reporting/http_runner.py`，只调用生产已注册的 `tasks/`、`tasks/{id}/`、`rotate_credential/`、`revoke_credential/`、`ingest/`，以及模型管理的 `POST model/association/`、`DELETE model/association/{model_asst_id}/`。
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
- 删除虚构 residual endpoint；清理前后都用真实 task list 按 run_id 扫描，严格要求当前 API 的单页 `next=None`、`count==len(results)` 且不超过 page_size。
- quick 隐式创建的模型无真实自定义上报删除/残留 API；cleanup 不假成功，保留 ledger 并抛 `CleanupIncompleteError`，交 Task 9 verifier 核验模型图资源。
- `--verify-ledger`、`--cleanup-ledger` 继续明确拒绝，未伪装实现。

## 第二轮安全复审修正

第二轮审查先新增 8 项模型归属、cleanup 与关系计划回归，首次结果为
`8 failed, 38 deselected`；随后另以单项 RED 证明 runner 尚未执行关系多步调用。

- quick 创建响应的 `config.model_id` 必须精确等于请求的
  `quick_model.model_id`；standard 响应也必须精确等于 seed 模型 ID。错配会在首个
  ingest 前失败，但 task/credential 已立即记账以便精准清理。model ledger 直接记录
  验证后的真实 `model_id`，不再重复拼接 run_id。
- cleanup 在任何 DELETE 前调用真实 task list，只接受严格 WebUtils envelope 和单页合同：
  `next is None`、`count == len(results)`、`count <= page_size`。列表任务名只能精确为本
  run 的 quick/standard 名称，ID/名称必须与 ledger 创建顺序一致；seed 已不存在可安全
  跳过。任一未记账任务、名称异常、DELETE 错误或删除后残留均保留 ledger 并失败。
- 关系不使用虚构 endpoint。计划和 runner 均通过 `/ingest/` 执行三步真实载荷：同批
  source+target 的立即关系、未来 target 的 pending relation、后续仅上报 target 以触发
  服务端 backfill；每步严格校验真实 summary 并记录 batch。FakeTransport 只模拟真实
  WebUtils envelope。Task 8 不声称图边已验证，图事实仍由 Task 9 核验。
- revoke 后分别以轮换前 token 与已吊销新 token 发送空 instances/relations 的最小 ingest；
  只有明确 `result=false` 或 HTTP 401/403 才判为通过，不增加资产实例副作用。

第二轮 fresh GREEN：`112 passed in 0.44s`；`http_runner.py` 93%、`ledger.py` 100%，
总覆盖率 `94.12%`。

## 第三轮安全复审修正

第三轮先以 4 组行为回归取得真实 `4 failed`，分别覆盖：模型意图必须先于 quick
POST 落账、真实模型关联必须先于任何 relation ingest 创建、泛化 `result=false`
不得冒充 token 作废证据、cleanup 协议失败必须统一为 `CleanupIncompleteError`。
修正 association 小写归属边界时又补充独立 RED，最终全部转绿。

- 生产路由证据为 `POST /api/v1/cmdb/api/model/association/` 与
  `DELETE /api/v1/cmdb/api/model/association/{model_asst_id}/`；创建 payload 只使用
  View 真实读取的 `src_model_id/dst_model_id/asst_id`，复用真实 session cookie 与
  `current_team`。`quick_model` 的生产 bootstrap 仅创建模型与字段，不支持关联声明，
  因此未虚构 quick payload 扩展。
- 每个 run 以 nonce 构造唯一 self association 类型 `crv_rel_<nonce>`；在创建请求前
  持久化 association intent，响应后严格核对 `_id`、两端 model、`asst_id` 与
  `model_asst_id`，再记录真实 `model_asst_id`。企业 relation service 实际按
  `model_asst_id` 查询模型关联，所以三步 ingest 使用创建响应的真实
  `model_asst_id`，而不是错误地使用关系类型短名 `asst_id`。
- `expected_model_id` 在 quick POST 前写入并持久化；即使服务器返回替换后的 model，
  账本仍保留预期模型 marker，standard 仅复用该 marker，不重复制造归属记录。
- rotate 后新 token 成功 ingest 后，立即用旧 token 验证明确拒绝；随后 revoke，再用
  新 token 验证。仅 HTTP 401/403，或 WebUtils message 同时稳定命中 token/credential
  主体与 invalid/revoked/expired/disabled 状态才接受；任意 `result=false` 明确失败。
- cleanup 仅按账本中的真实 `model_asst_id` 调用精确 DELETE；intent 用于响应丢失时的
  崩溃恢复，不做名称扫描。分页、DELETE、账本归属与协议错误都会先保留账本，再统一
  抛不含下游详情的 `CleanupIncompleteError`；原本已是该类型则原样保留。

第三轮 fresh GREEN：`120 passed in 1.93s`；`http_runner.py` 93%、`ledger.py` 100%，
总覆盖率 `93.70%`。

## TDD 与门禁证据

最终聚焦命令：

```bash
PYTHONPATH=. .venv/bin/pytest -p no:django \
  --confcutdir=validation/custom_reporting -c pytest.ini -q -o addopts='' \
  --cov=validation.custom_reporting.http_runner \
  --cov=validation.custom_reporting.ledger --cov-report=term \
  --cov-fail-under=90 \
  validation/custom_reporting/tests/test_http_runner.py \
  validation/custom_reporting/tests/test_ledger.py
```

第三轮最终结果：

```text
120 passed in 1.93s
validation/custom_reporting/http_runner.py  427 statements  32 missed  93%
validation/custom_reporting/ledger.py        81 statements   0 missed 100%
Total coverage: 93.70%
```

四个触及 Python 文件通过 `black --check`、`isort --check-only`、`flake8`。测试使用 FakeTransport，未真实联网。
