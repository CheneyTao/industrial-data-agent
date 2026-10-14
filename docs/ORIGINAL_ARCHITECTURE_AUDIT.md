# Spring AI Alibaba DataAgent `main` 完整性与架构审计

> 审计日期：2026-09-15（Asia/Shanghai）  
> 原仓库：Spring AI Alibaba DataAgent 的本地审计副本  
> 上游：`https://github.com/spring-ai-alibaba/DataAgent.git`  
> 分支：`main`  
> 审计提交：`3fb7852f71b2f6bfda3de392f79207df7294199f`

## 1. 结论

### 1.1 下载与 Git 完整性

本地副本完整，并且与审计时远端 `main` 完全一致。

- `HEAD == origin/main == 3fb7852f71b2f6bfda3de392f79207df7294199f`
- 735 个 Git 受控文件，0 个缺失文件
- 工作区干净
- `git fsck --full --no-dangling` 无错误
- 无 Git submodule
- 无 sparse checkout
- 无 Git LFS 指针遗漏
- 根 Maven Wrapper、后端、Nuxt 前端、文档、CI 与 Docker 文件均存在

### 1.2 上游构建基线

当前提交只修改 README，因此后端工作流按 `paths-ignore: '**.md'` 被跳过。其直接父提交 `000e97f` 已通过：

- `test-and-build`
- `format`
- `check-style`
- `Frontend Build Check`（安装、Vitest、Nuxt build）
- `linter`
- `license-check`
- `secret-check`

当前提交自身通过 Frontend Build Check、linter、license-check 与 secret-check。也就是说，源码具备可信的编译/单测基线，但这不等于所有产品路径端到端闭环。

### 1.3 产品闭环结论

**仓库文件完整；官方 `main` 的产品能力并非全部闭环。**

后端核心工作流、模型管理、数据源、知识库、语义模型、MCP、SSE、会话、报告和沙箱实现均存在；但前后端契约、部署文件、配置消费和租户隔离存在确定的上游断口。Python 迁移以源码行为为基准，同时对这些断口做根因修复，不能机械复制错误。

## 2. 仓库规模与边界

| 项目 | 数量 |
|---|---:|
| Git 受控文件 | 735 |
| Java 生产文件 | 303 |
| Java 测试文件 | 177 |
| Java 测试方法（`@Test` 等） | 1693 |
| 前端文件 | 136 |
| 前端现有测试用例 | 17 |
| REST 映射 | 98 |
| 工作流节点 | 16 |
| 条件分发器 | 11 |
| Prompt 模板 | 17 |
| 管理数据库业务表 | 13 |

Java 顶层包分布：

| 包 | 文件数 | 职责 |
|---|---:|---|
| `annotation` | 3 | 枚举校验、MCP 工具标记 |
| `aop` | 1 | Prompt 注入/切面能力 |
| `bo` | 9 | 数据库、Schema、结果集业务对象 |
| `config` | 7 | StateGraph、MCP、安全、OpenAPI、OTel、混合检索 |
| `connector` | 34 | 多数据库连接、DDL、Accessor、连接池 |
| `constant` | 2 | 状态键与文档元数据键 |
| `controller` | 15 | REST/SSE/API |
| `converter` | 3 | Entity/DTO/VO 转换 |
| `dto` | 33 | API、规划、Prompt、Schema、搜索 DTO |
| `entity` | 12 | 管理库实体 |
| `enums` | 10 | 数据源、模型、知识、切分、输出类型等 |
| `event` | 3 | 知识向量化与删除事件 |
| `exception` | 2 | 输入与服务端异常 |
| `mapper` | 13 | MyBatis 持久化 |
| `prompt` | 3 | Prompt 装载、常量、组装 |
| `properties` | 4 | DataAgent、沙箱、文件、OSS 配置 |
| `security` | 4 | API Key 认证 |
| `service` | 84 | 领域服务、RAG、模型、图、MCP、沙箱等 |
| `splitter` | 3 | 句子、段落、语义切分 |
| `strategy` | 1 | 图 Checkpoint 策略 |
| `util` | 17 | SQL/JSON/Markdown/流/报告/状态工具 |
| `vo` | 12 | API 输出模型 |
| `workflow` | 27 | 16 节点与 11 分发器 |

