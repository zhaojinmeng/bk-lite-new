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
