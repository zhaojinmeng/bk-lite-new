# CMDB 自定义上报验证双基线

## 基线 A：锁定交付基线

- 主仓验证起点：`b1cdfc8d5b1a5ff3d9ed796367501578aa56fbbe`。
- Enterprise gitlink：`1e9c3d2d0ea6b95fa8d57aa326b511187d48350e`。
- `enterprise@1e9c3d2` 的 `server/apps/cmdb_enterprise/custom_reporting/` 仅包含
  `__init__.py` 与 `models.py`，且该提交的 `server/apps/cmdb_enterprise/urls.py`
  为 `urlpatterns = []`。
- 因此该锁定交付基线不包含当前运行态 overlay 中的自定义上报
  `provider.py`、`services/`、`tasks.py`、URL 注册及对应行为测试，不能用于宣称
  当前运行态功能已由 `enterprise@1e9c3d2` 交付。

## 基线 B：固定运行态 overlay 制品

- 来源：主工作区 `server/apps/cmdb_enterprise`。
- 隔离验证目标：当前 worktree `server/apps/cmdb_enterprise`；该目录受主仓
  `.gitignore` 保护，仅作为后续真实验证的运行态制品，不写入生产环境。
- 清单：[`overlay-sha256.txt`](overlay-sha256.txt)，共 78 个文件。
- 排除项：所有 `__pycache__/` 内容和 `*.pyc` 文件。
- 聚合 SHA-256：
  `f7ece8164af8fdf3b9ef96e26438bf44991b7e426f01de1b6830faa456c02e42`。
- 复制完成后重新构建目标清单，源与目标 78 个条目逐项相同，聚合 SHA-256
  相同；目标未发现 `__pycache__` 或 `*.pyc`。

运行态 overlay 仅用于复现当前运行行为。它与锁定 Enterprise gitlink 是两个
独立基线，不得将 overlay 的验证结果归因或冒充为 `enterprise@1e9c3d2` 的交付结果。

## 固定命令

```bash
cd server
/Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/server/.venv/bin/python \
  -m validation.custom_reporting.artifact \
  --source /Users/windyzhao/Documents/Canway/weops_X/cmdb/bk-lite/server/apps/cmdb_enterprise \
  --destination apps/cmdb_enterprise \
  --manifest ../docs/reviews/cmdb-custom-reporting-real-validation-2026-07-16/overlay-sha256.txt
```

该命令输出的源与目标聚合 SHA-256 均应为上述固定值；任何差异均表示运行态
制品已漂移，需要重新确认来源，不能静默沿用旧基线。

## Task 3 真实测试基线（2026-07-16）

### 执行边界与制品复验

- 执行工作树 HEAD：`2162d7658`。
- 执行时间：2026-07-16 08:59—09:02（Asia/Shanghai）。
- 数据库：SQLite 内存库（`DB_ENGINE=sqlite`、`DB_NAME=:memory:`），并使用
  `--nomigrations`；未连接生产或外部数据库。
- MinIO 使用本地地址和固定测试值；未使用真实任务、模型、令牌或生产凭据，
  也未发起真实 HTTP 写入。
- 测试前重新检查 overlay：清单正文 78 项，目标实际文件也是 78 项，路径集合
  无差异，逐文件 `shasum -a 256 -c` 全部成功；清单正文聚合 SHA-256 为
  `f7ece8164af8fdf3b9ef96e26438bf44991b7e426f01de1b6830faa456c02e42`。

### Community 无 overlay

按计划中的原始命令执行，最小应用集为
`system_mgmt,node_mgmt,cmdb`。真实结果为：

```text
6 passed in 0.15s
exit code: 0
```

该结果只证明 Community 默认扩展/no-op 与模型委托边界；没有加载
`cmdb_enterprise`，不得用于证明商业实现行为。

### 运行态 overlay

计划中的原始命令（应用集
`system_mgmt,node_mgmt,cmdb,cmdb_enterprise`）真实首跑结果为：

```text
3 failed, 78 passed in 3.15s
TOTAL 859 statements, 133 missed, 85%
exit code: 1
```

三个失败均为 `test_custom_reporting_views.py` 的 View 用例。根因是测试环境合同
缺项：根 URL 导入 `apps.system_mgmt` 时，未开启的 Celery 配置没有把
`django_celery_beat` 注册进 `INSTALLED_APPS`；仅补
`ENABLE_CELERY=true` 后该错误消失，随后请求中间件明确暴露空
`SECRET_KEY`。这属于环境问题，不是已确认的产品缺陷或测试断言缺陷。
异常及最小验证过程记录在 projectmem #0288；未修改生产逻辑，也未用 xfail
掩盖失败。

补充非敏感测试环境值 `ENABLE_CELERY=true`、
`SECRET_KEY=test-secret-key` 后，先复验原三个失败用例：

```text
3 passed in 0.76s
exit code: 0
```

随后按相同测试选择器和覆盖率参数完整复跑：

```text
81 passed in 1.80s
TOTAL 936 statements, 155 missed, 83%
exit code: 0
```

首跑的 85% 是三个 View 用例失败、View/Serializer 未进入完整覆盖统计时得到的
不完整口径，不能解读为优于补充复跑的 83%。完整逐模块覆盖率、missing lines、
命令和风险解释见 [`01-test-matrix.md`](01-test-matrix.md)。

上述 overlay 结果只归属于清单固定的当前运行态制品。锁定的
`enterprise@1e9c3d2` 不具备这些运行态行为，因此无论首跑还是补充复跑结果，
均不得归因于该 gitlink 的交付。