## 3. 总体架构

```text
Nuxt 4 / Vue 3 / Pinia
  ├─ 管理页：Agent、模型、数据源、知识、语义模型、Prompt
  └─ 运行页：会话、SSE 工作流时间线、SQL 结果、报告、HITL
                     │ HTTP + SSE
                     ▼
Spring Boot 3.4 / WebFlux / Security
  ├─ 98 个 REST/SSE 映射
  ├─ StateGraph（16 节点、11 条件路由）
  ├─ OpenAI-compatible 动态 Chat/Embedding 模型
  ├─ RAG（Simple / Elasticsearch / Milvus）
  ├─ Text-to-SQL 与多数据库 Connector
  ├─ 每任务 Docker Python Sandbox
  ├─ MCP Server（2 个 Tool）
  ├─ Langfuse OTLP / 节点级 tracing
  └─ MyBatis + 管理数据库
                     │
        ┌────────────┼─────────────┐
        ▼            ▼             ▼
  分析数据源      向量存储      Docker Engine
```

## 4. 管理数据模型（13 表）

| 表 | 主要用途 |
|---|---|
| `agent` | Agent 基础信息、发布状态、API Key |
| `datasource` | 被分析数据源连接配置 |
| `agent_datasource` | Agent 与数据源关联；每 Agent 最多一个 active |
| `agent_datasource_tables` | 每个关联选择的数据表 |
| `logical_relation` | 用户配置的逻辑外键 |
| `semantic_model` | 字段业务名、同义词、业务描述等语义层 |
| `business_knowledge` | 业务术语、描述、同义词、召回与向量状态 |
| `agent_knowledge` | 文档、QA、FAQ、文件信息、切分与向量状态 |
| `agent_preset_question` | Agent 预设问题 |
| `chat_session` | 会话、置顶、软删除与标题 |
| `chat_message` | 消息、类型、JSON metadata |
| `user_prompt_config` | Agent/全局 Prompt 优化配置与优先级 |
| `model_config` | Chat/Embedding 模型、代理、激活状态 |

Spring AI JDBC Chat Memory 还会按框架 Schema 建立消息记忆表；它与产品层 `chat_message` 分开承担模型上下文和 UI 历史记录。

## 5. REST/SSE 能力清单（98 个映射）

### 5.1 Agent（12）

- `GET /api/agent/list`
- `GET /api/agent/{id}`
- `POST /api/agent`
- `PUT /api/agent/{id}`
- `DELETE /api/agent/{id}`
- `POST /api/agent/{id}/publish`
- `POST /api/agent/{id}/offline`
- `GET /api/agent/{id}/api-key`
- `POST /api/agent/{id}/api-key/generate`
- `POST /api/agent/{id}/api-key/reset`
- `DELETE /api/agent/{id}/api-key`
- `POST /api/agent/{id}/api-key/enable?enabled=...`

### 5.2 Agent 数据源关联（7）

- `POST /api/agent/{agentId}/datasources/init`
- `GET /api/agent/{agentId}/datasources`
- `GET /api/agent/{agentId}/datasources/active`
- `POST /api/agent/{agentId}/datasources/{datasourceId}`
- `POST /api/agent/{agentId}/datasources/tables`
- `DELETE /api/agent/{agentId}/datasources/{datasourceId}`
- `PUT /api/agent/{agentId}/datasources/toggle`

### 5.3 Agent 知识（7）

