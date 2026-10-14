# 聊天附件：实现与验收边界

## 用户流程

在聊天区域拖入文件，或点击“添加文件” → 本地解析 → 预览字段、类型、行数、缺失值与限制 → 输入问题并发送。

文件选择后不会自动调用模型。发送时选择的附件是本轮唯一查询来源；原数据库连接不改变、不混入。附件保留在当前会话的选择列表，后续提问继续使用；移除全部附件后恢复数据库问答。刷新后可以通过历史消息的“再次使用”重新选择已上传附件。移除选择不删除已留存的原文件和运行证据。

这次实现的是**对话附件分析**，不是任意文件自动写入业务库。追加、覆盖、upsert、发布到共享知识库或 Dashboard 数据集仍需单独设计映射和确认流程，本次没有启用这些写入。

## 架构与数据流

聊天 UI / 文件选择 → `AttachmentService`（Agent 鉴权、会话归属、大小与容量校验）→ 有时限的独立解析进程 → 原文件 + 识别结果 + 私有 SQLite 快照 → 当前 Graph 的只读数据源作用域 → 原有知识召回、Planner、SQL、Python/Pandas、Report Generator、持久化证据。

没有新增第二套 Agent 或 workflow 节点。SQL 查询复用 `ConnectorService`、`AnalysisService` 和既有只读/已选表/行数/超时限制。人工确认恢复从 checkpoint 重新绑定相同附件；请求参数不能换掉原作用域。MCP `get_schema(runId)`、`execute_query(runId)` 从相同运行证据恢复附件来源，不回退到 Agent 的原数据库。

文件放在 `vector_file_path` 同级的 `analysis-runs/_attachments/{agent}/{conversation}/{attachment}/`。不是头像上传目录，没有公开文件下载地址。原文件 SHA256 与派生快照 SHA256 保留；字段重命名有原名映射。只创建私有分析表，不创建/改变业务数据库表、索引、AgentDatasource、语义模型或共享 RAG 索引。现有预览容器的 analysis-runs 可写挂载已经覆盖该目录。

目前安全粒度沿用原应用的 Agent API Key + 会话归属，不是新增多租户用户权限体系。发布到公网仍需要现有应用的完整认证、网关请求体限制与存储运维策略。

## 支持能力与保守转换

| 输入 | 处理方式 | 明确限制 |
| --- | --- | --- |
| CSV / TSV | 编码识别、分隔符识别、完整读取并生成分析表 | 首行为表头；列数不一致直接报错；不丢弃坏行 |
| XLSX / XLSM | 多工作表分别建私有表，展示原始列名映射 | 不执行宏、公式和外部链接；公式保留为文本；首行缺表头需整理 |
| JSON / JSONL | 对象或对象数组转记录表 | 嵌套对象/数组保留 JSON 文本，不自动展开或猜业务粒度 |
| Parquet | 使用已有可选 DuckDB 依赖，投影明确字段 | 未安装 DuckDB 时 capabilities 不宣称支持 |
| PDF | 文字层提取，保留页码标记并按片段存储 | 不是表格识别；扫描件、加密文档和超 200 页文档拒绝 |
| DOCX | 段落和表格文字提取 | 非版式还原，图片、页眉等不是分析数据表 |
| TXT / MD / LOG / HTML / XML | 惰性文本证据，可按片段只读检索 | 不执行脚本、不渲染原始 HTML/XML、不解析为业务表 |

空单元格映射 NULL；仅全列一致且安全时推断整数/有限小数。前导零编号、超范围整数、混合类型和模糊日期保留文本。不会擅自填充缺失值、去重、推断单位或关联键。清洗规则的用户确认界面不在本次交付范围内。

