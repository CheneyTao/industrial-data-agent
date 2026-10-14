# 工业数据智能分析系统

面向工业数据查询依赖 SQL 专业能力、故障知识分散的问题，提供自然语言问数、知识问答、数据分析、图表与结构化报告。支持从问题到查询、执行、证据保存和报告复核的完整流程。

当前版本：**v0.2.1**。功能发布说明见 [v0.2.1](docs/releases/v0.2.1.md)。

## 核心功能

| 功能 | 当前实现 |
| --- | --- |
| Agent 工作流 | 自研异步状态图编排意图识别、知识检索、任务规划和 SQL/Python 执行；支持多轮上下文、人工审核及审核中断恢复 |
| RAG 检索 | 采集数据库 Schema 与业务知识；支持关键词、向量检索和 RRF 融合排序；可选 BGE-M3、Elasticsearch、Milvus |
| SQL 生成与纠错 | 基于 Schema 和业务上下文生成 SQL；执行前以 SQLGlot AST 校验只读语句；语义检查和执行错误反馈支持有限重试 |
| Python 分析 | 在每任务 Docker 沙箱中执行分析代码，限制资源、执行时间和输出大小；与 SQL 查询结果配合生成图表 |
| 工业数据看板 | PdM 设备、故障、型号与时间窗口筛选；可选 Scania CSV 只读分析，区分车辆与观测行粒度、数据分区与相对时间 |
| 可追溯报告 | 保存查询、指标定义、筛选范围、图表和证据编号；关键数值由程序读取或重算，保留并列排名、NULL 与截断范围 |
| 报告与诊断 | 指标字典、受控关联分析、周期报告、行动记录与实测证据复核；诊断质量门检查缺失字段与观察条件 |
| 工具与交互 | FastAPI REST/SSE、18 个 MCP 工具、Nuxt 管理界面、模型管理、数据源与知识配置、聊天附件 |

例如：“按型号比较本月故障设备占比，列出故障次数最高的设备并生成报告。”系统结合可用 Schema 或注册指标规划查询，执行后保存结果和筛选范围，生成图表与可核查的报告。报告中的统计关联与候选原因保留证据条件。

## 技术栈与来源

Python 3.11+、FastAPI、SQLAlchemy、SQLGlot、Pandas/DuckDB（可选）、MySQL/SQLite、Redis（可选）、Docker、MCP、Nuxt 4 / Vue 3 / ECharts。

工作流当前使用自研 **GraphEngine**，支持持久化 checkpoint 和人工审核恢复。LangGraph 迁移属于后续目标，当前版本没有该依赖。

本项目基于 Spring AI Alibaba DataAgent 做 Python 后端重构，保留并适配其 REST/SSE 契约与 Nuxt 前端，随后扩展工业分析、BI 与报告体系。冻结迁移基线为上游提交 `3fb7852f71b2f6bfda3de392f79207df7294199f`。来源与逐项映射见 [架构审计](docs/ORIGINAL_ARCHITECTURE_AUDIT.md)、[迁移映射](docs/MIGRATION_MAP.md)。沿用 Apache License 2.0，见 [LICENSE](LICENSE)。

## 本地启动

### 1. 后端与模拟模型

以下 PowerShell 命令适合先验证界面与接口，不调用真实模型：

```powershell
git clone https://github.com/CheneyTao/industrial-data-agent.git
Set-Location industrial-data-agent
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
$env:DATAAGENT_MODEL_MODE = "mock"
dataagent
```

后端接口：`http://127.0.0.1:8065/docs`；健康检查：`http://127.0.0.1:8065/health`。

### 2. 前端

另开终端，要求 Node.js 20+ 与 pnpm：

```powershell
Set-Location frontend
pnpm install --frozen-lockfile
pnpm dev
```

访问 `http://127.0.0.1:3000`。模拟模式用于流程演示，不能代表真实自然语言分析质量。

### 3. 真实模型、数据与分析

- 停止后端并移除 `DATAAGENT_MODEL_MODE` 临时变量，在模型管理中配置并激活 CHAT 与 EMBEDDING 模型，再启动后端。
- 根据需要安装 `connectors`、`vector`、`local-rag`、`bi`、`mcp` 等可选依赖，例如 `python -m pip install -e ".[connectors,bi,mcp]"`。
- 配置自己的数据源与知识文件，初始化 Schema 索引。外部模型密钥在本地配置，不写入仓库。
- Python 分析要求 Docker Engine 与可用沙箱镜像；没有 Docker 时不会回退到宿主机执行生成的代码。
- 仓库包含演示生成器、SQL 和知识模板，不附带完整 PdM/Scania 原始数据、模型权重、已生成数据库或运行证据。看板需要先准备数据，见 [工业演示数据说明](examples/industrial-pdm/README.md) 与 [BI 实施说明](docs/BI_IMPLEMENTATION.md)。

完整部署方式见 [启动文档](docs/STARTUP.md)。Docker Compose 包含 NVIDIA GPU、本地 BGE-M3 与 Docker socket 的配置，运行前需按目标机器配置驱动、模型与数据挂载。普通 CPU 机器可使用上面的本地后端与远程 EMBEDDING 模型。

## 功能边界

- 诊断最多给出有条件的统计关联或候选原因；现场工程根因确认需要额外证据。
- Scania 匿名特征不推断物理含义，相对时间不转成真实日历周期；合成演示数据不作为真实生产指标。
- 当前没有训练故障预测/RUL 模型，未实现 DoWhy/PyRCA 因果识别、邮件推送或独立人员身份认证。
- 聊天附件支持多格式解析与会话来源隔离；指定 Pandas 方法及图表业务粒度的完整真实模型验收仍未通过，见 [附件说明](docs/CHAT_ATTACHMENTS.md)。
- 自研工作流与实际代码一致；本次没有重新验证 Top-5 93% 和 SQL EX 97%，这些数字不作为本版本的性能承诺。

## 验证与文档

```powershell
python -m pytest
python -m ruff check src tests tools
Set-Location frontend
pnpm test:unit
pnpm build
```

真实模型、Docker、GPU 和外部数据库等集成验证需要对应服务与凭据。本次发布执行的检查记录在 [发布说明](docs/releases/v0.2.1.md)；既有验证和环境差异见 [VERIFICATION](docs/VERIFICATION.md) 与 [KNOWN_DIFFERENCES](docs/KNOWN_DIFFERENCES.md)。

- [Python 架构](docs/PYTHON_ARCHITECTURE.md)
- [API 与 SSE 兼容](docs/API_COMPATIBILITY.md)
- [工业看板](docs/DASHBOARD.md)
- [报告体系](docs/REPORT_SYSTEM_RELEASE.md)
- [聊天附件](docs/CHAT_ATTACHMENTS.md)
- [版本记录](CHANGELOG.md)
