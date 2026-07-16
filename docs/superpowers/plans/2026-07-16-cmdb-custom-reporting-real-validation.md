# CMDB 自定义上报双模式真实验证实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在隔离 worktree 中固定可追溯交付基线与当前运行态 overlay，安全执行标准/快速模式的契约、故障和真实 HTTP/FalkorDB 验证，并交付代码质量与发布结论。

**Architecture:** 验证代码独立放在 `server/validation/custom_reporting/`，不进入社区或企业业务包；它负责制品固化、测试账本、HTTP 驱动和精准清理。测试分为 SQLite/可控图边界的契约与故障注入、Django 真实组件集成、允许写入环境的真实 HTTP + FalkorDB E2E，所有证据汇总到独立 review 目录。

**Tech Stack:** Python 3.12、Django 4.2、DRF、pytest/pytest-django/pytest-cov、requests、Celery、FalkorDB、SHA-256、SQLite 隔离测试库。

## Global Constraints

- 工作目录固定为 `.worktrees/cmdb-custom-reporting-real-validation`，分支固定为 `codex/cmdb-custom-reporting-real-validation`。
- 不连接或写入生产环境；真实写入必须同时满足显式允许变量、主机 allowlist 和唯一 `run_id`。
- 不直接运行 `server/scripts/custom_reporting_e2e_test.py`。
- 不修改生产逻辑；发现缺陷只补复现测试、证据和报告，修复另开 TDD 计划。
- 不复用已有任务、模型或凭据，不轮换非本次 Token，不删除非本次 `run_id` 数据。
- 运行态 overlay 只作为固定制品测试，不能冒充 `enterprise@1e9c3d2`。
- 相关模块行覆盖率目标不低于 80%，核心 ingest/merge/relation/cleanup 目标不低于 90%。
- 任何未处置 P0/P1 均使最终发布建议为 `Block`。

## 文件结构

- Create: `server/validation/custom_reporting/__init__.py` — 验证工具包标记。
- Create: `server/validation/custom_reporting/artifact.py` — overlay 文件清单、单文件和聚合 SHA-256。
- Create: `server/validation/custom_reporting/ledger.py` — `run_id`、资源账本与精准清理计划。
- Create: `server/validation/custom_reporting/http_runner.py` — dry-run 默认的真实 HTTP/E2E 驱动器。
- Create: `server/validation/custom_reporting/tests/test_artifact.py` — 制品固化测试。
- Create: `server/validation/custom_reporting/tests/test_ledger.py` — 隔离与清理边界测试。
- Create: `server/validation/custom_reporting/tests/test_runtime_contracts.py` — 控制面、Schema、身份与 Token 契约。
- Create: `server/validation/custom_reporting/tests/test_failure_boundaries.py` — 部分失败、owner、关系和清理故障测试。
- Create: `server/validation/custom_reporting/tests/test_task_registration.py` — Celery/Beat 注册一致性测试。
- Create: `server/validation/custom_reporting/tests/test_http_runner.py` — E2E 驱动器行为测试。
- Create: `server/validation/custom_reporting/tests/factories.py` — 验证专用任务与 Token 工厂。
- Create: `docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/00-baseline.md` — 双基线与环境证据。
- Create: `docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/overlay-sha256.txt` — 固定运行态文件清单。
- Create: `docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/01-test-matrix.md` — 场景与结果矩阵。
- Create: `docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/02-findings.md` — P0–P3 Findings。
- Create: `docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/03-architecture-quality.md` — 架构与代码质量审查。
- Create: `docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/04-release-recommendation.md` — 最终发布建议。

---

### Task 1: 固定双基线与运行态 overlay 制品

**Files:**
- Create: `server/validation/custom_reporting/artifact.py`
- Test: `server/validation/custom_reporting/tests/test_artifact.py`
- Create: `docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/00-baseline.md`
- Create: `docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/overlay-sha256.txt`

**Interfaces:**
- Produces: `build_manifest(root: Path) -> list[ArtifactEntry]`、`aggregate_digest(entries) -> str`、可复制到 worktree 的固定清单。

- [ ] **Step 1: 写制品清单失败测试**