图片/OCR、旧 `.xls`、压缩包、音视频、SQL dump、API/数据库导入尚未接入聊天附件；不会偷用外部 Tika 或联网转换服务。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/chat-attachments/capabilities` | 当前安装环境支持的后缀与上传限制 |
| POST | `/api/chat-attachments?agentId=…&conversationId=…` | multipart `file`，成功才返回 ready 附件 |
| GET | `/api/chat-attachments/{id}?agentId=…&conversationId=…` | 鉴权后的识别结果和有限预览 |
| POST | `/api/stream/search` | 原 GraphRequest 增加 `attachmentIds`；SSE 输出沿用旧协议 |

原 GET `/api/stream/search` 保持兼容。附件 ID 通过 POST body 发送，API Key 只走请求头，不进入 URL 或消息元数据。

默认单文件 10 MiB（复用 `file_max_size`），一轮最多 6 个；单文件最多 10 万行/128 列/100 万单元格/12 张表，所选附件合计最多 24 张表/256 列。文档最多 200 万字符。预览最多前 5 行，长单元格明确标注截断；数据库快照不按预览截断。

解析并发 2，繁忙返回 429；单解析 30 秒超时会终止子进程。Office 解压体积不超过 40 MiB；Linux 额外设置进程内存/CPU/输出体积上限，Windows 依赖进程截止时间与解析内容限额，不声称有同等 OS 内存隔离。

每会话最多 100 个原文件、250 MiB 原文件体积，以及 32 份组合快照、512 MiB 组合快照容量。失败上传不发布 manifest，清理本次未成功的临时文件。已成功的文件保留，不自动删除历史证据；应用整体的配额、保留周期与主动清理策略需要另行运维配置。

## 文件变更

- 新增后端：`src/dataagent/attachments.py`、`attachment_parser.py`、`api/attachments.py`。
- 后端接入：`app.py`、`schemas.py`、`api/graph.py`、`connectors.py`、`analysis.py`、`mcp_server.py`、`workflow/engine.py`、`workflow/nodes.py`。
- 新增前端：`app/services/attachments.ts`、`app/stores/chatAttachments.ts`、`app/components/chat/ChatAttachments.vue`。
- 前端接入：`pages/chat.vue`、`ChatInputArea.vue`、`ChatMessageList.vue`、`stores/chat.ts`、`services/graph/index.ts`。
- 验证：`tests/test_chat_attachments.py`、API 契约更新、附件前端测试与 Graph POST 测试、`tests/fixtures/chat-attachments/`。
- 独立验收入口：`tools/start_attachment_smoke.py`。它在现有项目内运行，只使用隔离管理库；不是新的产品项目或第二套 Agent。
- 联调修复：`reports.py`、`tests/test_report_grounding.py`；`workflowTimeline.ts` 及测试、`ChatWorkflowTimeline.vue`、`stores/chat.ts`；`sandbox.py`、`tests/test_sandbox_dependencies.py`、`tests/test_vector_documents_sandbox.py`。
- 方法与粒度合同：`prompts/planner.txt`、`python-generator.txt`、`python-analyze.txt`、`data-view-analyze.txt`、`report-generator-plain.txt`；`tests/test_workflow.py` 校验传递边界，`tests/test_architecture_contract.py` 仅更新这五份已审阅提示词的字节锁，保留其他合同约束。
- 运行配置：`docker/Dockerfile.sandbox-analytics`、`docker-compose.bi-preview.yml`、`tools/start_bi_full.ps1`；模型候选与兼容边界见 `docs/MODEL_FALLBACK.md`。

## 联调发现与根因修复

1. **报告证据编号误判**：旧规则把 SQL 表标识符中的 32 位十六进制片段误判为独立证据编号，导致有真实证据的报告被拒绝。规则改为完整标识符边界，而不是特判附件表前缀；不存在的独立证据编号仍然拒绝。私有附件来源的报告附录不再显示数据源编号 `None`。
2. **失败步骤错误显示完成**：已保存消息的 `completed` 只表示流已结束，不能证明每一步成功。时间线按每步错误及实际报告内容区分完成、失败与未完成，持久化错误信息，保留失败后重试的历史，不再对失败报告显示“已生成”。
3. **Pandas 原生扩展加载失败**：原基础沙箱无 Pandas/NumPy，临时安装到 `/tmp` 后，被原有 `noexec` 挂载规则禁止加载原生扩展。新增派生分析镜像固定 Pandas 2.3.3、NumPy 2.4.6、packaging 26.3；依赖安装器检查已安装版本及传递依赖，满足时复用只读镜像，避免重装到临时目录覆盖。继续保留 `noexec`、只读根目录、CPU/内存/PID 限制、cap-drop 与 no-new-privileges。并未通过放宽隔离绕过问题。
4. **执行方法与报告口径脱节**：旧提示把“优先标准库”放在用户指定库之前，报告只接收结果、看不到实际代码，还可能把失败尝试的实现当作成功实现。生成合同现在明确要求尊重安全的指定方法；报告将每次 Python 结果绑定到其执行前的代码证据，用 AST 静态提取声明导入，不执行代码或靠文本关键词推断。静态导入本身也不冒充运行分支/算法正确性的证明，缺少代码时明确未知。模型仍可能违反生成合同，最新完整验收尚未通过，不能把提示规则本身当作已成功执行。
5. **总计混入类别图**：最少步骤规则曾诱导将总计与分组 UNION 后一起绘图。规划和展示合同现在要求不同粒度分开查询/展示；展示不暗中删除结果行，混合粒度优先表格。报告证据明确图表没有业务行过滤，不能把含总计的图描述为仅有型号类别。此处仍有模型语义判断，不能宣称已形式化证明所有业务粒度。

这不等于支持任意原生依赖在线安装。未预装的原生库仍需要审查后加入派生运行镜像；纯 Python 临时依赖仍按原有受限路径处理。

## 启动和测试

在项目根目录执行后端测试：

```powershell
.venv\Scripts\python.exe -m pytest -o addopts='' -q
```

前端测试和完整构建：

```powershell
cd frontend
npm.cmd run test:unit
$env:NUXT_BACKEND_URL = 'http://127.0.0.1:8076'
npm.cmd run build
```

从克隆后的仓库根目录启动，SCANIA 数据路径通过 `DATAAGENT_SCANIA_DATA_DIR` 配置。启动沿用 `docs/BI_IMPLEMENTATION.md` 和 `tools/start_bi_full.ps1`。

当前版本已构建并部署。Docker 启动且原 MySQL/Redis 等依赖健康后，在项目根目录执行：

```powershell
.\tools\start_bi_full.ps1 -Mode backend
.\tools\start_bi_full.ps1 -Mode mcp
# 另开一个终端启动前端（如果 3006 已运行，不要重复启动）
.\tools\start_bi_full.ps1 -Mode frontend
```

打开 `http://127.0.0.1:3006/chat?agentId=10`；后端为 8076，MCP 为 8077。全新构建使用 `-Mode backend -Build`（先构建分析沙箱镜像），前端构建使用 `-Mode frontend -Build`。若预览容器镜像旧于已构建镜像，脚本会明确拒绝静默复用：先核实名称、停止并保留旧**预览**容器，再运行上述启动命令。不要对 override 执行 `compose up` 或 `--remove-orphans`，不要替换原 8065/8066 服务。

