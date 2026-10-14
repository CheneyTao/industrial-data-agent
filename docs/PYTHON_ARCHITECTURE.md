# Python 等价架构

> 本文保留迁移基线说明。2026-09-18 起的自适应交付物、12 个 MCP 工具、7 种图型与持久化证据链，以 [全项目流程图](PROJECT_FLOW.md) 为准。

## 1. 设计目标

本实现以 Spring AI Alibaba DataAgent `main` 的
`3fb7852f71b2f6bfda3de392f79207df7294199f` 为冻结基线。迁移单位是行为语义，而不是 Java
类名：98 个上游 API、16 个节点、11 个条件分发器、39 个状态键、17 个 Prompt、13 张业务表、
MCP 两个 Tool、SSE、HITL、RAG、Sandbox 与 Nuxt 交互均有 Python 对应实现。

Python 运行时另外增加 `graph_checkpoint` 和 `conversation_turn` 两张表，分别承载可恢复中断与
多轮规划上下文，因此管理库共 15 张表。

## 2. 运行拓扑

```text
Nuxt 4 / Vue 3 / Pinia
        │ REST + fetch SSE（支持 X-API-Key）
        ▼
FastAPI
  ├─ 领域 API：Agent、数据源、知识、语义模型、模型、Prompt、会话
  ├─ GraphEngine：显式状态机、checkpoint、interrupt/resume、stop
  ├─ RedisMemoryCache：会话/上下文/checkpoint 读穿缓存、锁与故障回退
  ├─ LLMClient：数据库热配置的 OpenAI-compatible Chat/Embedding
  ├─ RetrievalService：Simple JSON / Elasticsearch / Milvus
  ├─ ConnectorRegistry：7 类分析数据库
  ├─ DockerPythonSandbox：每任务隔离容器
  ├─ FileStorage：本地或 OSS
  └─ OpenTelemetry/Langfuse OTLP
        │
        ├─ 管理库：SQLite（本机默认）或 MySQL（Compose 默认）
        ├─ Redis：低时延热层；管理库仍是持久化事实源
        ├─ 分析数据源：MySQL/PostgreSQL/H2/Dameng/SQL Server/Oracle/Hive
        ├─ 向量库：JSON / Elasticsearch / Milvus
        └─ Docker Engine

独立 MCP Python SDK 2.x 进程
  ├─ listAgentsToolCallback
  └─ nl2SqlToolCallback
```

WSL 不是必需组件。Windows、WSL 或 Linux 都可以运行 FastAPI；Docker 只对 Python 代码沙箱
是强制依赖。Compose 下 FastAPI 与 MCP 共享向量 JSON 时使用 OS 文件锁和原子替换，避免跨进程
更新丢失。

## 3. 代码模块

| Python 模块 | 职责 |
|---|---|
| `app.py` | FastAPI 生命周期、路由、CORS、异常、健康检查、依赖装配 |
| `api/` | 101 个操作：98 个上游操作、2 个前端兼容端点、1 个增强健康端点 |
| `models.py` / `database.py` | SQLAlchemy 2 async 模型、事务与 Schema 初始化 |
| `schemas.py` | Pydantic 2 API DTO、规划结构、SSE 输出模型 |
| `workflow/state.py` | 原状态常量、16 节点顺序、11 分发器名称、初始状态 |
| `workflow/nodes.py` | 节点实现、路由、重试、SQL/Python/Report 工具循环 |
| `workflow/engine.py` | SSE 驱动、HITL checkpoint/resume、取消、多轮历史 |
| `memory_cache.py` | Redis 读穿缓存、同键分布式锁、事务后失效、熔断与 MySQL 回退 |
| `llm.py` | Chat/Embedding 热配置、结构化 JSON 修复、密钥与代理消费 |
| `embedding_rebuild.py` | Embedding 模型切换前候选空间全量重建与原子切换 |
| `vectorstore.py` | 三种向量后端、元数据隔离、dense/sparse、RRF 混合检索 |
| `schema_index.py` | Schema 文档生成、按 Agent/数据源建索引 |
| `documents.py` | 文件存储、限流读取、抽取、五类切分、知识向量生命周期 |
| `connectors.py` | 连接 URL、元数据、DDL、只读 SQL 验证与结果限制 |
| `sandbox.py` | Docker 资源限制、PEP 723 依赖、协议化 stdout/stderr、清理 |
| `mcp_server.py` | MCP SDK 2.x Streamable HTTP、SSE、stdio |
| `reports.py` | Markdown/ECharts HTML 报告和数据驱动图表 |
| `maintenance.py` | 中断任务恢复、失败重试、软删除资源清理 |
| `telemetry.py` | 节点级 span、token、输入输出截断与 Langfuse OTLP |