```python
def test_manifest_excludes_runtime_files_and_is_stable(tmp_path):
    (tmp_path / "provider.py").write_text("x = 1\n")
    (tmp_path / "module.pyc").write_bytes(b"cache")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "provider.pyc").write_bytes(b"cache")
    first = build_manifest(tmp_path)
    second = build_manifest(tmp_path)
    assert [entry.path for entry in first] == ["provider.py"]
    assert first == second
    assert aggregate_digest(first) == aggregate_digest(second)
```

- [ ] **Step 2: 确认 RED**

Run: `cd server && /Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/server/.venv/bin/pytest -q -o addopts='' validation/custom_reporting/tests/test_artifact.py`

Expected: FAIL，`build_manifest` 尚不存在。

- [ ] **Step 3: 实现稳定 SHA-256 清单**

```python
@dataclass(frozen=True)
class ArtifactEntry:
    path: str
    sha256: str

def build_manifest(root: Path) -> list[ArtifactEntry]:
    files = sorted(
        path for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    )
    return [ArtifactEntry(str(path.relative_to(root)), sha256(path.read_bytes()).hexdigest()) for path in files]

def aggregate_digest(entries: list[ArtifactEntry]) -> str:
    payload = "".join(f"{entry.sha256}  {entry.path}\n" for entry in entries)
    return sha256(payload.encode()).hexdigest()
```

- [ ] **Step 4: 固定来源并验证复制前后相同**

Run: `cd server && /Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/server/.venv/bin/python -m validation.custom_reporting.artifact --source /Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/server/apps/cmdb_enterprise --destination apps/cmdb_enterprise --manifest ../docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/overlay-sha256.txt`

Expected: 输出源/目标相同聚合 SHA-256，目标只包含清单内文件；`enterprise@1e9c3d2` 的缺失行为模块单独写入 `00-baseline.md`。

- [ ] **Step 5: 提交**

```bash
git add server/validation/custom_reporting docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/00-baseline.md docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/overlay-sha256.txt
git commit -m "test(cmdb): 固定自定义上报双基线制品"
```

### Task 2: 建立安全运行账本与清理边界

**Files:**
- Create: `server/validation/custom_reporting/ledger.py`
- Test: `server/validation/custom_reporting/tests/test_ledger.py`

**Interfaces:**
- Produces: `ValidationLedger.create() -> ValidationLedger`、`record(kind, identifier)`、`cleanup_plan() -> list[ResourceRef]`。
- Consumes: Task 1 固定的 overlay。

- [ ] **Step 1: 写 run_id、逆序清理与越界拒绝测试**

```python
def test_cleanup_plan_contains_only_current_run_resources():
    ledger = ValidationLedger.create(now="20260716T071500Z", nonce="a1b2c3")
    ledger.record("task", "crval_20260716T071500Z_a1b2c3_task")
    ledger.record("instance", 101)
    assert ledger.cleanup_plan() == [
        ResourceRef("instance", 101),
        ResourceRef("task", "crval_20260716T071500Z_a1b2c3_task"),
    ]
    with pytest.raises(ValueError, match="不属于当前 run_id"):
        ledger.record("task", "existing-production-task")
```

- [ ] **Step 2: 确认 RED**

Run: `cd server && /Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/server/.venv/bin/pytest -q -o addopts='' validation/custom_reporting/tests/test_ledger.py`

Expected: FAIL，`ValidationLedger` 尚不存在。

- [ ] **Step 3: 实现账本和固定资源删除顺序**

```python
CLEANUP_ORDER = {"edge": 0, "instance": 1, "review": 2, "pending": 3, "batch": 4, "credential": 5, "task": 6, "association": 7, "model": 8}

def record(self, kind: str, identifier: str | int) -> None:
    if isinstance(identifier, str) and kind in {"task", "association", "model"} and self.run_id not in identifier:
        raise ValueError("资源不属于当前 run_id")
    self.resources.append(ResourceRef(kind, identifier))

def cleanup_plan(self) -> list[ResourceRef]:
    return sorted(reversed(self.resources), key=lambda item: CLEANUP_ORDER[item.kind])
```

- [ ] **Step 4: 验证 GREEN 与序列化恢复**

Run: `cd server && /Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/server/.venv/bin/pytest -q -o addopts='' validation/custom_reporting/tests/test_ledger.py`

