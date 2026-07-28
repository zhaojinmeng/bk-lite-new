# CMDB 采集链路真实基础设施 Smoke

本目录只提供 Task 9 业务 smoke 所需的隔离基础设施和安全运行器。普通
pytest/CI 默认不启动 Docker；只有显式设置 `CMDB_COLLECTION_SMOKE=1` 才能构造运行配置。

## 环境要求

- Docker Engine 与 Docker Compose v2；
- 可用内存至少 1.5 GiB；
- 镜像架构支持当前宿主机；
- 测试只连接回环随机端口或 Compose 内部服务名，不接受外部 VM、NATS 或 FalkorDB 地址。

镜像固定为明确版本，禁止 `latest`：

- `nats:2.10.26-alpine`
- `telegraf:1.34.0-alpine`
- `victoriametrics/victoria-metrics:v1.115.0`
- `falkordb/falkordb:v4.4.0`

升级时逐个修改版本，并先执行 `docker compose pull`、`docker compose config`
和本目录 safety tests，再运行 Task 9 的完整业务 smoke。若供应链要求 digest，
应在受信任 CI 中拉取多架构 manifest 后，将对应 digest 补入此文件和 Compose，
不能猜测 digest。

## 本地安全合同

```bash
cd server
MINIO_ENDPOINT=localhost:9000 \
MINIO_ACCESS_KEY=test \
MINIO_SECRET_KEY=test \
MINIO_USE_HTTPS=false \
INSTALL_APPS=system_mgmt,node_mgmt,cmdb \
uv run pytest -q -o addopts='' \
  apps/cmdb/tests/smoke/collection_chain/test_safety_pure.py \
  apps/cmdb/tests/smoke/collection_chain/test_safety_limits_pure.py
```

Safety tests 完全离线，不要求 Docker daemon。检查 Compose 语法时可运行：

```bash
docker compose \
  --file server/apps/cmdb/tests/smoke/collection_chain/compose.yaml \
  config --quiet
```

真实 smoke 必须显式启用，并为单次运行生成唯一 `run_id` 和对应 Compose project：

```bash
CMDB_COLLECTION_SMOKE=1 \
CMDB_SMOKE_RUN_ID=cmdb-a1b2c3d4 \
uv run pytest -q -o addopts='' \
  apps/cmdb/tests/smoke/collection_chain/test_collection_chain.py
```

运行器使用随机宿主端口、条件健康检查和有界轮询，不使用裸 `sleep`。无论启动、
健康检查还是业务断言失败，都会在 `finally` 中先保存 Compose 日志，再仅对当前
project 执行 `down --remove-orphans`。Compose 使用 tmpfs，不创建持久 named volume；
禁止 `down -v`、全局 `prune` 或宽目录递归删除。

基础设施状态通过后，运行器还会先执行真实协议 canary：向 NATS
`metrics.<run_id>` 发布唯一 Influx 行，并轮询 VictoriaMetrics 查询接口确认该
`run_id` 可见，之后才执行业务 workload。workload、canary、外部命令、日志读取、
资源 remover 和残留确认都有独立硬截止时间；日志默认最多保留 1 MiB。可用以下
环境变量收紧边界：

- `CMDB_SMOKE_WORKLOAD_TIMEOUT`
- `CMDB_SMOKE_CANARY_TIMEOUT`
- `CMDB_SMOKE_CLEANUP_TIMEOUT`
- `CMDB_SMOKE_COMMAND_TIMEOUT`
- `CMDB_SMOKE_LOG_TIMEOUT`
- `CMDB_SMOKE_LOG_MAX_BYTES`
- `CMDB_SMOKE_LEDGER_MAX_RESOURCES`

这些变量只能收紧或在硬上限内调整：启动 300s、workload 600s、canary/cleanup
各 120s、单命令 60s、日志 30s、轮询间隔 5s、日志 10 MiB、ledger 10000
条。`NaN`、`Inf` 和超上限值会在 Docker 启动前拒绝。

清理完成后会继续有界确认当前 project 的容器列表为空，且精确名称
`${COMPOSE_PROJECT_NAME}_default` 的网络不存在。若业务与清理同时失败，运行器用
`ExceptionGroup` 同时保留两类异常，不能以清理异常覆盖业务根因。

VictoriaMetrics 使用 `-influxSkipSingleField`，因此 canary 的单字段 `value`
保持原 measurement 名；查询不会错误寻找自动追加的 `_value` 后缀。

## Task 9 接入点

Task 9 在 `CollectionChainSmokeRunner.run(workload)` 的 workload 中接入七类代表对象：
`host`、`mysql`、`influxdb`、`nginx`、`qcloud`、`vmware`、`network`。workload
通过 `SmokeContext` 获取 `run_id` 和 ledger：

- 所有写入的指标、CMDB 实体和图关联都带 `run_id`；
- 创建后立即登记精确资源标识；
- 图清理由 Task 9 提供资源级 remover，且必须验证资源 owner 等于当前 `run_id`；
- 容器端口通过 `docker compose port` 查询，不写固定宿主端口；
- 用有界查询轮询等待 VM/图库可见，不允许依赖固定睡眠。

Task 9 完成前，本目录不声称七个业务对象已通过真实 smoke。
