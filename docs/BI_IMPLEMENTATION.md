# 多数据集智能 BI 实施记录

## 2026-09-21 只读基线

- 应用代码位于克隆后的仓库根目录；下面的命令均从此目录执行。
- SCANIA 使用本机已有数据目录，通过 `DATAAGENT_SCANIA_DATA_DIR` 配置；原始数据不随源码发布。
- 已读取 DATA_INVENTORY、SCHEMA_REPORT、DATA_QUALITY_REPORT，并核对官方挑战说明 PDF。
- SCANIA：9 个 CSV、2 个 PDF，约 1.542 GiB；不下载、不移动、不修改原始数据。
- 训练 23,550 辆；验证 5,046 辆；测试 5,045 辆，按 split 隔离。
- 时间是相对 time_step，不是日历。105 个运行字段与 8 个 Spec 字段匿名，禁止猜物理含义。
- train_tte 首次维修事件 2,272；未发生维修/右删失 21,278。观察时长不能直接当作所有车辆的寿命。
- validation/test 的 class_label 是最后 readout 的已知标签。排序是回顾性标签优先级，不能称预测风险。
- 现有架构：FastAPI / SQLAlchemy / 自研 GraphEngine / ArtifactStore / Nuxt 4 / Vue 3 / Vuetify / ECharts 6。
- 无 LangGraph 依赖；本次扩展已有 GraphEngine，不做框架迁移。
- Redis 已有可选读穿缓存；持久化状态仍归管理数据库。
- 基线：后端 231 passed；前端 33 passed。
- 当前应用副本不含 .git；修改前将源码、测试、文档和前端代码保存到 `work/before-multidataset-bi-20260921-031917`。

## 对原方案的修正

1. Dataset Catalog 是现有连接器之上的语义层；Schema/Metric 来自验证后的定义。
2. 工业 PdM 继续调用 DashboardService/Repository，接口兼容。
3. 文件型 SCANIA 使用可选 `bi` 依赖 DuckDB 直接只读聚合 CSV；不建立第二份数据副本。
4. BI 是现有 GraphEngine 的扩展节点，复用 LLMClient、ArtifactStore、ChartSpec、Report Generator。
5. TaskManager 在原 GraphEngine 生命周期内维护状态；新增表仅为增量管理表，不改变来源库。
6. 任务取消以安全检查点与驱动 interrupt 为界。系统不终止 FastAPI 主进程。

## 阶段门槛

基线审计 → 语义层 → 数据访问与看板 → 自然语言路由与 BI 计划 → 任务生命周期 → 集成验收。
所有阶段使用同一项目，指标独立复核、原有测试和新契约测试共同验证。

## 当前交付与运行地址

- **完整预览：`http://127.0.0.1:3006/dashboard`，后端 8076，MCP `http://127.0.0.1:8077/mcp`。**
- 3005/8075 是第一阶段本机轻量验收实例，仅验证 BI；完整 Agent/RAG 请使用 3006/8076。
- 选择器支持 `industrial_pdm` / `scania_component_x`；也可直接使用 `/dashboard?dataset=scania_component_x`。
- 原 Docker 服务的 3000 / 8065 / 8066 端口未替换。新容器 `dataagent-bi-preview` / `dataagent-bi-mcp` 是同一项目的独立端口验收实例，不是另建应用。
- 管理数据库复用现有 Compose MySQL 的本机映射 13307。原模型配置保持不变。
- 新增 `analysis_task`、`bi_analysis_context` 两张管理表，使用现有 `create_schema` 初始化。
- 原始 CSV、PdM 来源库与索引未修改。轻量环境证据在 `work/bi-preview`；完整环境 HTTP/MCP 共用 `work/bi-preview-docker/analysis-runs`。
- 完整镜像 `dataagent-python-backend:bi-preview` 继承已存在的 `:local` GPU 运行时，验证基础依赖一致后仅增补 DuckDB/Pandas，并安装本次源码；`pip check` 通过。
- 完整环境以只读方式挂载原 BGE 缓存、向量数据、SCANIA CSV 和 PdM SQLite；模型设置为离线读取，没有下载模型。

## 架构与数据流

