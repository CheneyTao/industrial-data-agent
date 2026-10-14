# 启动与部署

## 1. 选择运行方式

| 方式 | 管理库 | Python Sandbox | 适合场景 |
|---|---|---|---|
| Windows/WSL 直接运行 | 默认 SQLite，也可连 MySQL | 仍连接 Docker Engine | 学习、调试、PyCharm |
| Docker Compose | 持久化 MySQL | 容器通过 Docker socket 创建任务沙箱 | 一键完整演示 |
| Linux/WSL + 远程服务 | MySQL | 本机或远程 Docker Host | 服务器部署 |

WSL 不是必需的。不要同时在 Windows 和 WSL 用两个进程写同一个 SQLite 文件；需要跨环境或多
实例时使用 MySQL。

原 Java `main` 没有 Redis 业务读写；Python 版按用户的低时延要求增加 Redis 热层，但没有把它
误当作持久化数据库。完整聊天、规划轮次和 HITL checkpoint 先提交 MySQL/SQLite，再更新或失效
Redis；缓存断线时读取自动回退管理库。

## 2. Windows 本机最短启动

要求 Python 3.11+、Node.js 20+、pnpm。完整 Python 分析还要求 Docker Desktop 正常。

```powershell
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[full,dev]"

# 先用确定性模拟模型验证系统，不调用外部 API
$env:DATAAGENT_MODEL_MODE = "mock"
dataagent
```

后端：`http://127.0.0.1:8065`；Swagger：`/docs`；健康检查：`/health`。

另开终端启动前端：

```powershell
Set-Location frontend
pnpm install --frozen-lockfile
pnpm dev
```

浏览器访问 `http://127.0.0.1:3000`。

工业设备 Dashboard 的数据口径、接口、筛选联动与索引建议见
[`DASHBOARD.md`](./DASHBOARD.md)，页面地址为 `http://127.0.0.1:3000/dashboard`。

`mock` 只用于契约/流程演示。真实分析应删除该临时环境变量，在模型管理页各配置并激活一个
CHAT 和 EMBEDDING 模型。

## 3. Docker Compose 完整启动

```powershell
Copy-Item .env.example .env
docker compose up --build -d
docker compose ps
```

默认服务：

| 服务 | 容器端口/宿主端口 | 内容 |
|---|---|---|
| `mysql` | `3306 / 13307` | DataAgent 管理库 `saa_data_agent`，保存模型、数据源、会话、任务与报告管理状态 |
| `redis` | `6379 / 16379` | 会话、消息、多轮上下文和 checkpoint 热缓存，AOF + 命名卷 |
| `mysql-data` | `3306 / 13306` | 示例分析库 `product_db`，6 张电商表 |
| `postgres-data` | `5432 / 15432` | 示例分析库 `china_population_db`，表、视图、函数与过程 |
| `backend` | `8065 / 8065` | FastAPI、Graph、RAG、Sandbox orchestration |
| `frontend` | `3000 / 3000` | Nuxt 4 Node server |
| `mcp` | `8066 / 8066` | MCP Streamable HTTP |

可选启用 Tika（旧 Office 等广泛文档格式）：

```powershell
docker compose --profile tika up -d tika
```

Compose 首次会拉取 MySQL、Redis、PostgreSQL、Python/Node 与 Sandbox 镜像，时间取决于网络。
管理库、Redis AOF、向量 JSON 和上传分别使用命名卷；`docker compose stop` 不删除数据。只有用户
明确要清空时才运行带 `--volumes` 的删除命令。

### Redis 记忆热层

Compose 中 `DATAAGENT_REDIS_REQUIRED=true`，后端会等待 Redis healthcheck 后启动。缓存范围为：

- Agent 会话列表与聊天消息；
- 已按轮数和 Token 预算整理的多轮规划上下文；
- Human-in-the-loop 可恢复 checkpoint。

写路径始终先提交管理库，再用同一 Redis 分布式键锁更新/失效缓存，避免读回旧值。Redis 操作有
短超时与熔断间隔；运行期故障会回退数据库，`/health` 显示 `degraded`，恢复后重放待处理失效并
回到 `ready`。默认 TTL 分别是聊天 300 秒、上下文 3600 秒、checkpoint 3600 秒，可在 `.env`
调整。密码通过独立 `DATAAGENT_REDIS_PASSWORD` 传入，不拼进 URL，避免特殊字符转义问题。

直接在 Windows/WSL 启动 FastAPI 时，可连接 Compose Redis：

```dotenv
DATAAGENT_REDIS_URL=redis://127.0.0.1:16379/0
DATAAGENT_REDIS_PASSWORD=dataagent
DATAAGENT_REDIS_REQUIRED=false
```

不需要缓存时把 `DATAAGENT_REDIS_URL` 留空；此时缓存层是真正的 no-op，不会建立连接或积累待失效键。

当前 Compose 为 backend 与 MCP 声明 NVIDIA GPU；后端镜像的示例 wheel 是 PyTorch
`2.6.0+cu124`。运行该配置要求 Docker 的 NVIDIA runtime 可用，Windows 上需匹配的 WSL2
backend 与驱动。迁移时将 `DATAAGENT_TORCH_WHEEL_URL` 设为适合目标环境的官方 wheel。
其他 Python 依赖从 `DATAAGENT_PYPI_INDEX_URL` 安装。

发布源码不包含 BGE-M3 权重。`.env.example` 默认 `DATAAGENT_EMBEDDING_MODE=database`，需要
激活 CHAT 和 EMBEDDING 配置。若选择本地 BGE-M3，显式设置 `local_bge_m3` 模式，准备模型
缓存或完整模型目录，并选择 `auto`、`cpu` 或 `cuda` 设备；此时仍需激活 CHAT 配置。未提供
`.env` 时，Compose 内的后备值会选择本地 BGE-M3，首次使用可能需要下载权重。