Expected: PASS；账本 JSON 往返后清理计划一致。

- [ ] **Step 5: 提交**

```bash
git add server/validation/custom_reporting/ledger.py server/validation/custom_reporting/tests/test_ledger.py
git commit -m "test(cmdb): 增加自定义上报验证资源账本"
```

### Task 3: 运行社区与运行态 overlay 完整基线

**Files:**
- Modify: `docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/00-baseline.md`
- Create: `docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/01-test-matrix.md`

**Interfaces:**
- Produces: 社区无 overlay 结果、运行态 overlay 全量结果、逐模块覆盖率和未覆盖行。

- [ ] **Step 1: 运行社区无 overlay 基线**

Run: `MINIO_ENDPOINT=localhost:9000 MINIO_ACCESS_KEY=test MINIO_SECRET_KEY=test MINIO_USE_HTTPS=false DB_ENGINE=sqlite DB_NAME=:memory: INSTALL_APPS=system_mgmt,node_mgmt,cmdb /Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/server/.venv/bin/pytest -q -o addopts='' --nomigrations apps/cmdb/tests/test_custom_reporting_extension.py apps/cmdb/tests/test_model_custom_reporting_delegation.py`

Expected: 6 passed。

- [ ] **Step 2: 运行 overlay 全量自定义上报测试与覆盖率**

Run: `MINIO_ENDPOINT=localhost:9000 MINIO_ACCESS_KEY=test MINIO_SECRET_KEY=test MINIO_USE_HTTPS=false DB_ENGINE=sqlite DB_NAME=:memory: INSTALL_APPS=system_mgmt,node_mgmt,cmdb,cmdb_enterprise /Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/server/.venv/bin/pytest -q -o addopts='' --nomigrations apps/cmdb_enterprise/tests/test_custom_reporting_*.py apps/cmdb_enterprise/tests/bdd/test_custom_reporting_bdd.py --cov=apps.cmdb_enterprise.custom_reporting --cov=apps.cmdb.custom_reporting --cov=apps.cmdb.views.custom_reporting --cov=apps.cmdb.serializers.custom_reporting --cov-report=term-missing`

Expected: 当前基线为 81 passed、总覆盖率约 83%；把实际数字和核心模块缺口写入矩阵，不把预期数字当实际结果。

- [ ] **Step 3: 提交基线证据**

```bash
git add docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/00-baseline.md docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/01-test-matrix.md
git commit -m "docs(cmdb): 记录自定义上报测试基线"
```

### Task 4: 复现控制面授权与 Token 能力边界

**Files:**
- Create: `server/validation/custom_reporting/tests/factories.py`
- Create: `server/validation/custom_reporting/tests/test_runtime_contracts.py`
- Modify: `docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/01-test-matrix.md`
- Modify: `docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/02-findings.md`

**Interfaces:**
- Produces: `TokenTask(task, raw_token)`、`create_token_task(...) -> TokenTask`，以及 create/update team 授权、功能权限、Token 轮换/作废证据。

- [ ] **Step 1: 建立验证专用任务与 Token 工厂**

```python
@dataclass(frozen=True)
class TokenTask:
    task: CustomReportingTask
    raw_token: str

def create_token_task(*, mode="standard", team=None, identity_keys=None, cleanup_strategy="none") -> TokenTask:
    suffix = uuid.uuid4().hex[:8]
    task = CustomReportingTask.objects.create(
        name=f"crval_{suffix}_task",
        team=team or [1],
        config={
            "mode": mode,
            "model_id": f"crval_{suffix}_model",
            "identity_keys": ["inst_name"] if identity_keys is None else identity_keys,
            "cleanup_strategy": cleanup_strategy,
        },
        created_by="validator",
        updated_by="validator",
    )
    credential = CustomReportingCredential.objects.create(task=task, name="validator", credential_type="api_token", credential_data={})
    return TokenTask(task=task, raw_token=credential.issue_token())
```

- [ ] **Step 2: 写跨组织创建与改组复现测试**