- `GET /api/agent-knowledge/{id}`
- `POST /api/agent-knowledge/create`（multipart）
- `PUT /api/agent-knowledge/{id}`
- `PUT /api/agent-knowledge/recall/{id}?isRecall=...`
- `DELETE /api/agent-knowledge/{id}`
- `POST /api/agent-knowledge/query/page`
- `POST /api/agent-knowledge/retry-embedding/{id}`

### 5.4 预设问题（3）

- `GET /api/agent/{agentId}/preset-questions`
- `POST /api/agent/{agentId}/preset-questions`
- `DELETE /api/agent/{agentId}/preset-questions/{questionId}`

### 5.5 业务知识（8）

- `GET /api/business-knowledge?agentId=...&keyword=...`
- `GET /api/business-knowledge/{id}`
- `POST /api/business-knowledge`
- `PUT /api/business-knowledge/{id}`
- `DELETE /api/business-knowledge/{id}`
- `POST /api/business-knowledge/recall/{id}?isRecall=...`
- `POST /api/business-knowledge/refresh-vector-store?agentId=...`
- `POST /api/business-knowledge/retry-embedding/{id}`

### 5.6 会话与消息（9）

- `GET /api/agent/{id}/sessions`
- `POST /api/agent/{id}/sessions`
- `DELETE /api/agent/{id}/sessions`
- `GET /api/sessions/{sessionId}/messages`
- `POST /api/sessions/{sessionId}/messages`
- `PUT /api/sessions/{sessionId}/pin?isPinned=...`
- `PUT /api/sessions/{sessionId}/rename?title=...`
- `DELETE /api/sessions/{sessionId}`
- `POST /api/sessions/{sessionId}/reports/html`

### 5.7 数据源与逻辑外键（14）

- `GET /api/datasource/types`
- `GET /api/datasource`
- `GET /api/datasource/{id}`
- `GET /api/datasource/{id}/tables`
- `POST /api/datasource`
- `PUT /api/datasource/{id}`
- `DELETE /api/datasource/{id}`
- `POST /api/datasource/{id}/test`
- `GET /api/datasource/{id}/tables/{tableName}/columns`
- `GET /api/datasource/{id}/logical-relations`
- `POST /api/datasource/{id}/logical-relations`
- `PUT /api/datasource/{id}/logical-relations/{relationId}`
- `DELETE /api/datasource/{id}/logical-relations/{relationId}`
- `PUT /api/datasource/{id}/logical-relations`（全量替换）

### 5.8 Graph（2）

- `GET /api/stream/search`（SSE）
- `POST /api/stream/stop`

Graph 查询参数保留：`agentId`、`conversationId`、`threadId`、`query`、`humanFeedback`、`humanFeedbackContent`、`rejectedPlan`、`nl2sqlOnly`。

### 5.9 模型配置（7）

- `GET /api/model-config/list`
- `POST /api/model-config/add`
- `PUT /api/model-config/update`
- `DELETE /api/model-config/{id}`
- `POST /api/model-config/activate/{id}`
- `POST /api/model-config/test/{id}`
- `GET /api/model-config/check-ready`

### 5.10 Prompt 配置（14）

- `POST /api/prompt-config/save`
- `GET /api/prompt-config/{id}`
- `GET /api/prompt-config/list`
- `GET /api/prompt-config/list-by-type/{promptType}`
- `GET /api/prompt-config/active/{promptType}`
- `GET /api/prompt-config/active-all/{promptType}`
- `DELETE /api/prompt-config/{id}`
- `POST /api/prompt-config/{id}/enable`
- `POST /api/prompt-config/{id}/disable`
- `GET /api/prompt-config/types`
- `POST /api/prompt-config/batch-enable`
- `POST /api/prompt-config/batch-disable`
- `POST /api/prompt-config/{id}/priority`
- `POST /api/prompt-config/{id}/display-order`

### 5.11 语义模型（11）