原始附件、快照及证据位于共享 `work/bi-preview-docker/analysis-runs`，容器更新不会抹掉已有记录。本次保留的旧预览容器后缀包括 `before-attachments-20260921`、`qa-before-citation-fix-20260921`、`before-analytics-runtime-20260922`、`before-method-evidence-20260922`，没有删除数据库卷或原服务。Docker 先前异常的 socket 目录也保留为 `run.backup-20260921-attachments` 和 `docker-secrets-engine.backup-20260921-attachments`，未重置 Docker 数据；重启后已实测引擎与依赖恢复。

不依赖 Docker 的隔离 UI 验收可开两个终端（模型为测试替身）：

```powershell
# 终端一，项目根目录
.venv\Scripts\python.exe tools/start_attachment_smoke.py
```

```powershell
# 终端二，项目 frontend 目录
$env:NUXT_BACKEND_URL = 'http://127.0.0.1:18076'
npm.cmd run dev -- --host 127.0.0.1 --port 13006
```

打开 `http://127.0.0.1:13006/chat?agentId=1`，使用测试文件 `tests/fixtures/chat-attachments/acceptance.csv`（10 行、amount 合计 550）和 `maintenance-note.md`，验证多文件、预览、移除、发送、历史重新选择以及窄屏布局。此入口不能证明真实模型能力，界面智能体名称和说明已明确标注“隔离测试”。