```python
@pytest.mark.django_db
def test_create_and_update_reject_unallowed_team(api_client, django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="crval_auth_user")
    api_client.force_authenticate(user)
    monkeypatch.setattr(CustomReportingProvider, "_allowed_orgs", staticmethod(lambda request: [1]))
    create = api_client.post("/api/v1/cmdb/api/custom_reporting/tasks/", {
        "name": "crval_auth_task", "team": [2], "config": {"mode": "standard", "model_id": "crval_auth_model", "identity_keys": ["inst_name"]}, "is_enabled": True,
    }, format="json")
    assert create.status_code == 403
    assert CustomReportingTask.objects.count() == 0
```

- [ ] **Step 3: 运行并确认现状，而非修改业务代码**

Run: `cd server && MINIO_ENDPOINT=localhost:9000 MINIO_ACCESS_KEY=test MINIO_SECRET_KEY=test MINIO_USE_HTTPS=false DB_ENGINE=sqlite DB_NAME=:memory: INSTALL_APPS=system_mgmt,node_mgmt,cmdb,cmdb_enterprise /Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/server/.venv/bin/pytest -q -o addopts='' --nomigrations validation/custom_reporting/tests/test_runtime_contracts.py -k 'team or permission or token'`

Expected: 未授权 team 用例在当前实现失败，失败响应与副作用构成 Finding 证据；Token 作废正向用例通过。

- [ ] **Step 4: 记录 Finding 并提交复现测试**

Finding 必须写明 Location、Trigger、Evidence、Impact、Root Cause、Why Existing Tests Missed It、Required Tests；不在此任务写修复。

```bash
git add server/validation/custom_reporting/tests/factories.py server/validation/custom_reporting/tests/test_runtime_contracts.py docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16
git commit -m "test(cmdb): 复现自定义上报授权边界"
```

### Task 5: 复现标准/快速模式 Schema 与身份契约

**Files:**
- Modify: `server/validation/custom_reporting/tests/test_runtime_contracts.py`
- Modify: `docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/01-test-matrix.md`
- Modify: `docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/02-findings.md`

**Interfaces:**
- Produces: 两种模式差异、空/非法 identity、未知/保留字段和端点模型错配证据。

- [ ] **Step 1: 写空身份键静默折叠复现**

```python
def test_empty_identity_keys_rejected_before_graph_write(monkeypatch):
    token_task = create_token_task(identity_keys=[])
    write = Mock()
    monkeypatch.setattr(Management, "add_inst", write)
    with pytest.raises(BaseAppException, match="身份键"):
        merge_service.merge_instances(token_task.task, token_task.task.config["model_id"], [{"inst_name": "a"}, {"inst_name": "b"}], "validator")
    write.assert_not_called()
```

- [ ] **Step 2: 写 standard 未知字段与 quick 字段登记对照测试**

```python
def test_enterprise_provider_must_implement_schema_validation():
    provider = CustomReportingProvider()
    assert type(provider).validate_instance_fields is not CustomReportingExtension.validate_instance_fields
    assert type(provider).validate_relation_fields is not CustomReportingExtension.validate_relation_fields

def test_quick_mode_registers_new_business_field(monkeypatch):
    token_task = create_token_task(mode="quick")
    register = Mock(return_value=["owner"])
    monkeypatch.setattr(ModelManage, "register_custom_reporting_model_fields", register)
    monkeypatch.setattr(merge_service, "merge_instances", lambda *args: {"created": 1, "updated": 0, "errors": 0, "covered_ids": [1], "old_data": [], "index": {}})
    monkeypatch.setattr(relation_service, "process", lambda *args: {"created": 0, "pending": 0})
    ingest_service.ingest(token_task.raw_token, {"instances": [{"inst_name": "a", "owner": "ops"}]})
    register.assert_called_once()
```

- [ ] **Step 3: 运行全部 Schema/identity 用例**

Run: `cd server && MINIO_ENDPOINT=localhost:9000 MINIO_ACCESS_KEY=test MINIO_SECRET_KEY=test MINIO_USE_HTTPS=false DB_ENGINE=sqlite DB_NAME=:memory: INSTALL_APPS=system_mgmt,node_mgmt,cmdb,cmdb_enterprise /Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/server/.venv/bin/pytest -q -o addopts='' --nomigrations validation/custom_reporting/tests/test_runtime_contracts.py -k 'identity or schema or field or relation_endpoint'`