逐 Java 文件映射见 `JAVA_TO_PYTHON_FILE_MAP.csv`，覆盖 303/303 个生产 Java 文件。

## 4. 16 节点状态流

主链为：

```text
IntentRecognition
  → EvidenceRecall
  → QueryEnhance
  → SchemaRecall
  → TableRelation
  → FeasibilityAssessment
  → Planner
  → PlanExecutor
       ├─ HumanFeedback → PlanExecutor / Planner
       ├─ SqlGenerate → SemanticConsistency → SqlExecute → PlanExecutor
       ├─ PythonGenerate → PythonExecute → PythonAnalyze → PlanExecutor
       └─ ReportGenerator → END
```

关键语义：

- 普通聊天可从 Intent 直接结束；不可分析或需澄清可从可行性节点结束。
- Schema Recall 先按 Agent、数据源、表选择和 embedding space 隔离，再做召回。
- Table Relation 合并物理外键、逻辑外键、语义模型与缺表补全，最多重试三次。
- 所有 Chat 调用先经过统一 Token 预算门：保留系统安全指令，超长任务数据采用首尾保留压缩；若
  OpenAI-compatible 服务仍返回上下文溢出，则按配置比例缩小输入并在产生任何输出前重试。
- 多轮上下文同时受最近轮数和 `DATAAGENT_CONVERSATION_CONTEXT_MAX_TOKENS` 约束，优先保留最新
  完整轮次。完整聊天仍持久化在管理数据库，裁剪只影响本次发送给模型的上下文。
- 会话列表/消息、已裁剪上下文和 HITL checkpoint 可从 Redis 热读；写操作以管理库 commit 为
  成功边界，再持同键锁失效/写入 Redis，避免把缓存变成第二事实源。
- Planner 只允许 SQL、Python、Report 三类工具；PlanExecutor 校验连续步号和参数。
- SQL 经 AST 只读校验、语义一致性检查、执行失败反馈与最多 10 次再生成。
- Python 最多生成/执行 5 次，失败后进入分析 fallback，不让报告链路整体崩溃。
- 开启人工审核时，HumanFeedback 执行前持久化完整状态；恢复必须携带同一 `threadId`。
- `nl2sqlOnly` 在得到 SQL 后结束，不执行查询和报告。

## 5. RAG 与 Embedding

上下文由四条链汇合：Schema 文档、Semantic Model、Business Knowledge、Agent Knowledge。

- Simple 后端是原子 JSON 文件，适合单机与演示；HTTP/MCP 多进程写入有跨进程锁。
- Elasticsearch 使用 dense vector 与 metadata filter。
- Milvus 支持 dense+sparse 混合检索，RRF 默认 `k=60`，候选数至少
  `max(topK*16, 48)`。
- 本地 `BAAI/bge-m3` 同时输出 1024 维 dense 与 lexical sparse，可使用 Hugging Face ID
  或一个完整本地模型目录。
- 每个向量都带 `embeddingSpaceId`。切换数据库管理的 Embedding 模型时，先在候选空间重建
  当前有效 Schema/知识；全部成功后才激活新模型，旧空间保留用于回滚。
- 检索时 dense 分支是必需结果；混合 sparse 分支超时或失败可降级为 dense，不能伪造成功。

## 6. 数据与安全边界

- 数据源密码、模型 API Key、代理密码只在写入时接收；列表/详情始终掩码或移除连接 URL。
- Agent API Key 使用 bcrypt 存储，仅生成/重置当次返回明文；SSE 同时支持 `X-API-Key`
  与 Bearer。
- SQL 使用 `sqlglot` 拒绝多语句和写入语句，并设置超时与最大行数。
- 上传按流读取，知识/Excel 默认 10 MiB，头像默认 2 MiB；本地路径限制在上传根目录。
- 生成的 Python 从不在 FastAPI 宿主进程执行；容器丢弃 capabilities、只读根文件系统、禁止提权、
  限制 CPU、内存、PID、输入输出和运行时间。
- 报告图表配置只允许 JSON，不执行模型生成的 JavaScript；生成 HTML 对渲染结果做标签、事件属性
  与危险 URL 清理。

## 7. 配置优先级

所有配置由 Pydantic Settings 读取：进程环境变量优先于项目 `.env`，再回退到代码默认值。
变量统一使用 `DATAAGENT_` 前缀。模型密钥可以从用户已有 `.env` 手工复制，但仓库与交付包永远
不包含真实 `.env`。

Compose 明确覆盖管理库、Redis、容器内数据路径、Docker socket 和服务地址；这使同一代码既能
本机 SQLite 零配置运行，也能在 Docker 中使用持久化 MySQL + Redis 热层。