```text
Nuxt /dashboard
  ├─ PdM DashboardView → 原 /api/dashboard/* → DashboardService → DashboardRepository
  └─ ScaniaDashboard  → /api/bi/datasets/*   → DatasetCatalog → ScaniaComponentXAdapter
                                                         → DuckDB 只读 CSV 聚合

聊天 / BiWorkbench / MCP
  → 同一个 GraphEngine
  → IntentRecognition → BI_ROUTE → BI_EXECUTE → BI_INTERPRET
                           │            └→ 原 ReportGenerator（请求报告时）
                           ├→ 返回已有 Dashboard 路由与筛选
                           └→ 原 HumanFeedback 审核/恢复节点
  → 共用 LLMClient、AnalysisService、ChartSpec、ArtifactStore
  → 查询、SQL、指标计划、图表、解释、报告均关联 evidence ID 与 SHA-256

原深度分析请求 → 原 Planner / SQL / Python / RAG / 报告链路，保持独立可用
TaskManager → 同一管理数据库中的任务状态与连续追问上下文
```

没有引入第二套 Agent，也没有把自研 GraphEngine 替换成 LangGraph。
BI 分支使用已注册指标生成受控 SQL；不是将任意模型 SQL 直接送到文件系统执行。
原 PdM “AI 深度分析”按钮保留原链路，不能因为提示中出现 Dashboard 字样就误路由到 BI。
RAG/Python 仍由原分析链路按需使用，不宣称每次简单 BI 聚合都经过 RAG 或 Python。

### 语义与查询约束

- `definition()` 统一承载字段、角色、实体、时间、匿名化标记、关联与能力；`metrics()` 返回指标包。
- CSV 表头实际读取，并检查 split 间运行字段一致；规格/标签的实体键非空且唯一、覆盖相同。
- 不合并 train/validation/test 标签。不将一对多 readout 与车辆级指标混在同一聚合中。
- 用户值参数化；字段与表达式只从白名单注册项取用。无用户输入的自由 SQL 标识符。
- 运行统计在 DuckDB 聚合，只有小型车辆元数据临时物化；不把百万行数据拉到 Pandas。
- 最多 500 行动态结果/500 个单车曲线均值点；相关系数使用全量配对观测，散点图仅展示排序后的前 N 点。
- 相对时间分桶默认宽度 20；区间比较将明确给定的相对区间等分为前半段和后半段，不假称同比/环比。
- 维度/指标/非法日期/非法 split/不兼容标签等输入拒绝执行；无数据返回空集合或不可用值。
- `null` 不被包装成预测概率；JSON 不输出 NaN/Infinity。
- 固定看板支持规格、标签与实体点击联动；更换特征和维度重新查询，过期响应不能覆盖新筛选。

## 指标定义

### industrial_pdm：沿用原有指标包

| 指标 | 定义与边界 |
|---|---|
| 设备总数 | machine/model 范围内的设备数；故障类型不改变该基准分母 |
| 故障设备数 | 筛选期、所选 failure 类型中至少一次故障的独立设备数 |
| 故障总次数 | 来源故障事件行数；不与维修/Error 多表直接 JOIN 后重复计数 |
| 当前故障率 | 故障设备数 / 筛选设备总数 × 100%，不是事件发生强度 |
| Error / Maintenance | 筛选期内事件数；选择 failure 类型时使用相应故障设备群体 |
| 平均设备年龄 | 筛选设备元数据 age 均值，单位年 |
| MTBF | 原看板口径：遥测观测设备小时数 / 故障数；是观测量代理，不是停机修复后的严格运行时间 MTBF |
| MTTR | 不可计算：缺少修复完成时间或持续时长；返回 null 并说明原因 |
| 近7天 / 近30天 | 以当前结束日期为锚点，在全局时间范围内统计 |

“最近”快捷筛选相对于数据最新日期，而不是电脑今天，避免历史数据全部变空。
7 张原图表、日/周/月粒度、设备 telemetry 采样与维修前后同部件窗口均保留。
BI 注册 7 个 PdM 指标，复用原 service；单机排名以故障数为主排序，Error/维修可作为附加指标。
不支持的 BI 组合明确拒绝，复杂 SQL/Python 问题仍可使用原 DataAgent。

### scania_component_x：独立口径