- `GET /api/semantic-model`
- `GET /api/semantic-model/{id}`
- `POST /api/semantic-model`
- `PUT /api/semantic-model/{id}`
- `DELETE /api/semantic-model/{id}`
- `DELETE /api/semantic-model/batch`
- `PUT /api/semantic-model/enable`
- `PUT /api/semantic-model/disable`
- `POST /api/semantic-model/batch-import`
- `GET /api/semantic-model/template/download`
- `POST /api/semantic-model/import/excel`（multipart）

### 5.12 其他（4）

- `POST /api/upload/avatar`
- `GET /api/upload/**`
- `GET /api/agent/{agentId}/sessions/stream`（会话标题更新 SSE）
- `GET /echo/ok`

## 6. StateGraph 状态流

### 6.1 主图

```text
START
  → IntentRecognition
      ├─ chat / irrelevant → END
      └─ analysis → EvidenceRecall
  → QueryEnhance
  → SchemaRecall
  → TableRelation
      ├─ retryable error → TableRelation（最多 3 次）
      └─ fatal / empty → END
  → FeasibilityAssessment
      ├─ clarification/free-chat → END
      └─ data analysis → Planner
  → PlanExecutor
      ├─ invalid plan → Planner（修复，最多 2 次）
      ├─ human review → HumanFeedback
      ├─ SQL step → SqlGenerate
      ├─ Python step → PythonGenerate
      ├─ report step → ReportGenerator
      └─ plan complete → END

HumanFeedback
  ├─ waiting → interrupt/checkpoint
  ├─ approve → PlanExecutor
  ├─ reject → Planner（最多 3 次）
  └─ limit → END

SqlGenerate
  ├─ generation retry → SqlGenerate
  └─ candidate → SemanticConsistency
SemanticConsistency
  ├─ fail → SqlGenerate
  └─ pass → SqlExecute
SqlExecute
  ├─ execution fail → SqlGenerate
  └─ success → PlanExecutor

PythonGenerate → PythonExecute
PythonExecute
  ├─ fail, retries remain → PythonGenerate
  ├─ fail, exhausted → PythonAnalyze(fallback)
  └─ success → PythonAnalyze
PythonAnalyze → PlanExecutor

ReportGenerator → END
```

### 6.2 16 个节点及行为语义

| 节点 | 输入/动作 | 关键输出或路由 |
|---|---|---|
| IntentRecognition | LLM 结构化意图识别 | `classification/response`；闲聊直接结束 |
| EvidenceRecall | 多轮改写；并行召回业务知识与 Agent 知识 | Evidence 文本；单支失败不阻断另一支 |
| QueryEnhance | 规范化与扩展查询 | `canonical_query`、`expanded_queries` |
| SchemaRecall | 用规范化查询召回表，再按表精确取列 | 表、列文档；无 active datasource 则结束 |
| TableRelation | 物理+逻辑外键、缺表补全、LLM 精选、语义模型 | SchemaDTO、dialect、semantic prompt；可重试 |
| FeasibilityAssessment | 判定 DATA_ANALYSIS / NEED_CLARIFICATION / FREE_CHAT | 非分析请求直接输出回答 |
| Planner | 正常规划或带验证错误修复计划 | `thought_process` + `execution_plan` |
| PlanExecutor | 校验工具、参数、步号并选择下一节点 | 只允许 SQL/Python/Report 三类工具 |
| HumanFeedback | checkpoint 前中断；批准或拒绝 | 同 thread 恢复；拒绝触发重规划 |
| SqlGenerate | 新 SQL 或基于失败原因修复 | SQL；检测“修复后 SQL 未变化” |
| SemanticConsistency | 先静态 SQL 安全校验，再 LLM 语义校验 | passed/reason；失败回 SQL 修复 |
| SqlExecute | active datasource 执行、1000 行上限、30 秒超时 | 原始结果、ResultSet 展示事件、可选图表配置 |
| PythonGenerate | SQL 结果采样+资源约束+错误反馈生成代码 | Python 代码；支持 PEP 723 依赖声明 |
| PythonExecute | 每任务 Docker 沙箱执行 | stdout JSON；最多 5 次，最终进入 fallback |
| PythonAnalyze | LLM 总结 Python 输出 | step analysis；fallback 输出固定说明 |
| ReportGenerator | 汇总规划与各步骤结果并流式生成 Markdown | `RESULT`、`FINAL_ANSWER` |

