# DataAgent-Python 全项目流程图

更新：2026-09-18。以当前 Python 实现为准；Java 上游审计资料仅用于迁移历史对照。

## 1. 全局：从数据到回答

```mermaid
flowchart TB
  subgraph Data[数据准备与治理]
    Raw[公开样例原始数据] --> DB[(工业分析 MySQL)]
    Synth[确定性生成的合成日志和运营数据] --> DB
    Alias[型号语义维表：原编码与 A-D 型机组别名] --> DB
    Meta[数据源绑定与已选表范围] --> Schema[读取真实字段和关系]
    DB --> Schema
    Docs[业务术语 / 语义模型 / 文档知识] --> Index[切分与 Embedding 索引]
    Schema --> Index
  end
  User[用户自然语言与多轮上下文] --> Web[Nuxt 网页]
  External[外部大模型与 MCP 客户端] --> MCP[MCP Server]
  Web --> API[FastAPI + SSE]
  API --> Graph[GraphEngine：理解、检索、规划与执行]
  MCP -->|完整请求 analyze_request| Graph
  MCP -->|独立查询、画图、分析工具| Services[共享 AnalysisService]
  Index --> Graph
  Graph <--> ModelRouter[统一模型调用与有界切换]
  ModelRouter <--> Model[配置中的主模型]
  ModelRouter <--> Fallback[配置中的备用模型列表]
  ModelRouter -->|模型、耗时、异常、切换记录| Store
  Graph --> Services
  Services --> Guard[只读 AST + 已选表约束 + 结果行数上限]
  Guard --> DB
  DB --> Result[结果集：空值、截断与实际执行 SQL]
  Result --> Store[(持久化证据目录)]
  Result --> Chart[模型读取全量画像和样例；选择维度/指标/分组与图型]
  Chart --> ValidateChart{全部结果校验通过?}
  ValidateChart -->|否：有界反馈重选| Chart
  ValidateChart -->|是：只从结果取数| Plot[确定性本地 ECharts 配置]
  Plot --> Stats[图表对应的统计事实与范围限制]
  Stats --> CalcPlan[按需选择声明式统计与两期比较]
  CalcPlan --> Calculate[本地 Decimal 计算：只从已存行取值]
  Calculate --> Draft[按需撰写：完整证据包，禁止静默截断]
  Calculate --> Store
  Draft --> Review{事实与来源核对通过?}
  Review -->|否：精确片段修订后复核| Draft
  Review -->|是| Report[发布报告与有依据的建议]
  Draft -->|草稿与审查记录| Store
  Chart -->|每次决策与校验错误| Store
  Plot --> Store
  Stats --> Store
  Report --> Store
  Result --> UI[结果卡片 / ECharts / 报告 / 展开查询依据]
  Plot --> UI
  Report --> UI
  UI --> User
```

这里存在两个入口，但不是两套业务逻辑。网页直接调用共享 Python 服务；外部模型通过 MCP 调用相同服务。网页内部没有为了使用 MCP 而绕回本机网络，也不宣称每个节点都经过 MCP。

当前是**模型规划 + 显式执行器**，不是任意代码自由执行的 Agent。安全规则固定，问题意图、计划步骤、展示类型和文字分析由模型结合上下文选择。更换数据源不需要硬编码一批问题与答案。

备用模型只在配置的同一 CHAT 接口内，因网络/超时或服务暂不可用而有界切换；SQL 错误不触发切换，已交付流不重放，向量模型不参与。每次尝试本地留存 `model_call`，失败节点留存 `node_error`，不依赖外部监控平台。策略与实测范围见 [MODEL_FALLBACK.md](MODEL_FALLBACK.md)。

## 2. 数据准备、索引和命名

```mermaid
flowchart LR
  Files[原始公开 CSV] --> Hash[文件 SHA-256 与行数检查]
  Hash --> Original[pdm_* 来源表]
  Original --> Gen[带版本与来源关联的确定性生成器]
  Gen --> Synthetic[synthetic_* 合成演示表]
  Original --> Views[分析视图]
  Synthetic --> Views
  Models[dim_machine_models] --> Views
  Views --> Select[Agent 选择允许查询的表和视图]
  Select --> Inspect[读取字段 / 外键 / 样例]
  Inspect --> SchemaIndex[Schema 文档索引]
  Knowledge[业务知识与数据限制说明] --> Chunk[文档切分]
  Chunk --> Embed[Embedding]
  SchemaIndex --> Embed
  Embed --> Vector[按 Agent、数据源、Embedding 空间隔离]
```