| 指标 | 适用范围与定义 |
|---|---|
| vehicle_count | 当前 split/筛选下车辆数；含时间条件时，必须在该区间存在 readout |
| readout_count | 当前车辆与相对时间范围内观测行数 |
| repair_vehicle_count | 仅 train，首次 Component X 维修标记为 1 的车辆数 |
| repair_vehicle_share_pct | 上项 / 当前 train 车辆数 × 100%；不是预测概率 |
| censored_vehicle_count | 仅 train，观察期内未记录维修的右删失车辆数 |
| study_duration_mean | train 的事件/删失观察时长均值，不能当作寿命均值 |
| label_window_vehicle_count | validation/test 中最后 readout 的 class_label 为 1–4 的车辆数 |
| known_label_level | 仅逐车展示 validation/test 的已知标签等级，用于回顾性排序；不是预测风险 |
| mean_运行字段 | 105 个匿名字段的非缺失 readout 均值，按观测行加权，物理单位未知 |
| 缺失率 / 时间覆盖 | 当前字段的缺失观测数 / 观测数；相对时间 min/max |

标签 0 不在已标注临近窗口；1/2/3/4 分别表示事件前 48–24、24–12、12–6、6–0 个相对时间单位。
规格 `Spec_0` 至 `Spec_7` 匿名，不解释为车型、温度或压力。

## HTTP API

原 PdM GET 接口保持不变：

```text
/api/dashboard/filters
/api/dashboard/overview
/api/dashboard/failure-trend
/api/dashboard/failure-by-model
/api/dashboard/failure-types
/api/dashboard/error-ranking
/api/dashboard/machine-ranking
/api/dashboard/machine/{machine_id}/telemetry
/api/dashboard/maintenance-analysis
/api/dashboard/index-audit
POST /api/dashboard/analysis-context
```

公共筛选为 `start_date/end_date/machine_id/model/failure_type`；趋势接受 `granularity=day|week|month`。

新增 11 个 BI 接口：

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | /api/bi/datasets | 数据集与可用性 |
| GET | /api/bi/datasets/{id} | 字段、语义、限制 |
| GET | /api/bi/datasets/{id}/metrics | 已注册指标 |
| POST | /api/bi/datasets/{id}/dashboard | 数据集专属固定聚合 |
| POST | /api/bi/datasets/{id}/entities/{entity_id} | SCANIA 单车有界曲线 |
| POST | /api/bi/plan | 规划、澄清或已有看板路由 |
| POST | /api/bi/analyze | 202 返回 task_id / conversation_id |
| GET | /api/bi/tasks/{task_id} | 状态、进度、部分结果与证据 |
| GET | /api/bi/tasks/{task_id}/events | SSE；终态或等待审核时结束 |
| POST | /api/bi/tasks/{task_id}/cancel | 幂等协作式取消 |
| POST | /api/bi/tasks/{task_id}/resume | 批准计划或退回修改 |

分析与任务接口沿用现有 Agent API Key 授权。任务读取/取消/恢复必须指定 `agent_id`，且任务属于该 Agent。
看板和目录接口沿用项目原固定看板的访问策略；本次未引入新的租户/RBAC 模型。

SCANIA 看板请求示例：

```json
{"filters":{"split":"validation","min_time":10,"max_time":30,"specifications":{"Spec_0":"Cat0"}},"feature":"171_0","dimension":"Spec_0"}
```

分析请求示例（将 agent_id 改为已有可用 Agent）：

```json
{"agent_id":10,"dataset_id":"scania_component_x","question":"按 Spec_0 比较车辆数，用柱状图展示","filters":{"split":"train"},"report":false}
```

后续请求沿用 `conversation_id`。“看一下第一辆”使用上次真实排名的首个 ID，不重新猜测。
明确的看板导航支持例如“打开 SCANIA 验证集看板，车辆189，time_step 0到100，Spec_0=Cat0”。
若询问 SCANIA 最近7天或日历日期，会澄清而不是制造不存在的日期。
当前非空筛选不能被规划模型静默改写；要改变已选范围，应更新看板筛选。

## 任务取消、失败与恢复

- 状态含 PENDING / RUNNING / CANCELLING / CANCELLED / COMPLETED / FAILED，另有 WAITING / PARTIAL / TIMED_OUT。
- 数据库是任务状态唯一事实源。HTTP/MCP 不同进程可读同一任务并发起取消。
- 复用原项目可选 Redis 会话/检查点缓存；不为取消另建 Redis 与数据库双写的竞争状态。
- 节点执行期间检查取消和总期限；不启动后续节点。LLM await 可取消；已完成证据保留。
- DuckDB 中断专属连接；SQLite 使用 interrupt；MySQL 只对当前查询连接发 KILL QUERY，不杀服务器。
- 同步数据库 worker 结束并释放读事务连接后，才报告取消。连接建立失败/查询失败均有明确错误。
- 失联 worker 超过心跳容限后返回失败，不无限显示运行；服务重启不自动重放已完成计算。
- 人工审核复用原检查点；错误 Agent/会话不能恢复或破坏他人检查点。
- 浏览器刷新可按 sessionStorage 中的任务 ID 重新读取；API Key 不写入 storage。
- 页面连接中断只显示连接异常，不伪造服务端 FAILED；可以重新连接或请求停止。