Expected: 当前空身份键、standard 未知字段和关系端点模型错配用例失败；quick 合法字段登记正向用例通过。

- [ ] **Step 4: 记录并提交**

```bash
git add server/validation/custom_reporting/tests/test_runtime_contracts.py docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16
git commit -m "test(cmdb): 复现自定义上报模式契约缺口"
```

### Task 6: 复现 owner、部分失败、关系与清理一致性

**Files:**
- Create: `server/validation/custom_reporting/tests/test_failure_boundaries.py`
- Modify: `docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/01-test-matrix.md`
- Modify: `docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/02-findings.md`

**Interfaces:**
- Produces: 同模型多任务隔离、partial batch、snapshot、关系越权和审核崩溃窗口证据。

- [ ] **Step 1: 写部分更新失败不得清理测试**

```python
@pytest.mark.django_db
def test_partial_merge_marks_batch_failed_and_skips_snapshot(monkeypatch):
    token_task = create_token_task(cleanup_strategy="snapshot")
    monkeypatch.setattr(merge_service, "merge_instances", lambda *args: {"created": 1, "updated": 0, "errors": 1, "covered_ids": [1], "old_data": [{"_id": 1}, {"_id": 2}], "index": {}})
    snapshot = Mock()
    monkeypatch.setattr(cleanup_service, "apply_snapshot", snapshot)
    with pytest.raises(BaseAppException, match="部分失败"):
        ingest_service.ingest(token_task.raw_token, {"instances": [{"inst_name": "a"}, {"inst_name": "b"}]})
    assert CustomReportingBatch.objects.get().status != CustomReportingBatch.STATUS_SUCCESS
    snapshot.assert_not_called()
```

- [ ] **Step 2: 写同模型不同 owner 不覆盖/不删除测试**

```python
def test_merge_query_is_scoped_by_owner_and_team(monkeypatch):
    token_task = create_token_task()
    filters_seen = []
    class RecordingGraph:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def query_entity(self, entity, filters):
            filters_seen.extend(filters)
            return ([], 0)
    monkeypatch.setattr(merge_service, "GraphClient", RecordingGraph)
    monkeypatch.setattr(ModelManage, "search_model_attr", lambda model_id: [])
    monkeypatch.setattr(Management, "add_inst", lambda self, items: {"success": [], "failed": []})
    monkeypatch.setattr(Management, "update_inst", lambda self, items: {"success": [], "failed": []})
    merge_service.merge_instances(token_task.task, token_task.task.config["model_id"], [], "validator")
    assert {"field": "collect_task", "type": "str=", "value": f"cr_{token_task.task.id}"} in filters_seen
    assert any(item["field"] == "organization" for item in filters_seen)
```

- [ ] **Step 3: 运行故障边界测试**

Run: `cd server && MINIO_ENDPOINT=localhost:9000 MINIO_ACCESS_KEY=test MINIO_SECRET_KEY=test MINIO_USE_HTTPS=false DB_ENGINE=sqlite DB_NAME=:memory: INSTALL_APPS=system_mgmt,node_mgmt,cmdb,cmdb_enterprise /Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/server/.venv/bin/pytest -q -o addopts='' --nomigrations validation/custom_reporting/tests/test_failure_boundaries.py`

Expected: partial、owner scope、关系双端授权和跨存储恢复断言暴露当前缺口；已有阈值正向用例继续通过。

- [ ] **Step 4: 记录并提交**

```bash
git add server/validation/custom_reporting/tests/test_failure_boundaries.py docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16
git commit -m "test(cmdb): 复现自定义上报一致性缺陷"
```

### Task 7: 验证 Celery 注册与资源预算

**Files:**
- Create: `server/validation/custom_reporting/tests/test_task_registration.py`
- Modify: `docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/02-findings.md`

**Interfaces:**
- Produces: Beat schedule 到 Worker 注册表的可执行契约，以及请求/扫描预算缺失清单。

- [ ] **Step 1: 写任务注册一致性测试**

```python
def test_every_enterprise_beat_task_is_registered():
    from apps.core.celery import app
    from apps.cmdb_enterprise.config import CELERY_BEAT_SCHEDULE
    app.loader.import_default_modules()
    missing = sorted(item["task"] for item in CELERY_BEAT_SCHEDULE.values() if item["task"] not in app.tasks)
    assert missing == []
```