## 本次验证记录（2026-09-21 至 2026-09-22）

- 后端完整测试：311 passed（最终复测 102.23 秒，与真实模型联调并行时的耗时）。
- 前端单元测试：49 passed。
- 本次修改的后端文件 Ruff 检查通过；完整 Nuxt 生产构建通过。
- 额外全项目 TypeScript 静态检查未通过：12 处既有问题，涉及旧 service 的相对类型导入、业务知识类型重复导出、既有测试类型和 markdown token 空值。涉及文件与修改前备份一致；附件新增代码没有出现在报错列表中。未把构建成功等同于全项目类型检查成功，未扩大范围改动无关模块。

自动测试覆盖：多格式解析、保守类型推断、重复/中文表头映射、多工作表、公式不执行、坏格式/空文件/大小限制、跨 Agent/会话禁止访问、完整 SQL 聚合、只读/表白名单、MCP 同源恢复、人工确认恢复、解析超时杀进程、上传进度响应式更新、迟到响应不串会话、附件 POST 不泄漏 API Key。未把 mock 模型测试称为真实 AI 成功。

完整 Nuxt 生产构建通过；仍有既存 npm 配置、依赖 deprecation 和大 chunk 提醒，本次没有升级无关依赖。

### 已完成的真实环境验收

- 3006 生产构建页面已更新；8076/8077 健康。原 3000/8065/8066 及 MySQL、Redis、工业数据库继续运行；未重新下载 SCANIA。
- 浏览器实际文件选择上传 CSV + MD，通过多文件解析、字段类型/前 5 行预览、错误提示、移除、发送与历史“再次使用”。10 行 CSV 的 `001` 编号保留文本；全文统计不是前 5 行预览的统计。无效列宽 CSV 和不支持的 SQL 文件被拒绝，错误项移除后可正常发送。
- 390×844 窄屏验收通过；页面未横向溢出，预览表格内部滚动；左侧导航可打开并通过内部关闭按钮收回。未取得操作系统文件拖拽手势的实际自动化证据；拖入事件入口与附件选择共用同一上传逻辑，但不把文件选择测试等同于真实拖拽测试。
- 附件真实 AI 运行 `83a66b53-5132-4188-82e4-04c9d9d01bc4` 完成 SQL、Python 和可见报告：10 行、合计 550；model_A 5 行/240，model_B 5 行/310；报告注明两条维修记录不能证明因果。首个失败运行 `80b52e39-be5f-47f1-ba95-7d058d347409` 原样保留。成功运行中的首个 Pandas 尝试仍失败，模型改用标准库后完成，不能称该次 Pandas 一次通过。
- Pandas 修复后的独立重放 `47a0490d-96ae-4bc5-991b-2dc08dd27809` 成功：复用上述失败的**同一段生成代码与不可变 SQL 结果**，10/550 与分组汇总一致，实际导入 Pandas 2.3.3/NumPy 2.4.6，`/tmp` 仍为 noexec。这是运行时复测，不冒充新增的完整 AI 报告运行。
- MCP 注册 18 个工具；`get_schema` 和 `execute_query` 在附件 runId 下仅见两张私有表，查询工业表被拒绝；完整聚合返回 10/550。宿主 SDK 访问本机服务时只给测试子进程补充 localhost 的 NO_PROXY，未改系统代理。
- 无模型的原数据库只读回归 `30793e48-b665-4fd4-9fd3-586ca4a95359` 通过：数据源 11，100 台设备，四型号设备数 16/17/35/32。
- 新备用模型真实数据库问答 `a58e0e28-3957-4081-aef9-2861d1e72382` 成功。主模型的额度错误与 V4.1 Flash 的实际自动接替均留存 `model_call`，SQL 结果与独立只读基准一致，浏览器展开可见 100 台及四型号计数。途中有两次语义校验重试，不声称首次即通过。
- Dashboard 八个端点实测 HTTP 200；2015-12-03 至 2016-01-01 的设备数 100、故障设备 48、故障 56、Error 312、Maintenance 234；设备 13 telemetry 从 703 条采样返回 352 点。
- Dashboard 浏览器复测：折线/柱状/分布图真实显示；点击设备 13 联动为 1 台/3 次故障，清除恢复 100 台；点击 A 型机组柱子联动为 16 台/10 台故障/13 次故障、故障率 62.50%，未白屏或持续 loading。
- 新备用模型附件运行 `7f2c35c4-2662-4c2e-af87-198df6e126c0` 的流程虽结束且数值正确，人工验收发现代码实际为标准库而报告称 Pandas，图表还含总计行，**不作为完整交付通过记录**。原运行与报告保留，没有把失败证据改写为成功；上述第 4/5 项修复由该记录触发。
- 方法合同修复后的运行 `3a5ca671-f542-4700-9d8a-b70f5e698a4e` 已将总计与分组分开，柱状图只有 model_A/240、model_B/310；但非思考模型仍未实际使用指定库，并在报告计算中引用结果不存在的 `amount`/`model` 字段。报告被校验阻止发布，记录为 failed，不能称完整链路通过。随后单独验证关键任务的受限推理配置，不取消字段或报告校验。
- 新 Qwen 候选的完整运行 `5d00763f-1b33-4ae3-a0b4-fa9cbc4e9de6` 验证主模型额度耗尽后由 `qwen3.7-max-2026-06-08` 接替，SQL 10/550 与 A/240、B/310 正确，图表不含总计。该次仍使用标准库而报告声称 Pandas，核对模型漏检，因此独立验收不通过并将证据运行标为 `partial`。原报告、代码、模型批准与独立验收 `2c05e91236ab2917d4c4cb5d88dee646` 均保留；不能将此前 Pandas 关键步骤回放成功合并算作这次完整通过。新候选与配置详见 `MODEL_FALLBACK.md`。