## MCP

在原 MCP Server 中增加 5 个薄适配工具，不新增服务器：

`list_bi_datasets`、`get_bi_catalog`、`analyze_bi`、`get_bi_task`、`cancel_bi_task`。

原有 13 个工具全部保留，总计 18 个。目录、查询、图表、报告与 HTTP 使用相同 service / GraphEngine。
原 `calculate_result` 的通用统计能力也保留，无需重复实现一套 MCP 算子服务。

## 完整启动与复测命令

以下是在当前机器现有环境中的命令；不重建 Conda、不下载 SCANIA，不输出现有密钥。

### 推荐：完整 GPU / RAG / Agent 环境

前提是原 Compose 的 MySQL、Redis、模型缓存和本地基础镜像已经存在（本机已核实）。

```powershell
# 从克隆后的仓库根目录执行。

# 首次或源码更新时构建增量镜像。不会替换旧镜像 :local。
.\tools\start_bi_full.ps1 -Mode backend -Build
.\tools\start_bi_full.ps1 -Mode mcp

# 保持此前台终端运行，访问 http://127.0.0.1:3006/dashboard
.\tools\start_bi_full.ps1 -Mode frontend -Build
```

已有同名容器时脚本只启动它；若镜像更新而容器未更新，脚本明确停止并要求显式处理旧预览容器，不自动删除。
本次两个预览容器已运行，不需要重复创建。旧 3000/8065/8066 服务完全保留。
`docker-compose.bi-preview.yml` 仅用于 `run --no-deps`，**不能直接对该 override 执行 `up` 来替换原 backend**。
SCANIA 与 PdM 的主机数据路径在此 override 中明确列出；其他机器按真实路径调整只读挂载。
验收实例关闭自动重建的即时触发（恢复扫描延后），不用于长期开启索引维护或上传新知识；生产发布需使用正常可写的专属数据卷与原生命周期设置。

### 可选：轻量本机 BI 开发

轻量 `.venv` 没有 Torch/FlagEmbedding。其默认 database embedding 模式也没有已启用模型，
因此不能把这个环境的 RAG 配置错误当作原 Agent 回归；完整链路已在上面的 GPU 镜像中实测通过。

```powershell
# 从克隆后的仓库根目录执行。

# 已在本地 .venv 安装；其他同构环境首次启用时安装可选 BI 依赖。
.\.venv\Scripts\python.exe -m pip install -e '.[bi]'

# 终端一：复用当前已运行 Compose 管理库及原模型设置。
.\tools\start_bi_local.ps1 -Mode backend -UseComposeManagement

# 终端二：同一项目原 Nuxt 前端，代理到 8075。
.\tools\start_bi_local.ps1 -Mode frontend
```

SCANIA 数据路径由 `DATAAGENT_SCANIA_DATA_DIR` 指定，也可传 `-ScaniaPath`。
本机 13307 映射是现有 Compose 配置的约定；其他部署需按实际映射配置管理数据库 URL。
如不使用现有 Compose，先按原项目要求配置 `DATAAGENT_MANAGEMENT_DATABASE_URL` 和真实模型，再省略该开关。
源码开发可加 `-Reload`；正式验收建议不加，避免编辑触发重启取消正在运行的任务。
脚本不修改 `.env`，也不替换原容器。不要把它当作生产部署脚本。

```powershell
# 后端回归
.\.venv\Scripts\python.exe -m pytest -o addopts='' -q

# 有服务运行时：真实数据与模型验收。先将 DATAAGENT_AGENT_ID 设置为已配置的工业 Agent ID。
.\.venv\Scripts\python.exe tools\verify_bi_runtime.py --url http://127.0.0.1:8076 --agent-id $env:DATAAGENT_AGENT_ID

# 独立 Pandas 复核三组 CSV 的计数、标签、规格、时间与缺失率。
.\.venv\Scripts\python.exe tools\verify_scania_counts.py --url http://127.0.0.1:8076 --data-dir $env:DATAAGENT_SCANIA_DATA_DIR

Set-Location frontend
npm.cmd run test:unit
npm.cmd run build
```