- [ ] **Step 2: 运行并确认当前 expire task 是否缺失**

Run: `cd server && ENABLE_CELERY=true MINIO_ENDPOINT=localhost:9000 MINIO_ACCESS_KEY=test MINIO_SECRET_KEY=test MINIO_USE_HTTPS=false DB_ENGINE=sqlite DB_NAME=:memory: INSTALL_APPS=system_mgmt,node_mgmt,cmdb,cmdb_enterprise /Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/server/.venv/bin/pytest -q -o addopts='' --nomigrations validation/custom_reporting/tests/test_task_registration.py`

Expected: 若 `custom_reporting_expire_cleanup` 未注册，测试明确列出完整 task name。

- [ ] **Step 3: 运行有界静态扫描并记录资源预算**

Run: `rg -n 'request\.data|query_entity|\.objects\.all\(\)|filter\(is_enabled=True\)|for .* in .*credentials|relations|instances' server/apps/cmdb/views/custom_reporting.py server/apps/cmdb_enterprise/custom_reporting`

Expected: 报告逐项记录已有上限及缺失上限；不执行无上限压力攻击。

- [ ] **Step 4: 提交**

```bash
git add server/validation/custom_reporting/tests/test_task_registration.py docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/02-findings.md
git commit -m "test(cmdb): 验证自定义上报任务与资源边界"
```

### Task 8: 实现 dry-run 默认的真实 HTTP E2E 驱动器

**Files:**
- Create: `server/validation/custom_reporting/http_runner.py`
- Create: `server/validation/custom_reporting/tests/test_http_runner.py`

**Interfaces:**
- Consumes: `ValidationLedger`、`CRV_BASE_URL`、`CRV_SESSION_COOKIE`、`CRV_ORG_ID`、`CRV_ALLOWED_HOSTS`。
- Produces: `run_validation(execute: bool) -> ValidationResult`，默认只输出计划；`execute=True` 才写入。

- [ ] **Step 1: 写默认 dry-run、主机 allowlist 和秘密脱敏测试**

```python
def test_runner_requires_explicit_execute_and_allowed_host(monkeypatch):
    runner = HttpValidationRunner(base_url="http://127.0.0.1:8011", allowed_hosts={"127.0.0.1"}, cookie="secret-cookie")
    result = runner.run(execute=False)
    assert result.requests_sent == 0
    assert "secret-cookie" not in result.rendered_plan
    with pytest.raises(ValueError, match="不在允许主机"):
        HttpValidationRunner(base_url="https://production.example.com", allowed_hosts={"127.0.0.1"}, cookie="x")
```

- [ ] **Step 2: 确认 RED**

Run: `cd server && /Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/server/.venv/bin/pytest -q -o addopts='' validation/custom_reporting/tests/test_http_runner.py`

Expected: FAIL，runner 尚不存在。

- [ ] **Step 3: 实现 HTTP 客户端与唯一资源流程**

```python
CUSTOM_REPORTING = "/api/v1/cmdb/api/custom_reporting/tasks"
INGEST = "/api/v1/cmdb/api/custom_reporting/ingest/"

def create_quick_task(self, suffix: str) -> dict:
    model_id = f"{self.ledger.run_id}_{suffix}_model"
    return self.post(f"{CUSTOM_REPORTING}/", {
        "name": f"{self.ledger.run_id}_{suffix}_task",
        "team": [self.org_id],
        "config": {"mode": "quick", "identity_keys": ["inst_name"], "cleanup_strategy": "none"},
        "quick_model": {"model_id": model_id, "model_name": model_id, "identity_keys": ["inst_name"]},
        "is_enabled": True,
    })

def create_standard_from_seed(self) -> dict:
    seed = self.create_quick_task("standard_seed")
    self.delete(f"{CUSTOM_REPORTING}/{seed['id']}/")
    return self.post(f"{CUSTOM_REPORTING}/", {
        "name": f"{self.ledger.run_id}_standard_task",
        "team": [self.org_id],
        "config": {"mode": "standard", "model_id": seed["config"]["model_id"], "identity_keys": ["inst_name"], "cleanup_strategy": "none"},
        "is_enabled": True,
    })
```