- `pdm_*` 表示本项目保留的来源数据，不等于已经验证的真实工厂现场记录。
- `synthetic_*` 是用于演示的生成数据，包括日志、工单、成本、产量和 OEE；不能作为真实业务收益或事故根因的证据。
- `model1`～`model4` 是匿名设备型号类别，**不是大模型名称**，也不是单台设备编号。显示为 A～D 型机组并保留原编码，不虚构厂商、规格或性能等级。
- `machineID` 才标识单台设备。模型配置页的 CHAT/EMBEDDING 是另外一套概念。
- 工业包中的参考 SQL 和独立演示脚本可用于对照；其执行成功不等于网页已经完成了真实自然语言生成 SQL。二者要分别验证。

## 3. 运行时完整节点与分支

```mermaid
flowchart TB
  Start[新请求：创建 runId / 装载上下文] --> Intent[1 IntentRecognition：识别意图]
  Intent -->|普通对话| End[结束]
  Intent --> Evidence[2 EvidenceRecall：召回业务依据]
  Evidence --> Enhance[3 QueryEnhance：补全语义]
  Enhance --> Preserve{显式数值约束是否保留}
  Preserve -->|是：补全只作参考| Schema[4 SchemaRecall：召回表和字段]
  Preserve -->|否：拒绝改写并保留原话| Schema
  Schema -->|没有可用表| Clarify[说明限制或请求澄清]
  Schema -->|已召回表| Relation[5 TableRelation：读取真实 Schema、关系闭包与模型选表]
  Relation -->|可重试的组装错误| Relation
  Relation --> Feasible[6 FeasibilityAssessment：可行性]
  Feasible -->|信息不足或不可执行| Clarify
  Clarify --> End
  Feasible --> Plan[7 Planner：交付物与执行计划]
  Plan --> Execute[8 PlanExecutor：校验与调度]
  Execute -->|首次执行或重新规划| Coverage{需求范围和粒度覆盖?}
  Coverage -->|否：反馈遗漏后重规划| Plan
  Coverage -->|是：缓存已审计划| Execute
  Execute -->|计划不合法且可重试| Plan
  Execute -->|审核启用| Human[9 HumanFeedback：持久化断点]
  Human -->|批准| Execute
  Human -->|修改要求| Plan
  Execute -->|SQL 步骤| SQL[10 SqlGenerate：生成只读 SQL]
  SQL -->|仅生成 SQL| End
  SQL --> Semantic[11 SemanticConsistency：语义核对]
  Semantic -->|不一致且可重试| SQL
  Semantic --> Query[12 SqlExecute：执行与保存结果]
  Query -->|执行失败且可重试| SQL
  Query --> Present{计划与数据是否需要图表}
  Present -->|否| Execute
  Present -->|是| Chart[选择图型 / 校验字段 / 从结果构建图表]
  Chart -->|需要分析或报告| Analyze[同源图表统计分析]
  Chart -->|只需图表或不适合绘图| Execute
  Analyze --> Execute
  Execute -->|复杂计算且已有 SQL 结果| PyGen[13 PythonGenerate]
  PyGen --> PyRun[14 PythonExecute：Docker 沙箱]
  PyRun -->|失败且可重试| PyGen
  PyRun -->|成功或有明确降级说明| PyAnalyze[15 PythonAnalyze]
  PyAnalyze --> Execute
  Execute -->|需要文字分析或报告| Report[16 ReportGenerator]
  Report --> Publish[附上已验证图表与 SQL 证据]
  Publish --> End
  Execute -->|所需步骤已完成| End
  Execute -->|重试耗尽| Failed[明确失败，不伪装完成]
```

报告不再是每次查询的强制最后一步。正常问题的交付物由规划器推断：单值/明细可只返回结果；比较/趋势可生成图表；需要解释或决策时再生成文字分析与建议。

执行步骤和结果通过 SSE 展示；报告正文先完成生成及图表替换，再发布，避免模型临时生成的图表数值进入页面。暂停/恢复依赖持久化 checkpoint；用户停止与异常不是业务成功。