真实模型验收会创建分析任务、证据，并使用当前模型额度；不向来源数据写入记录。
新机器完整依赖、数据库、原 Agent/RAG/沙箱启动仍以原项目 README / Compose 文档为准。
新容器镜像需要包含 `.[bi]` 依赖并只读挂载数据目录，不能仅拷贝源码后假称已部署新能力。

## 实际验收记录

### 自动化

- 基线后端 231 → 当前 **259 passed**（37.59 秒；含 28 项新增 BI 测试）。
- 前端 **36 passed / 11 files**；生产构建成功。
- 新增模块静态检查通过。
- 覆盖字段/指标白名单、split 语义、非法日期/范围、空数据、未知车辆、参数化值、粒度防重复计数、相关系数、区间比较、超时、中断、幂等取消、跨 TaskManager 读取、人工审核恢复、旧流程路由、MCP 调用与报告证据。

### 原始数据独立核算

| split | 车辆 | 观测行 | 相对时间覆盖 | 核对 |
|---|---:|---:|---|---|
| train | 23,550 | 1,122,452 | 0–507.4 | Pandas 与 API 一致 |
| validation | 5,046 | 196,227 | 0–456.6 | Pandas 与 API 一致 |
| test | 5,045 | 198,140 | 0–447.6 | Pandas 与 API 一致 |

规格计数、标签分布、所选字段缺失率亦逐项一致。
验证集 `Spec_0=Cat0, time_step=10..30`：3,843 辆、14,430 条观测，与独立分块统计一致。

### 真实模型与运行证据

- SCANIA 自动规划 Spec_0 对比：Cat0=19,221、Cat1=4,291、Cat2=38；生成图表和带引用解释。
  运行 `80b60bf8-8faa-4503-b199-5411cd4e3a87`。
- 已知标签等级前5车辆完成，首辆 189；运行 `565f2e6d-9b1a-4d1c-b96d-7a4e93d6d577`。
- 原 ReportGenerator 完整报告完成并包含查询依据；运行 `f5812e2e-bfea-40a8-ba10-24008e2accca`。
- PdM 自然语言指标/维度规划与真实查询完成；运行 `c8da0edf-300f-4e6d-a62d-5143ee2802be`。
- 连续追问继承 validation 与车辆189；自然语言看板路由、原 `/api/stream/search` SSE 路由通过。
- 取消任务实测终态 CANCELLED；MySQL 15秒长查询约 **0.360秒**取消，连接池 checkedout=0，随后 SELECT 成功。
- 浏览器验证：SCANIA 分组切换、车辆189联动及曲线、清除筛选；PdM KPI、设备13联动和周趋势；动态分析表格、图表及解释可见，未记录控制台错误。
- 完整 GPU 环境原 Agent 实测通过：Intent → EvidenceRecall → QueryEnhance → SchemaRecall → TableRelation → Feasibility → Planner → PlanExecutor → SQLGenerate → SemanticConsistency → SQLExecute，查得真实设备数 100；没有经过 BI_ROUTE。运行 `41592a92-3151-405a-89ac-2226c3ef5f23`。
- 新 MCP 服务实际注册 18 个工具；MCP 创建任务，HTTP 读取到 COMPLETED 与同一份证据，跨进程验证通过。运行 `c6b70e32-182f-4f9a-bea8-babb3e1e7e5c`。
- 验收中记录到过本机缺 embedding 模型、开发热重载中止任务；现已通过独立完整镜像和不热重载的生产前端消除交付环境差异，并保留原始记录，不冒充全部尝试首次成功。

### 索引审计（仅查询，没有创建）

已存在：设备唯一键 machineID；telemetry 的 datetime 索引与 (machineID, datetime) 唯一索引；
errors/maint/failures 均有 (machineID, datetime) 索引。
候选建议为 failures(failure, datetime, machineID)、machines(model, machineID)，须先按真实查询 EXPLAIN 验证收益并确认后再执行。

## 新增与修改文件

新增：

- `src/dataagent/bi/{__init__,contracts,catalog,scania,service,tasks}.py`
- `src/dataagent/api/bi.py`、`src/dataagent/query_execution.py`
- `frontend/app/components/dashboard/{DashboardView,ScaniaDashboard,BiWorkbench}.vue`
- `frontend/app/services/bi/index.ts`、`frontend/app/utils/{bi.ts,bi.test.ts}`
- `tests/test_bi.py`
- `tools/start_bi_local.ps1`、`tools/verify_bi_runtime.py`、`tools/verify_scania_counts.py`
- `tools/start_bi_full.ps1`、`docker/Dockerfile.bi-preview`、`docker-compose.bi-preview.yml`
- `docs/BI_IMPLEMENTATION.md`