### 6.3 状态与 Checkpoint 语义

- 所有声明的 Graph State Key 使用 REPLACE 策略。
- `conversationId` 表示跨轮会话；`threadId` 表示一次图运行。
- Human-in-the-loop 必须使用同一个 `threadId` 恢复。
- 默认 MySQL Checkpointer，可切换内存实现。
- 正常完成、取消或错误会释放 checkpoint；等待人工反馈时保留。
- 多轮上下文最多保留配置的轮数，记录“用户问题 + Planner 输出”。
- `nl2sqlOnly=true` 会生成单 SQL 步骤并禁用人工复核。

### 6.4 SSE 协议

`GraphNodeResponse` 字段：

- `agentId`
- `threadId`
- `eventType`
- `stepId`
- `attempt`
- `nodeName`
- `text`
- `textType`
- `error`
- `complete`

事件：普通 `data`、命名 `error`、命名 `complete`。业务事件类型包括 `NODE_OUTPUT`、`FINAL_ANSWER`、`HUMAN_FEEDBACK_REQUIRED`。文本包裹标记包括 JSON、PYTHON、SQL、HTML、MARK_DOWN、RESULT_SET、TEXT，后端在发给浏览器前剥离边界标记并设置 `textType`。

## 7. Text-to-SQL 与数据源

### 7.1 已实现 Connector/Handler

- MySQL
- PostgreSQL
- H2（有 Handler，但未出现在 `/api/datasource/types`）
- Dameng
- SQL Server
- Oracle
- Hive

枚举还声明 SQLite、Hologres、VPC、ADB-PG、MaxCompute、FC memory、virtual datasource 等，但没有对应 Handler，不能作为当前可用能力计入。

### 7.2 Connector 统一能力

- 列举数据库/Schema/表
- 拉取表与列元数据
- 读取物理外键
- 列样本数据
- SQL 执行与结果集转换
- 数据源连接测试
- 方言相关 LIMIT/TOP/FETCH FIRST
- 用户逻辑外键与物理外键合并

PostgreSQL 将 `database|schema` 编码在 `databaseName`，Oracle 将 `service|schema` 编码在 `databaseName`；这是后端现有兼容约定，不是显式字段模型。

## 8. RAG 与知识系统

### 8.1 三层知识

1. Schema RAG：表、列、主键、物理/逻辑外键、样本值。
2. Semantic Model：物理字段到业务名/同义词/描述的显式映射。
3. Business Knowledge + Agent Knowledge：业务术语、QA/FAQ、上传文档。

### 8.2 Vector Store

- 默认 `MetadataAwareSimpleVectorStore`，序列化到 `./vectorstore/vectorstore.json`。
- 可换 Elasticsearch 或 Milvus。
- 文档元数据至少携带 `agentId`/`datasourceId`、`vectorType` 与领域记录 ID。
- Schema 表召回默认 `topK=10`、阈值 `0.2`。
- 业务/知识默认 `topK=8`、阈值 `0.4`。
- 删除批上限 5000。
- 替换采用“先加新 ID，再删旧 ID；失败回滚新文档”的方式，避免先删后加导致数据窗口为空。
- Embedding 批处理默认 8000 token、20% 预留、每批最多 10 个文本。

### 8.3 混合检索

- 默认关闭。
- 向量检索与关键词检索并发，每支默认超时 3000 ms。
- Elasticsearch 关键词分支用 `content` match + metadata filter，抓取 `2 * topK`，默认 `minScore=0.5`。
- 融合使用 RRF，常量 `k=60`，按 `1/(60+rank)` 累加并截取 topK。
- 关键词检索失败降级为纯向量；向量分支失败则整体失败。