用户原话始终是权威输入，不被模型补全替代。通用数值约束检查会拒绝遗漏/改变显式年份、数量等的改写，并保存被拒绝的提案；该检查不是完整语义证明，SQL 语义核对仍同时看到原话和补全建议。必要图表失败会保存非空原因，运行标记为 `partial`，不冒充完整成功。

## 4. MCP 工具边界

```mermaid
flowchart LR
  Host[支持 MCP 的模型宿主] --> List[listAgentsToolCallback]
  Host --> Begin[begin_analysis]
  Begin --> Schema[get_schema]
  Schema --> NL[nl2SqlToolCallback 或宿主模型生成 SQL]
  NL --> Query[execute_query]
  Query -->|需要图表| Chart[create_chart]
  Query -->|需要计数/汇总/变化率| Calc[calculate_result]
  Calc --> Report
  Chart -->|需要分析| Analyze[analyze_result]
  Analyze -->|需要报告| Report[generate_report]
  Query -->|问题已回答| Finish[finish_analysis]
  Chart -->|问题已回答| Finish
  Analyze -->|问题已回答| Finish
  Host --> Full[analyze_request：委托完整规划流程]
  Host --> Trace[get_run_trace]
  Host --> Artifact[get_artifact]
```

| 工具 | 输入依据 | 返回内容 |
|---|---|---|
| `listAgentsToolCallback` | 状态、关键词 | 可用智能体 |
| `begin_analysis` | agentId、自然语言问题 | runId 与问题证据 |
| `get_schema` | agentId，可选 runId | 真实 Schema、内容版本，可保存快照 |
| `nl2SqlToolCallback` | 问题、agentId | SQL 文本，不执行查询 |
| `execute_query` | runId、只读 SQL | 结果、实际执行 SQL、截断和证据编号 |
| `create_chart` | 已存结果编号、图型和字段 | 可渲染 ECharts Option 与图表证据，不接收模型编造的数值数组 |
| `analyze_result` | 同源结果与图表编号 | 确定性统计事实、空值、范围说明；不自动证明因果 |
| `calculate_result` | 已存结果编号、分组/过滤/计算声明 | 本地精确计数、求和、均值、极值、两期差值和变化率；保留原结果引用 |
| `generate_report` | runId、分析重点 | 模型文字解读 + 已验证图表 + SQL 证据附录 |
| `finish_analysis` | runId | 无需报告时正常结束 |
| `analyze_request` | 自然语言、agentId | 与网页相同的完整规划流程 |
| `get_run_trace` | runId | 工件清单、步骤、耗时、依赖与状态 |
| `get_artifact` | runId、artifactId | 校验哈希后的 SQL/结果/图表/报告 |

默认 Streamable HTTP：`http://127.0.0.1:8066/mcp`。MCP 客户端必须提供受保护智能体的 `apiKey`，与网页使用同一验证逻辑。

已实现的图型：纵向柱状、横向条形、折线、饼图、散点、热力图、堆叠柱状。适用性和字段要校验；重复类别不静默合并，空值不替换为 0。返回的是图表配置，**不是图像文件，也不是截图视觉理解结果**。当前分析读取图表所对应的结构化数据和统计特征。