- [ ] **Step 4: 实现精准 cleanup，禁止全表删除**

```python
def cleanup(self) -> None:
    for resource in self.ledger.cleanup_plan():
        handler = self.cleanup_handlers[resource.kind]
        handler(resource.identifier)
    residuals = self.scan_residuals(self.ledger.run_id)
    if residuals:
        raise CleanupIncompleteError(self.ledger.path, residuals)
```

- [ ] **Step 5: 验证 GREEN 并提交**

Run: `cd server && /Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/server/.venv/bin/pytest -q -o addopts='' validation/custom_reporting/tests/test_http_runner.py validation/custom_reporting/tests/test_ledger.py`

Expected: PASS；断言无 `.objects.all().delete()`、无非 run_id 删除、无 Cookie/Token 输出。

```bash
git add server/validation/custom_reporting/http_runner.py server/validation/custom_reporting/tests/test_http_runner.py
git commit -m "test(cmdb): 增加安全自定义上报真实E2E驱动"
```

### Task 9: 执行标准/快速模式真实 HTTP + FalkorDB E2E

**Files:**
- Modify: `docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/01-test-matrix.md`
- Modify: `docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/02-findings.md`
- Create: `docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/e2e-ledger.json`

**Interfaces:**
- Consumes: Task 8 驱动器、允许写入的开发/测试地址与登录会话 Cookie。
- Produces: 标准/快速正向、关系 pending/backfill、Token 生命周期、清理和残留证据。

- [ ] **Step 1: 运行 dry-run**

Run: `cd server && CRV_BASE_URL=http://127.0.0.1:8011 CRV_ALLOWED_HOSTS=127.0.0.1 CRV_ORG_ID=1 CRV_SESSION_COOKIE="$CRV_SESSION_COOKIE" /Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/server/.venv/bin/python -m validation.custom_reporting.http_runner --dry-run`

Expected: 只输出带唯一 run_id 的资源/请求/清理计划，`requests_sent=0`，不打印 Cookie 或 Token。

- [ ] **Step 2: 执行真实正向流程**

Run: `cd server && CRV_ALLOW_WRITE=1 CRV_BASE_URL=http://127.0.0.1:8011 CRV_ALLOWED_HOSTS=127.0.0.1 CRV_ORG_ID=1 CRV_SESSION_COOKIE="$CRV_SESSION_COOKIE" /Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/server/.venv/bin/python -m validation.custom_reporting.http_runner --execute --ledger ../docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/e2e-ledger.json`

Expected: standard/quick 创建、更新、字段登记、图节点、立即关系、pending/backfill、Token 轮换/作废全部逐项输出 PASS；任一失败立即进入精准 cleanup。

- [ ] **Step 3: 核对真实 FalkorDB 与关系库**

Run: `cd server && /Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/server/.venv/bin/python -m validation.custom_reporting.http_runner --verify-ledger ../docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/e2e-ledger.json`

Expected: 运行中账本的每个实例/边/Batch 可查且 owner、organization、collect_task 正确。

- [ ] **Step 4: 精准清理与残留扫描**

Run: `cd server && CRV_ALLOW_WRITE=1 /Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/server/.venv/bin/python -m validation.custom_reporting.http_runner --cleanup-ledger ../docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/e2e-ledger.json`

Expected: 仅删除账本资源；随后模型、任务、实例、边、pending、review、credential、batch 残留均为 0。

- [ ] **Step 5: 提交真实 E2E 证据**

```bash
git add docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16
git commit -m "test(cmdb): 记录自定义上报真实E2E结果"
```

### Task 10: 完成架构、代码质量和测试质量审查

**Files:**
- Modify: `docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/02-findings.md`
- Create: `docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/03-architecture-quality.md`

**Interfaces:**
- Consumes: Tasks 1–9 的调用链、测试、覆盖率和真实 E2E 证据。
- Produces: P0–P3 去重 Findings 与架构评分。

- [ ] **Step 1: 按调用链审查职责与依赖方向**

检查 `views/custom_reporting.py -> provider.py -> task/ingest/merge/relation/cleanup -> Management/GraphClient/ORM`，为每个单元记录职责、输入、输出、依赖和失败语义。