### 8.4 Agent Knowledge 生命周期

- 类型：DOCUMENT、QA、FAQ。
- 文档由 Apache Tika 解析。
- 分块策略：token、recursive、sentence、semantic、paragraph。
- 创建提交后异步向量化：PENDING → PROCESSING → COMPLETED/FAILED。
- 删除先逻辑删除，再异步清理文件和向量。
- 每小时有兜底任务，清理删除超过 30 分钟且资源未清理的记录，每批 100 条。
- 文件存储支持 local 与 Alibaba Cloud OSS。

## 9. Python Sandbox

- 生产路径只使用 Docker Sandbox，无宿主机执行回退。
- 每次 Python 步骤新建一个任务容器，完成后强制关闭/删除。
- 默认镜像：`agentscope-registry.ap-southeast-1.cr.aliyuncs.com/agentscope/runtime-sandbox-base:latest`。
- 非 privileged；内存 500 MB；CPU 1 core；最大连接 4096。
- 并发 4、等待队列 10。
- 代码上限 256 KiB；stdin 10 MiB；stdout 1 MiB；stderr 256 KiB；PEP 723 metadata 8 KiB。
- 代码执行超时 60 秒；依赖安装超时 3 分钟。
- PEP 723 只接受单个 `# /// script` 区块，以及 `dependencies`、`requires-python` 两个键。
- 最多 20 个动态依赖；安装到 `/tmp/dataagent-deps`，通过 `PYTHONPATH` 注入。
- SQL 结果以 JSON 写入 stdin；沙箱 Bootstrap 用版本化 marker + Base64 JSON envelope 回传。
- 最多生成/执行 5 次；耗尽后进入 PythonAnalyze fallback，不让整个报告链路崩溃。

## 10. 模型管理

- OpenAI-compatible Chat 与 Embedding 两种模型类型。
- 字段包括 provider、base URL、API key、model name、temperature、max tokens、自定义 completions/embeddings path。
- 支持每模型 HTTP 代理及可选用户名/密码。
- 同一模型类型仅允许一个 active。
- Registry 懒加载并在配置激活/更新后热刷新。
- 激活或更新 Embedding 模型前检查向量维度兼容性。
- 更新时不允许改变 model type。
- 列表输出掩码 API key/proxy password；掩码回传时保留原密钥。
- active 配置禁止删除；删除采用软删除。
- 测试 Chat 会发送 `Hello`，Embedding 会嵌入 `Test` 并校验维度。
- readiness 同时要求 active Chat 与 active Embedding。

## 11. Human-in-the-loop

- 人工复核是运行请求参数 `humanFeedback`，不是 Agent 持久化属性。
- 图在 HumanFeedback 节点执行前中断并保存 checkpoint。
- 前端收到 `HUMAN_FEEDBACK_REQUIRED` 后用原 `threadId` 提交批准/拒绝。
- 批准后关闭本轮 review 并继续当前计划。
- 拒绝后保存反馈、重置当前步骤并回 Planner 修复；最多 3 次。
- `rejectedPlan` 由前端带回，供恢复/显示使用。

## 12. MCP

Spring AI WebFlux MCP Server 暴露两个工具：

| Tool | 能力 |
|---|---|
| `listAgentsToolCallback` | 按状态、关键词查询 Agent |
| `nl2SqlToolCallback` | 指定 Agent 执行 `nl2sqlOnly` 图并返回 SQL |

自定义 `@McpServerTool` 标记用于把 MCP 工具从普通 ChatClient Tool 扫描中排除，避免 ChatModel/ChatClient/MCP 工具之间形成循环依赖。

## 13. 报告与前端交互