### 仍需明确的边界

历史普通数据库自然语言回归覆盖了 `403 / AllocationQuota.FreeTierOnly` 与备用连接错误；不能把无 SQL 结果但流程结束的运行算为验收通过。后续验收先验证 DeepSeek，再验证 8 个 Qwen 型号，预览候选的实测结果与可用性边界见 `MODEL_FALLBACK.md`。

截至 2026-09-22 00:56 左右，受限推理测试 `fc5739a8-747a-416f-a6de-dce78d484d34` 在模型调用前失败：容器 `socket.getaddrinfo` 返回 `Temporary failure in name resolution`，Windows 同域名解析成功。未据此改动系统 DNS、代理或重启整个 Docker。未将未经验证的推理配置写入生效配置。预览后端/MCP 仍 healthy，本地数据查询与附件解析不依赖此模型域名。

2026-09-22 01:14 左右，网络可用时已重新执行完整链路，结果见 `5d00763f-1b33-4ae3-a0b4-fa9cbc4e9de6`；模型调用也仍记录过一次短时连接错误，未定位或修改网络根因。

**尚未完成**：新备用模型在完整附件报告中的稳定方法遵循与计算计划质量，尤其是“用户指定方法 → 实际执行 → 报告声称”的强制一致性。旧真实模型已成功生成过附件报告，且 Pandas 运行时及新 Max 关键步骤独立重放通过，但这些证据不能合并宣称“新备用模型的完整 SQL → Pandas → 报告已通过”。

业务库自动追加/覆盖、任意文件自动清洗、OCR、全局文件生命周期治理及公网多租户权限仍不在本次附件分析交付内。现有全项目 TypeScript 的 12 处错误也未消除。