可选第三方：[AntV Chart MCP](https://github.com/antvis/mcp-server-chart)。官方默认请求在线绘图服务，可另行私有部署。本项目本轮不接入该远程服务、不发送工业行数据给它。将来接入也应由本项目根据结果编号取数，第三方只承担渲染。

## 5. 可追溯证据链

```mermaid
flowchart LR
  Q[问题] --> S[Schema 与知识快照]
  S --> P[执行计划]
  P --> G[模型生成 SQL]
  G --> X[实际执行 SQL / 数据源 / 上限]
  X --> R[返回行 / 截断 / 时间 / 内容哈希]
  R --> C[图表选型 / 字段 / Option]
  R --> A[描述统计]
  C --> A
  A --> D[报告与建议]
  C --> D
  X --> D
  R --> Calc[声明式计算 / 结果 / 原始行来源]
  Calc --> D
```

存储位置为 `DATAAGENT_VECTOR_FILE_PATH` 同目录的 `analysis-runs/<runId>/`，Compose 默认 `/app/data/analysis-runs/`，由 backend 与 MCP 共享卷持久化。JSON 内容使用 SHA-256、父工件编号、原子写入和跨进程锁；HTTP 与 MCP 读取时校验所属 Agent 和内容哈希。

查询证据中的 SQL 是带系统行数保护的实际执行语句，另存模型原始请求 SQL。系统多取一行判定截断，只展示设定上限内的行。查询中的业务 `LIMIT`、过滤条件和聚合范围仍决定数据含义；`truncated=false` 不代表覆盖整个数据库。

API：

- `GET /api/analysis/runs/{runId}?agentId=...`
- `GET /api/analysis/runs/{runId}/artifacts/{artifactId}?agentId=...`

证据链证明“本次输出依赖哪些已保存的输入”，不是不可伪造的数字签名，也不证明模型结论正确或数据库此刻未变化。文件可被系统管理员连同清单一起改写；生产级不可抵赖审计需要独立签名/审计存储。

## 6. 部署与支撑服务

```mermaid
flowchart TB
  Browser[浏览器 :3000] --> Nuxt[frontend]
  Nuxt --> Backend[backend :8065]
  MCPHost[MCP 客户端] --> MCP[mcp :8066]
  Backend --> Mgmt[(管理 MySQL：配置、会话、断点)]
  MCP --> Mgmt
  Backend --> Cache[(Redis：热缓存)]
  MCP --> Cache
  Backend --> Data[(共享 data 卷：向量、模型缓存、分析证据)]
  MCP --> Data
  Backend --> Uploads[(uploads 卷：上传文档)]
  MCP --> Uploads
  Backend --> Sources[(业务 MySQL / PostgreSQL / 工业 MySQL)]
  MCP --> Sources
  Backend --> LLM[模型供应商 / 本地 BGE Embedding]
  MCP --> LLM
  Backend --> Docker[受限 Python 沙箱容器]
  Backend -.可选.-> Tika[文档抽取]
  Backend -.可选.-> OTel[OpenTelemetry / Langfuse]
```

管理库与分析数据源不是同一个库。工业演示包保留独立 MySQL 容器与数据卷，不在应用镜像内重复打包大数据集。`.dockerignore` 排除 examples、work、data、uploads 与测试临时输出。

数据库账户仍应使用最小只读权限；SQL AST 和超时限制不能代替数据库权限。当前通用连接器的超时是应用侧等待期限，并不保证所有驱动能立即终止数据库端正在执行的查询。

## 7. 代码导航与验收

| 位置 | 职责 |
|---|---|
| `src/dataagent/workflow/engine.py` | 生命周期、SSE、断点、步骤证据 |
| `src/dataagent/workflow/nodes.py` | 16 个节点与模型规划调度 |
| `src/dataagent/analysis.py` | 网页与 MCP 共用的查询、图表、分析服务 |
| `src/dataagent/visualization.py` | 图型适用性、确定性 ECharts 与统计 |
| `src/dataagent/artifacts.py` | 证据存储、归属与完整性检查 |
| `src/dataagent/query_constraints.py` | 原始输入数值约束保护；不绑定具体年份、型号或问题 |
| `src/dataagent/mcp_server.py` | 13 个 MCP 工具 |
| `src/dataagent/calculations.py` | 通用声明式 Decimal 运算，不接受模型生成的答案数值或可执行代码 |
| `src/dataagent/reports.py` | 受证据约束的报告与安全导出 |
| `frontend/app/components/chat/ChatResultSet.vue` | 图表、表格、空值、截断与查询依据 |
| `examples/industrial-pdm/` | 原始/合成工业数据、生成器、知识与独立对照验证 |
| `tests/test_analysis_artifacts.py` | 证据、越权、7 种图型、MCP 工具链、自适应报告测试 |
| `tools/export_analysis_run.py` | 独立校验并导出证据；可附带本地前端资源以离线渲染 |

验收应分三层：离线回归检查契约和安全边界；真实模型测试检查自然语言生成 SQL；网页检查真实图表、来源展开和报告显示。测试中使用 mock 的结果不冒充真实大模型效果。运行记录与本轮验收结论见 `OPTIMIZATION_VERIFICATION.md`。
