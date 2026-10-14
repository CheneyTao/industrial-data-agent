# 工业设备数据分析 Dashboard

## 1. 架构与数据流

`/dashboard` 是现有 Nuxt 4/Vue 3 前端中的独立页面，不是第二个项目。固定 BI 数据流如下：

```text
DashboardView / 图表组件
  -> /api/dashboard/*
  -> DashboardService
  -> DashboardRepository
  -> 工业 PDM SQLite/MySQL（只读查询）
```

“AI 深度分析”复用现有 DataAgent，不新建 Agent 链路：

```text
当前筛选 + Dashboard 聚合证据
  -> POST /api/dashboard/analysis-context
  -> 现有 /api/stream/search SSE
  -> Planner -> SQL Tool / Python Tool / RAG / 归因分析 / Report Generator
  -> 现有会话与消息存储
```

固定看板即使没有配置 Agent 也能使用；AI 深度分析需要一个已发布、已绑定工业数据源的现有
DataAgent。页面优先使用 URL 中的 `agentId`，否则使用第一个已发布 Agent；没有可用 Agent 时会
给出明确提示，不会无限加载。

## 2. 数据源与配置

默认工业数据源：

```text
examples/industrial-pdm/generated/runtime/industrial_pdm.sqlite
```

已确认真实表为 `pdm_machines`、`pdm_telemetry`、`pdm_errors`、`pdm_maint`、
`pdm_failures`。默认文件包含 100 台设备、876,100 条遥测、3,919 条 Error、3,286 条维修和
761 条故障记录。

可在 `.env` 覆盖：

```dotenv
DATAAGENT_DASHBOARD_DATABASE_URL=sqlite:///./examples/industrial-pdm/generated/runtime/industrial_pdm.sqlite
DATAAGENT_DASHBOARD_QUERY_TIMEOUT_SECONDS=30
DATAAGENT_DASHBOARD_TELEMETRY_MAX_POINTS=500
```

也可使用 MySQL SQLAlchemy URL。Dashboard 连接是独立只读查询入口，不替换 DataAgent 管理库或
Agent 已绑定的数据源。

## 3. API

所有筛选型 GET 接口接受 `start_date`、`end_date`、`machine_id`、`model`、
`failure_type`。日期格式为 `YYYY-MM-DD`，结束日期按整日包含；非法日期、起止倒置和不存在的
设备会返回明确的 400/404。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/dashboard/filters` | 数据范围和 machine/model/failure 选项 |
| GET | `/api/dashboard/overview` | KPI |
| GET | `/api/dashboard/failure-trend` | 日/周/月故障趋势 |
| GET | `/api/dashboard/failure-by-model` | 型号故障率 |
| GET | `/api/dashboard/failure-types` | 故障类型分布 |
| GET | `/api/dashboard/error-ranking` | Error Top N |
| GET | `/api/dashboard/machine-ranking` | 高故障设备 Top N |
| GET | `/api/dashboard/machine/{machine_id}/telemetry` | 单机遥测趋势与数据库内采样 |
| GET | `/api/dashboard/maintenance-analysis` | 按维修类型统计维修前后 7 天同部件故障 |
| POST | `/api/dashboard/analysis-context` | 生成当前筛选的 Agent 问题和可追溯证据 |
| GET | `/api/dashboard/index-audit` | 只读查看相关表索引并给出建议 |

趋势接口额外接受 `granularity=day|week|month`；排行接口接受有上限的 `limit`；遥测接口接受
不超过服务器上限的 `max_points`。统计尽量在数据库中聚合，查询不使用 `SELECT *`。

## 4. 指标口径

| 指标 | 定义 |
|---|---|
| 设备总数 | 当前 model/machine 范围内的设备数 |
| 故障设备数 | 当前时间和筛选范围内至少发生一次目标故障的去重设备数 |
| 故障总次数 | 当前筛选范围内的故障事件数 |
| 当前故障率 | 故障设备数 / 当前设备总数 |
| Error 总次数 | 当前时间、设备、型号范围内的 Error 事件数；选定 failure 时仅统计发生过该故障的设备 |
| Maintenance 总次数 | 与 Error 相同范围口径的维修事件数 |
| 平均设备年龄 | 当前设备范围内 `age` 的平均值 |
| MTBF | 当前范围内遥测覆盖的设备运行小时数 / 故障事件数 |
| MTTR | 源数据没有维修完成时间或维修时长，因此明确返回不可计算，不伪造数值 |
| 最近 7 天故障 | 相对当前筛选结束日向前 7 天，不使用系统当前日期 |
| 最近 30 天故障率 | 相对当前筛选结束日向前 30 天的故障设备数 / 当前设备总数 |

首次进入页面默认显示数据最大日期之前 30 天；最近 7/30/90 天也都锚定真实数据最大日期，因此
历史数据集不会因为系统当前年份不同而显示空白。

## 5. 联动与容错

- 点击型号柱切换 model，点击故障环切换 failure_type，点击排行设备切换 machine_id。
- 顶部筛选和活动筛选标签均可清除；任何筛选变化都会重新请求全部聚合数据。
- 多接口并行加载使用部分成功策略：单个图表失败不会导致整页白屏。
- 空结果、API 错误、Agent 错误/超时、取消分析、遥测采样量都会在对应区域明确显示。
- 图表输入统一将缺失值转换为可控的空态，避免 `undefined`、`NaN` 和无限 loading。

## 6. 索引审计

当前 SQLite 已有 machine/datetime 相关索引，Dashboard 不会自动修改生产数据结构。对更大数据量，
可在备份并评估写入成本后由数据库管理员考虑：

- `pdm_failures(failure, datetime, machineID)`：故障类型 + 时间筛选；
- `pdm_machines(model, machineID)`：型号筛选和关联。

先调用 `GET /api/dashboard/index-audit` 查看部署环境的真实索引，再决定是否变更。未经确认不要
执行建索引或其他 DDL。

## 7. 本机启动

在克隆后的仓库根目录执行以下命令。前端命令在另一个终端从同一根目录进入 `frontend`。

后端：

```powershell
.\.venv\Scripts\Activate.ps1
dataagent
```

前端：

```powershell
Set-Location frontend
pnpm dev
```

访问 `http://127.0.0.1:3000/dashboard`。固定 BI 使用上述 Dashboard 数据库；AI 深度分析使用现有
模型配置和 Agent 绑定。仅做确定性链路演示时可在启动后端前设置
`$env:DATAAGENT_MODEL_MODE = "mock"`，真实分析不要使用 mock。

## 8. 验证

```powershell
.\.venv\Scripts\python.exe -m ruff check --no-cache src tests tools
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q

Set-Location frontend
pnpm test:unit
pnpm build
```

Dashboard 专项测试覆盖 KPI、全部图表接口、筛选联动、遥测采样、维修前后窗口、分析上下文、非法
日期、不存在设备、OpenAPI 契约和只读索引审计。
