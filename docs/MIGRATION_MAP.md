# Java → Python 迁移映射

## 1. 基线与覆盖口径

- 上游仓库：Spring AI Alibaba DataAgent
- 分支/提交：`main` / `3fb7852f71b2f6bfda3de392f79207df7294199f`
- Java 生产文件：303
- 已建立 Python 对应关系：303/303
- 原 Prompt：17/17，按 SHA-256 锁定并逐字节保留
- 原前端：Nuxt 4 源码保留，并只修改 API 鉴权、表单契约与报告安全断口

完整逐文件清单位于 `JAVA_TO_PYTHON_FILE_MAP.csv`。每一行包含原 `java_path`、Java 包、
Python 目标文件、覆盖状态与迁移说明；自动测试验证 303 条路径唯一且目标文件真实存在。

## 2. 包级映射

| Java 包/层 | Python 对应 | 迁移语义 |
|---|---|---|
| `controller` | `src/dataagent/api/*.py` | Spring MVC/WebFlux 映射转 FastAPI，路径、方法、查询参数和响应外形兼容 |
| `entity`、`mapper` | `models.py`、`database.py` | MyBatis Entity/Mapper 转 SQLAlchemy 2 async model/session |
| `dto`、`vo`、`bo`、`converter` | `schemas.py`、`serialization.py` | Lombok/Bean 转 Pydantic 2 与显式序列化 |
| `workflow.node` | `workflow/nodes.py` | 16 节点输入、输出、重试与路由语义 |
| `workflow.dispatcher` | `workflow/nodes.py`、`workflow/state.py` | 11 个条件分发器转节点返回的显式下一状态 |
| `strategy`、Graph config | `workflow/engine.py`、`models.GraphCheckpoint` | Checkpoint、interrupt/resume、stop、线程状态 |
| Graph/Chat memory hot path | `memory_cache.py`、`workflow/engine.py`、`api/chats.py` | Redis 读穿缓存；MySQL 持久化、锁定失效和故障回退 |
| `connector` | `connectors.py` | 数据源注册、URL、连接测试、表/列/外键、查询执行 |
| `service.vectorstore` | `vectorstore.py`、`schema_index.py` | Simple/ES/Milvus、过滤、批处理、混合检索 |
| `service.knowledge`、`splitter`、`event` | `documents.py`、`maintenance.py`、`api/knowledge.py` | 文件抽取、五类切分、异步索引、重试与资源清理 |
| 模型服务与配置 | `llm.py`、`embedding_rebuild.py`、`api/configuration.py` | OpenAI-compatible 模型、代理、掩码、热切换 |
| SQL 工具/服务 | `connectors.py`、`workflow/nodes.py` | SQL 生成、语义检查、AST 安全、执行和纠错 |
| Sandbox 服务 | `sandbox.py` | Docker SDK、队列、资源限制、PEP 723、结果协议 |
| MCP config/tool | `mcp_server.py` | Spring MCP 转 MCP Python SDK 2.x |
| security | `security.py`、`api/graph.py` | API Key 生成、bcrypt、掩码、SSE 鉴权 |
| file/OSS | `documents.py`、`api/uploads.py` | 本地/OSS 保存删除、URL、路径隔离、大小限制 |
| Prompt/AOP | `prompts/`、`prompts.py`、节点 Prompt 合并 | 原模板保留，用户 Prompt 按类型/Agent/优先级注入 |
| observability | `telemetry.py` | OTel span、Langfuse OTLP、token 与截断 |
| exception/util/constants/enums | `errors.py`、`responses.py`、`workflow/state.py` 等 | 异常码、响应外形、状态键、枚举和解析逻辑 |

## 3. 节点一一对应