- 报告节点流式产生 Markdown，可包含 fenced `echarts`/JSON 配置。
- SQL 节点可先发原始 ResultSet，再异步补充 LLM 生成的图表 displayStyle。
- 前端以 Pinia 保存每个会话的运行态，展示节点时间线、SQL、Python、表格、图表和最终报告。
- 会话首条消息可异步生成 20 字以内中文标题，并通过每 Agent 的 SSE 通道推送 `titleUpdated`。
- HTML 报告下载会包装完整页面结构；Marked + ECharts 默认从可配置 HTTPS CDN 加载，严格离线部署需改成本地静态资源。
- 头像上传限制 image MIME 与默认 2 MiB；文件可本地或 OSS 存储。
- Nuxt 开发代理把 `/api/**` 转发到 `localhost:8065`。

## 14. API Key 与鉴权

- WebFlux Security 只对 `GET /api/stream/search` 建立 Agent API Key 鉴权入口，其他管理接口保持开放。
- 若 Agent 的 `api_key_enabled != 1`，流式接口不要求密钥。
- 启用时接受 `X-API-Key` 或 `Authorization: Bearer ...`。
- 新密钥为 `sk-...`；服务端默认用 Spring `DelegatingPasswordEncoder` 的 `{bcrypt}` 形式保存，并追加 `::last4` 供掩码显示。
- 兼容历史明文密钥。
- 普通查询只返回 `****last4`；仅生成/重置时返回一次明文。

## 15. 可观测性

- Langfuse 通过 OTLP HTTP 接收 OpenTelemetry Span。
- 根 Span 覆盖一次 Graph 运行，并记录 agent/thread/nl2sql/HITL 输入属性。
- 每个节点、每次重试创建独立子 Span。
- 按节点 attempt 记录 prompt/completion/total token。
- 节点输入输出单属性截断为 8000 字符。
- 客户端中断、正常结束和异常路径均有显式 Span/累加器清理，避免内存泄漏。

## 16. 已确认的上游断口

| 严重度 | 断口 | 证据与影响 | Python 版处理原则 |
|---|---|---|---|
| 阻断 | Docker 前端 Dockerfile 指向不存在的 `data-agent-frontend`，实际目录为 `data-agent-frontend-nuxt`；仍按旧静态 `dist` 产物复制 | 官方 Compose 无法按该文件构建当前 Nuxt 前端 | 改为 Nuxt 4 正确构建/Node server 或静态产物路径，并做镜像构建测试 |
| 阻断 | Compose 后端未挂载 Docker socket，也未配置可达远端 Docker Host | 容器内 Python 工作流无法连接默认 `unix:///var/run/docker.sock` | 显式挂载 socket或配置 remote sandbox；健康检查在启动时报告 |
| 高 | 前端请求 `GET /api/agent-knowledge/agent/{agentId}` 与 `/statistics/{agentId}`，后端没有对应映射 | 调用即 404；现有页面暂未调用，因此前端构建无法发现 | Python 兼容 API 补齐两个端点并加契约测试 |
| 高 | 前端 Agent 表单提交 `humanReviewEnabled`，后端 Entity/SQL 无该字段 | 用户在 Agent 配置页的设置不会持久化；真正生效的是运行页 query 参数 | 明确区分 Agent 默认值与本轮 override；新增持久化字段并保持 query 参数优先级 |
| 高 | Schema 向量按 `datasourceId` 共享，初始化时读取同一数据源所有 Agent 所选表的并集 | 一个 Agent 可召回另一个 Agent 选择的表；存在配置隔离泄漏 | Schema 文档增加 `agentDatasourceId/agentId` 元数据并按当前关联过滤 |
| 中 | 前端 `Datasource` 有独立 `schemaName`，后端 Entity/SQL 没有；PostgreSQL/Oracle把 Schema 编码进 `databaseName` | UI/API 模型语义不一致，编辑与迁移易丢 Schema | Python 数据模型用显式 `schema_name`，同时读写兼容旧 `database|schema` 编码 |
| 中 | Prompt 类型三方漂移 | Controller 返回 5 类；UI 显示 `report-generator/planner/sql-generator/general-chat`；运行时只有 `report-generator` 真正消费配置 | 建立单一 PromptType 枚举；所有声明类型接入对应节点；保留旧值别名 |
| 中 | `agent.prompt` 字段存在但运行时未读取 | Agent 自定义 Prompt 保存后不影响图 | 定义清楚注入位置并接入运行上下文 |
| 中 | `/api/datasource/types` 不返回 H2，但 H2 Handler/DDL 已实现 | 能力存在却无法从标准 UI 创建 | 类型列表从 Registry 生成，H2 可配置为开发类型 |
| 中 | MCP 文档同时给出 `/sse` 与 `/mcp/connection`，无显式项目级 endpoint 配置 | 客户端配置容易失败，实际路径依赖 Starter 版本默认值 | Python 版固定并文档化 Streamable HTTP 与兼容 SSE 路径，提供 MCP 自检 |
| 低 | Quick Start 仍称 Access API “未完全实现”，但源码已有 API Key 管理与 stream 鉴权 | 文档与源码版本漂移 | 以可执行契约测试为准更新文档 |
| 低 | 文档称默认向量库为内存；源码会持久化到 JSON 文件 | 运维预期不一致 | 文档明确开发持久化位置与生产后端 |
| 低 | `agent_knowledge.splitter_type` SQL 注释未列 `paragraph`，源码和文档已支持 | 只影响说明，不影响存储 | 枚举、Schema 注释和 UI 使用同一来源 |