修改：

- `src/dataagent/{app,config,schemas,mcp_server,llm,dashboard,connectors}.py`
- `src/dataagent/workflow/{engine,nodes}.py`
- `frontend/app/pages/dashboard.vue`：原内容拆至 DashboardView，增加数据集容器。
- `frontend/app/stores/chat.ts`：识别经过白名单验证的本地看板路由。
- `tests/{test_api_contract,test_architecture_contract,test_mcp,test_workflow}.py`
- `pyproject.toml`、`.env.example`：可选 BI 依赖与配置。

构建产物、pycache、测试缓存与运行证据不是业务源码变更清单的一部分。

## 明确限制与后续事项

1. 本次未训练 ML、预测风险/RUL/寿命或实现因果识别。匿名数据不支持真实物理原因确认。
2. BI Planner 是一个已注册指标组的一次分析，不是任意行业万能 Agent；复杂多阶段问题使用原 DataAgent。
3. 固定图表使用抽样/聚合点，不能将展示点数误认为原始观测总数。
4. 取消已经验证 DuckDB、SQLite、MySQL、模型等待；未实机验证 Oracle/JDBC/Hive 等驱动的取消行为，不能承诺它们均即时中止。
5. 暂无独立任务队列自动重试/迁移；进程意外中断可识别失败，但不自动重跑，以免重复消耗和覆盖证据。
6. 生产构建仍有已有依赖 deprecation、npm 配置及大 chunk 警告，不阻断构建；本次不升级无关依赖。
7. 完整交付预览运行于 3006/8076/8077，原 3000/8065/8066 容器没有替换；正式切换生产入口仍应按发布流程单独进行。

## 2026-09-21 侧栏收起问题修复

- 根因：768px 及以下的侧栏覆盖页头开关，抽屉内部没有关闭入口或遮罩；原展开状态还使用了不同的 1280px 判断。
- `BaseDrawer` 统一由同一断点决定布局与交互；新增抽屉内关闭按钮、遮罩点击和 Esc 关闭。隐藏导航及遮罩后的内容不可聚焦，收起后将焦点交回页头开关。
- `layouts/default.vue` 移除重复且不一致的窄屏判断，补充开关名称、展开状态和目标区域关联。组件 README 同步更新。
- 实际浏览器验证通过：原窗口三种关闭方式，1280px 桌面折叠/展开，两个菜单分组折叠/展开，768px/769px 断点切换，390px 小窗口展开/收起。验证后恢复原窗口尺寸，当前语义模型页面保持收起。
- 36 项前端测试通过；两个修改组件 ESLint 通过；生产构建成功。更新了 3006 前端实例，后端、数据库与原 3000 服务未改动。

## 2026-09-21 聊天输入区菜单与连接核对

- 截断根因：数据库/模型菜单原来绝对定位在输入框下方，祖先聊天区域的 `overflow: hidden` 会裁剪它；增加 z-index 无法解决祖先裁剪。
- 复用现有 Vuetify `VMenu` 浮层，优先向上展开并在视口内自动定位，空间不足时列表内部滚动。长名称换行，展示真实数据库名及已有说明；移除手写全局点击关闭监听，保留原切换业务逻辑。
- `ChatInputArea.vue`、`utils/datasourceSelection.ts` 与对应测试增量修改；数据库/模型未切换，未清理测试连接。
- 历史验收环境包含 `industrial_pdm` 工业公开样例与显式标注的合成演示层，以及多条指向 `product_db` 的 E2E 演示连接。连接记录不随源码发布，其他部署需自行配置。
- 历史 E2E SQLite 演示连接使用 Windows 路径，Docker `/tables` 返回空列表；宿主机可读不等于容器可读，必须按实际部署核对挂载和连接。该项失败记录保留，不计为容器数据源验收通过。
- SCANIA 仍通过 Dataset Catalog 读取现有 CSV，不属于此处 SQL 数据库连接列表。
- 40 项前端测试通过（新增 4 项显示元数据测试），修改文件 ESLint 与生产构建通过。浏览器确认 5 项完整显示、Esc 关闭、模型菜单正常；320px 高的窗口可内部滚动至最后一项，列表边界在视口内。已恢复原窗口尺寸；验收未切换 Agent 的数据源绑定。