| Java StateGraph 节点 | Python 类 | 关键行为 |
|---|---|---|
| IntentRecognitionNode | `IntentRecognitionNode` | 分析意图/普通对话判定 |
| EvidenceRecallNode | `EvidenceRecallNode` | 业务知识与 Agent 知识召回、问题改写 |
| QueryEnhanceNode | `QueryEnhanceNode` | 独立问题和 canonical query |
| SchemaRecallNode | `SchemaRecallNode` | 表/列向量召回及选择表约束 |
| TableRelationNode | `TableRelationNode` | Schema、物理/逻辑关系、语义模型合并 |
| FeasibilityAssessmentNode | `FeasibilityAssessmentNode` | 可执行/澄清/无法执行 |
| PlannerNode | `PlannerNode` | 结构化多工具计划 |
| PlanExecutorNode | `PlanExecutorNode` | 计划验证、当前步、下一工具 |
| HumanFeedbackNode | `HumanFeedbackNode` | 中断、接受、拒绝与重新规划 |
| SqlGenerateNode | `SqlGenerateNode` | 首次 SQL 与错误修复 |
| SemanticConsistencyNode | `SemanticConsistencyNode` | 语义一致性校验与重生原因 |
| SqlExecuteNode | `SqlExecuteNode` | 只读执行、ResultSet、图表补充 |
| PythonGenerateNode | `PythonGenerateNode` | 基于 SQL 结果生成受限 Python |
| PythonExecuteNode | `PythonExecuteNode` | Docker 执行、错误反馈与重试 |
| PythonAnalyzeNode | `PythonAnalyzeNode` | 结果分析及 fallback |
| ReportGeneratorNode | `ReportGeneratorNode` | 流式 Markdown/ECharts 最终报告 |

## 4. 数据模型映射

原 13 张业务表保持同名与核心字段语义：

`agent`、`datasource`、`agent_datasource`、`agent_datasource_tables`、
`logical_relation`、`semantic_model`、`business_knowledge`、`agent_knowledge`、
`agent_preset_question`、`chat_session`、`chat_message`、`user_prompt_config`、`model_config`。

Python 增加：

- `graph_checkpoint`：替代进程内 Graph interrupt 状态，允许重启后恢复。
- `conversation_turn`：保留最近 N 轮问题与 Planner 输出，等价承载多轮 Graph Memory。

另外把上游前端已提交、后端却未持久化的 `humanReviewEnabled` 落到 `agent`；把原先编码在
`databaseName` 中的 Schema 显式落为 `schema_name`，同时继续兼容旧编码。

## 5. 框架替换

| Java/Spring 生态 | Python 等价 |
|---|---|
| Spring Boot WebFlux | FastAPI + Uvicorn + StreamingResponse |
| Spring AI Alibaba StateGraph | 显式异步状态图引擎 |
| MyBatis / JDBC | SQLAlchemy 2 async 管理库 + SQLAlchemy/JDBC bridge Connector |
| Spring AI ChatClient | `httpx` OpenAI-compatible client |
| Spring AI VectorStore | Simple JSON / Elasticsearch async / PyMilvus |
| Spring AI MCP Server | MCP Python SDK 2.x `MCPServer` |
| Reactor `Flux` | async generator + SSE |
| Spring Security filter | FastAPI Header 鉴权 + bcrypt |
| ApplicationEvent | BackgroundTasks + 持久化状态恢复调度器 |
| Docker Java client | Docker SDK for Python |
| Micrometer/OTel | OpenTelemetry Python SDK |

## 6. 有意不复制的上游错误

以下不是功能删减，而是根因修复：

- 补齐前端已经调用但 Java 后端缺失的两个知识查询端点。
- Human Review 默认值真正持久化，并允许单次请求覆盖。
- Agent 更新只修改显式提交字段，避免 UI 局部保存清空 Prompt/头像。
- 前端 SSE 改用 `fetch`，API Key 能随流请求发送。
- Compose 挂载 Docker socket，并使用正确的 Nuxt 4 Node 产物。
- Compose 恢复管理 MySQL 及 MySQL/PostgreSQL 示例数据源；按用户明确要求增加 Redis 热层，但
  保留 MySQL 为事实源，并实现故障回退，不冒充上游原生能力。
- Embedding 模型改变不再直接复用旧向量，而是候选空间重建后切换。
- 报告图表配置从 JavaScript 求值改为严格 JSON 解析。
- H2 与 Dameng 已有 Handler 的能力在 API 类型列表中如实公开。

具体影响及环境性差异见 `KNOWN_DIFFERENCES.md`。