### 示例数据源在 UI 中怎么填

当后端也运行在 Compose 中：

| 类型 | host | port | database | username/password |
|---|---|---:|---|---|
| MySQL | `mysql-data` | 3306 | `product_db` | `root` / `.env` 中 demo 密码，默认 `root` |
| PostgreSQL | `postgres-data` | 5432 | `china_population_db` | `postgres` / demo 密码，默认 `postgres` |

当 FastAPI 直接运行在 Windows 时，分别使用 `127.0.0.1:13306` 和
`127.0.0.1:15432`。

## 4. 真实模型配置

数据库模型管理支持任意 OpenAI-compatible 服务：

- CHAT：`baseUrl`、`apiKey`、`modelName`、`completionsPath`；
- EMBEDDING：`baseUrl`、`apiKey`、`modelName`、`embeddingsPath`；
- 每模型可设置 HTTP 代理、代理用户名/密码；
- 同一类型只有一个 active。

使用部署者自己的 OpenAI-compatible 服务配置填写，真实凭据保存在仓库外或被忽略的 `.env` 中。前端详情永远只
显示掩码；留着掩码保存不会覆盖真实密钥。

也可用仓库工具把已授权的兼容配置写入管理库并立即测试，工具不会打印真实密钥：

```powershell
python tools/configure_chat_from_env.py --env-file "..\private-model.env"
```

Embedding 配置激活前会为当前有效 Schema 和知识建立候选向量空间。重建失败时旧模型继续 active，
不会出现“模型已切换但向量仍属于旧模型”的混合索引。

## 5. 本地 BGE-M3

```powershell
python -m pip install -e ".[local-rag]"
$env:DATAAGENT_EMBEDDING_MODE = "local_bge_m3"
$env:DATAAGENT_LOCAL_EMBEDDING_MODEL = "BAAI/bge-m3"
$env:DATAAGENT_LOCAL_EMBEDDING_DEVICE = "auto"
dataagent
```

`DATAAGENT_LOCAL_EMBEDDING_MODEL` 也可直接指向一个完整模型目录。目录必须包含配置、tokenizer 和
真实权重；只存在 Hugging Face snapshot 元数据而缺权重链接不算完整缓存。CUDA 可用时 `auto`
选择 GPU，否则选择 CPU；显式写 `cuda` 但 CUDA 不可用会启动失败，不静默降级。

## 6. 向量后端

零配置默认：

```dotenv
DATAAGENT_VECTOR_STORE_TYPE=simple
DATAAGENT_VECTOR_FILE_PATH=./data/vectorstore.json
```

生产建议用外部 Elasticsearch 或 Milvus：

```dotenv
DATAAGENT_VECTOR_STORE_TYPE=elasticsearch
DATAAGENT_ELASTICSEARCH_URL=http://localhost:9200
DATAAGENT_ELASTICSEARCH_INDEX=spring-ai-document-index-v1
```

或：

```dotenv
DATAAGENT_VECTOR_STORE_TYPE=milvus
DATAAGENT_MILVUS_URI=http://localhost:19530
DATAAGENT_MILVUS_COLLECTION=dataagent_documents_v1
DATAAGENT_VECTOR_ENABLE_HYBRID_SEARCH=true
```

切换 embedding 维度时应使用新版本索引/collection，或让模型管理的候选空间重建完成后再清理旧
空间。不要把维度不一致的旧索引强行复用。

## 7. MCP

本机启动：

```powershell
dataagent-mcp
```

默认 endpoint 为 `http://127.0.0.1:8066/mcp`。stdio：

```powershell
$env:DATAAGENT_MCP_TRANSPORT = "stdio"
dataagent-mcp
```

MCP 与 HTTP 后端必须指向同一管理库和向量后端。

## 8. Sandbox 与 Docker Host

安全设计没有宿主机 `exec()` 回退。Docker 不可用时 `/health` 返回 `degraded`，SQL/RAG/管理功能
仍可用，但含 Python 步骤的计划会失败或进入分析 fallback。

Compose 将 `/var/run/docker.sock` 挂给后端。这等同较高的宿主 Docker 权限；生产建议使用隔离的
远程 Docker Host，并把 `DATAAGENT_SANDBOX_IMAGE` 固定为可信 digest。

## 9. 常用验证

```powershell
python -m ruff check src tests tools
python -m pytest
python -m build --no-isolation

Set-Location frontend
pnpm test:unit
pnpm build
```

真实 HTTP/SSE、MCP 与 Docker 数据源：

```powershell
python tools/e2e_smoke.py --base-url http://127.0.0.1:8065 --mysql-host mysql-data --mysql-port 3306
# 把上一条输出的 agentId 填入，并用稳定名称前缀匹配本次唯一 Agent 名
$agentId = 4
python tools/mcp_smoke.py --url http://127.0.0.1:8066/mcp --agent-id $agentId --expected-agent "Compose E2E Agent" --query "列出 users 表中的用户"
python tools/docker_datasource_smoke.py --base-url http://127.0.0.1:8065 --mysql-host mysql-data --mysql-port 3306 --postgres-host postgres-data --postgres-port 5432
python tools/redis_memory_smoke.py --base-url http://127.0.0.1:8065
docker compose cp tools/redis_graph_memory_smoke.py backend:/tmp/redis_graph_memory_smoke.py
docker compose exec -T backend python /tmp/redis_graph_memory_smoke.py
```

这些 smoke 会写测试数据；请对测试环境执行，不要直接指向生产管理库。