## 17. 本机可验证范围与限制

下列内容是冻结原仓库审计当时的环境快照，不代表 Python 迁移的最终验证状态：

- 没有可用 JDK/Java，因此没有在本机重复 Maven 编译；使用上游同提交链 CI 作为基线证据。
- 当时 Docker daemon 不可用，因此没有运行原项目 Compose；随后已修复 Docker Desktop，并对
  Python 迁移执行完整 Compose、数据库、Sandbox、GPU BGE-M3 与 MCP 验证，见 `VERIFICATION.md`。
- 前端尚未在原目录安装 `node_modules`，避免污染用户的干净原仓库；上游 Frontend Build Check 已通过。

这些限制只影响“本机重新执行”证据，不影响 Git 完整性结论和上述源码级断口判断。

## 18. Python 重构验收基线

Python 版只有同时满足以下条件才可称为功能等价：

1. 98 个原 REST/SSE 映射均有兼容实现或明确兼容重定向。
2. 16 节点、11 路由、全部状态键、重试上限、HITL interrupt/resume 语义有自动测试。
3. 17 个 Prompt 模板保留，并验证结构化输出模型。
4. Schema、Semantic Model、Business Knowledge、Agent Knowledge 四条上下文链都进入相同下游节点。
5. Simple/Elasticsearch/Milvus 向量后端和 RRF 混合检索具有相同 metadata filter 语义。
6. 七类现有数据库 Handler 均有 Python 对应适配器；UI 公布能力与 Registry 一致。
7. Docker 沙箱没有宿主机 fallback，资源/尺寸/超时/PEP 723/清理边界均保留。
8. MCP 的两个 Tool、模型热切换、API Key、会话标题 SSE、HTML/Markdown 报告全部可验证。
9. 原 Nuxt 前端能够直接对接兼容 API；上游缺失端点在 Python 后端补齐。
10. 单元、契约、工作流组件、真实数据库集成、Docker 沙箱和可选 live-model 测试分层执行。
11. 建立逐文件 Java → Python 映射，303 个 Java 生产文件覆盖率必须为 100%。
12. Docker Compose 实际可启动，健康检查能区分“管理功能可用”和“Python 沙箱可用”。

本文件是迁移范围基线；后续实现不得通过删除上游已有能力来缩小验收范围。