- [ ] **Step 2: 审查授权、Schema、状态机、跨存储和资源预算**

每个 Finding 使用固定结构：

```markdown
### Finding CRV-Fxx：标题
- Severity: P0|P1|P2|P3
- Location: `path:line`
- Trigger: 可重复输入或步骤
- Evidence: 测试名、实际结果、代码路径
- Impact: 数据/权限/可用性影响
- Root Cause: 单一根因
- Why Existing Tests Missed It: 现有断言缺口
- Required Tests: 修复验收行为
- Minimal Safe Fix Boundary: 最小修复范围，不在本轮实现
```

- [ ] **Step 3: 评价测试质量**

逐项评价覆盖率、mock 边界、行为断言、RED 证明、真实服务覆盖、夹具真实性和未收集风险；不得仅给百分比。

- [ ] **Step 4: 提交审查**

```bash
git add docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/02-findings.md docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/03-architecture-quality.md
git commit -m "docs(cmdb): 完成自定义上报架构质量审查"
```

### Task 11: 最终门禁、发布建议与工作树核验

**Files:**
- Create: `docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/04-release-recommendation.md`
- Modify: `docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/01-test-matrix.md`

**Interfaces:**
- Produces: 可审计的最终结论、未关闭风险和后续 TDD 修复清单。

- [ ] **Step 1: 重跑验证工具自身测试**

Run: `cd server && /Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/server/.venv/bin/pytest -q -o addopts='' validation/custom_reporting/tests`

Expected: 验证工具测试全绿；故意复现生产缺陷的测试必须标记为 `xfail(strict=True, reason="CRV-Fxx")`，禁止以普通失败污染门禁或以非严格 xfail 掩盖意外通过。

- [ ] **Step 2: 重跑现有 81 项 overlay 基线与覆盖率**

Run: `cd server && MINIO_ENDPOINT=localhost:9000 MINIO_ACCESS_KEY=test MINIO_SECRET_KEY=test MINIO_USE_HTTPS=false DB_ENGINE=sqlite DB_NAME=:memory: INSTALL_APPS=system_mgmt,node_mgmt,cmdb,cmdb_enterprise /Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/server/.venv/bin/pytest -q -o addopts='' --nomigrations apps/cmdb_enterprise/tests/test_custom_reporting_*.py apps/cmdb_enterprise/tests/bdd/test_custom_reporting_bdd.py --cov=apps.cmdb_enterprise.custom_reporting --cov=apps.cmdb.custom_reporting --cov=apps.cmdb.views.custom_reporting --cov=apps.cmdb.serializers.custom_reporting --cov-report=term-missing`

Expected: 无新增回归；记录实际通过数与覆盖率。

- [ ] **Step 3: 验证文档与工作树**

Run: `! rg -n 'T[B]D|T[O]DO|FIX[M]E|X[X]X|待[补]|占[位]' docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16`

Run: `git diff --check`

Run: `git status --short`

Expected: 未完成标记扫描无输出，`git diff --check` 通过，只有本计划明确的待提交文件。

- [ ] **Step 4: 写发布建议**

`04-release-recommendation.md` 必须分别给出：锁定 gitlink 结论、固定运行态 overlay 结论、标准模式结论、快速模式结论、真实 E2E 清理结果、P0/P1 数量、覆盖率门禁和 `Pass/Conditional/Block`。任一未关闭 P0/P1 时写 `Block`。

- [ ] **Step 5: 最终提交**

```bash
git add docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16
git commit -m "docs(cmdb): 交付自定义上报生产级验证结论"
```

## 计划自审映射

- 双基线与 SHA-256：Task 1。
- 安全账本、dry-run、精准清理：Tasks 2、8、9。
- 现有测试与覆盖率：Tasks 3、11。
- 控制面、组织和 Token：Task 4。
- 标准/快速 Schema 与身份：Task 5。
- owner、partial、关系、清理和恢复：Task 6。
- Celery/Beat 与资源预算：Task 7。
- 真实 HTTP + FalkorDB：Tasks 8、9。
- 架构、代码和测试质量：Task 10。
- 最终门禁与发布建议：Task 11。
