# 验证报告

> 验证日期：2026-09-17（Asia/Shanghai）  
> Python 迁移基线：Spring AI Alibaba DataAgent `main`
> `3fb7852f71b2f6bfda3de392f79207df7294199f`

## 1. 静态完整性

| 项目 | 结果 |
|---|---|
| 上游 Git 完整性 | 735 个受控文件、0 缺失、无 LFS/submodule/sparse 问题、fsck 通过 |
| Java 生产文件映射 | 303/303，路径唯一且所有 Python 目标存在 |
| API 契约 | 98/98 上游操作 + 3 个兼容/增强操作，集合精确相等 |
| StateGraph 契约 | 16/16 节点、11/11 分发器、39/39 状态键 |
| Prompt | 17/17，SHA-256 byte lock，通过变量渲染测试 |
| 管理表 | 13/13 上游业务表 + 2 Python runtime 表 |
| 数据源类型 | MySQL、PostgreSQL、H2、Dameng、SQL Server、Oracle、Hive |
| Python lint | `ruff check src tests tools` 通过 |

## 2. 自动测试与构建

| 命令 | 结果 |
|---|---|
| `python -m pytest` | 96 passed |
| `coverage run -m pytest` / `coverage report` | 总覆盖率 71%，含 branch coverage |
| `pnpm test:unit` | 7 files、21 tests 全部通过 |
| `pnpm build` | Nuxt 4.3.0 / Nitro 2.13.1 production build 通过 |
| `python -m build --no-isolation` | sdist 与 pure-Python wheel 均成功 |
| `docker compose config --quiet` | 通过 |

生成包：

- `spring_ai_alibaba_dataagent_python-0.1.0-py3-none-any.whl`
- `spring_ai_alibaba_dataagent_python-0.1.0.tar.gz`

隔离构建在本机第一次失败，是 Windows 中文区域的 pip 子进程错误输出被 `build` 按 UTF-8 解码，
同时沙箱网络阻止临时环境下载；已安装声明的 hatchling 后用 `--no-isolation` 完成可重复构建。这不是
项目源代码编译错误。

## 3. Docker Compose HTTP/SSE/HITL 真实运行

`tools/e2e_smoke.py` 对 Compose 发布的真实 Uvicorn、持久化管理 MySQL 和容器内分析 MySQL 完成：

1. `/echo/ok` 与 management health；
2. Agent 创建；
3. MySQL 分析数据源创建和密码安全响应；
4. Agent 关联、表选择、Schema 向量化（1 表、4 列）；
5. SSE 执行至 HumanFeedback 中断并持久化 checkpoint；
6. 相同 `threadId` 接受计划并恢复；
7. SQL 执行、最终报告、`complete` 帧；
8. `nl2sqlOnly` 返回 `SELECT * FROM users LIMIT 100`。

实测恢复流为 12 个事件，所有事件 `error=false`；最终 `/health` 为 `ready`。测试期间临时使用
确定性 mock 模型，结束后同一脚本的 `finally` 已把 Compose 恢复到 `modelMode=database`。本地烟测
客户端显式 `trust_env=false`，避免工作站代理错误截获 `127.0.0.1`。

## 4. Docker 数据库真实运行

四个基础设施容器均通过 Docker healthcheck：

| 容器 | 验证结果 |
|---|---|
| `dataagent-python-mysql` | `saa_data_agent` 已由 SQLAlchemy 建立 15 张表 |
| `dataagent-python-redis` | Redis 7.4，`PONG`，AOF 已启用且最后写状态 `ok` |
| `dataagent-python-mysql-datasource` | `product_db`：5 users、10 products、10 orders、19 order_items，共 6 表 |
| `dataagent-python-postgres-datasource` | 13 年人口、13 省份、2 视图，共 7 个表/视图 |

`tools/docker_datasource_smoke.py` 再从 FastAPI Connector API 验证：

- MySQL 连接成功，发现 6 表，`users` 4 列；
- PostgreSQL 连接成功，发现 7 个表/视图，`population_total` 8 列；
- API 返回密码掩码且不返回 `connectionUrl`；测试对象在 finally 中删除。

MySQL 初始化 SQL 现显式执行 `SET NAMES utf8mb4`；当前持久卷的表/列注释、10 个商品名和 5 个
分类名已用幂等脚本修复，并通过 UTF-8 client 回读为正确中文。

Redis 已真实部署到宿主 `16379` / 容器 `6379`，使用命名卷
`dataagent-python_dataagent-redis`。`tools/redis_memory_smoke.py` 通过真实 FastAPI 验证消息首次未命中、
第二次命中、写后失效和会话删除后 404。容器内 `redis_graph_memory_smoke.py` 验证多轮上下文与 HITL
checkpoint 各产生真实命中，数据库/缓存删除后均不可恢复，结果为 `hits=2`、`errors=0`、
`pendingInvalidations=0`。

另做受控故障测试：先把消息同时写入管理 MySQL 并热读，再临时停止 Redis。后端 `/health` 正确变为
`degraded`、`redis.ready=false`，相同消息仍从 MySQL 返回；恢复 Redis 后总体状态自动回到 `ready`，
待处理失效为 0。该测试结束后测试 Agent/会话已删除，Redis 容器已恢复 healthy。

## 5. Docker Python Sandbox 真实运行

默认 AgentScope 镜像 digest：

```text
sha256:0923ab99a5a7367b314edd7c1c31d5639023913a8a87b1865113d5cb4930440a
```

`tools/sandbox_smoke.py` 在真实任务容器中执行并解析：

```json
{"sum": 6, "runtime": "sandbox"}
```

结果 `success=true`、stderr 为空；执行后查询 `dataagent.sandbox=python` 标签，残留容器数为 0。
单元测试另外验证只读根文件系统、`cap_drop=ALL`、`no-new-privileges`、非 privileged、CPU、内存、
PID、超时与强制清理参数。

## 6. MCP 真实运行

`tools/mcp_smoke.py` 通过 MCP Python SDK 2.x Streamable HTTP client 完成 initialize、list_tools 与
call_tool：

- 工具集合精确为 `listAgentsToolCallback`、`nl2SqlToolCallback`；
- Agent 列表调用成功；
- NL2SQL 调用返回 `SELECT * FROM sales LIMIT 100`。

这不是直接调用 Python 函数，而是实际 HTTP MCP transport。

最终正式模型与本地 BGE-M3 配置下再次通过同一 MCP transport，Agent 4 返回
`SELECT id, username FROM users`。

## 7. 模型与 RAG 真实运行

- 使用独立私有配置做过 OpenAI-compatible live smoke，HTTP 200；真实密钥未写入
  项目、日志、文档或交付包。
- `tools/configure_chat_from_env.py` 已把同一配置安全写入本机管理 MySQL：模型
  `qwen3.8-flash` 为 active，连接测试成功，API 只回传掩码；模型就绪检查为
  `chatModelReady=true`、`embeddingModelReady=true`、`ready=true`。
- 最终 Compose GPU 镜像为
  `sha256:d1dc9fa36123466b00d93f50e6704c8bf44335b2690a38eb2d3779e6b8f176a7`，
  大小 `3,539,848,281` bytes。
- 正在运行的正式模式后端容器内实测 PyTorch `2.6.0+cu124`、CUDA runtime `12.4`、
  `cudaAvailable=true`、设备 `NVIDIA GeForce RTX 4060 Laptop GPU`。
- 同一容器使用 BGE-M3 FP16 编码中文查询，得到 1024 维 dense 与 sparse non-zero 3。
- 完整 BGE-M3 缓存导入到 `dataagent-python_dataagent-data` 命名卷，并在目标卷补全 snapshot
  权重链接。模型目录实测约 2.2 GB；模型缓存不随源码发布。
- mock embedding 升级为中英文标识符 + 中文二元词特征，并把算法身份升级到 v2；基本 Schema
  查询相似度由 0.129 提升到 0.434，高于生产默认 0.2 阈值，旧 v1 向量不会混入新空间。
- 正式配置下 `tools/live_stack_smoke.py` 在后端服务进程内用 GPU BGE-M3 重建 1 表/4 列，
  经真实 CHAT 模型完成 14 个 SSE 事件并生成
  `SELECT id, username FROM users`，流以 `complete` 结束。
- `tools/verify_context_runtime.py` 用真实 `qwen3.8-flash` 验证上下文治理：原输入本地保守估算
  `300068` Token，发送前压缩到 `28144`；请求 2.188 秒完成，只产生 1 个流式片段，且
  `CONTEXT_OK` 精确出现 1 次。容器运行配置为总窗口 `32768`、会话历史预算 `4096`、溢出重试
  `2` 次；Token 估算完全离线，不在首次请求下载词表。
- Simple 向量库验证了持久化、元数据隔离、embedding space 隔离、跨实例重载和跨进程写锁。
- Milvus native dense+sparse、RRF、动态 ID filter 与 dense 必需/keyword 可降级语义有自动测试。

## 8. 交付包回放

- 完整源码 ZIP 共 254 个文件：76 个 Python 文件、139 个前端文件；必需文件缺失 0，排除目录/
  敏感 `.env` 命中 0，只保留 `.env.example`。
- 从 ZIP 解压到全新目录后，强制 `PYTHONPATH` 指向解压的 `src`，96 项测试再次全部通过；
  导入路径确认来自解压目录，解压包内 `docker compose config --quiet` 通过。
- wheel 原始归档 65 个条目，包含 17 个 Prompt、Redis memory 模块和
  `semantic_model_template.xlsx`；从 wheel 解压
  后可创建 FastAPI 应用。
- sdist 共 85 个条目，未包含 `.env`、虚拟环境、前端依赖或缓存。
- 对当时私有配置中的已知密钥和 252 个交付文本文件做只比较、不回显的安全扫描，命中 0；
  OpenAI/GitHub/Google/private-key 通用高风险格式命中 0。

## 9. 仍需目标环境验收的集成

以下没有在本机伪造“通过”：

- Elasticsearch/Milvus 真实服务器；
- OSS、Langfuse、完整 Tika 格式矩阵；
- H2/Dameng jar、SQL Server ODBC、Oracle、Hive 企业服务；
- 生产反向代理、TLS、管理员身份系统与压测。

对应代码和契约测试已存在，但上线前必须用目标版本、驱动、网络和凭据执行集成测试。
