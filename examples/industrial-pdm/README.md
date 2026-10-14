# Industrial PdM DataAgent 演示数据源

这是一个面向 Spring AI Alibaba DataAgent 的工业预测性维护演示包，覆盖：

- 来源数据入库与多表关联
- 自然语言生成 SQL
- Python 异常检测与证据化根因候选
- RAG 业务知识检索
- HTML/JSON 数据分析报告
- Agent 执行审计

## 数据分层

### 来源层：`pdm_*`

`original/` 中的 5 个公开样例 CSV 按字节保留，生成器默认校验 SHA-256：

| 表 | 行数 | 含义 |
|---|---:|---|
| `pdm_machines` | 100 | 设备型号和机龄 |
| `pdm_telemetry` | 876,100 | 小时级电压、转速、压力和振动 |
| `pdm_errors` | 3,919 | 非停机错误事件 |
| `pdm_maint` | 3,286 | 维护和部件更换事件 |
| `pdm_failures` | 761 | 部件故障事件 |

“来源层”只表示本项目未修改文件，不表示记录已经核验为真实工厂现场数据。

### 合成演示层：`synthetic_*`

`scripts/generate_synthetic_data.py` 从来源层确定性生成：

| 表 | 行数 | 用途 |
|---|---:|---|
| `synthetic_fault_reports` | 761 | 故障前特征、规则候选、合成报告 |
| `synthetic_operator_logs` | 6,587 | 故障前遥测叙述、错误叙述、正常对照日志 |
| `synthetic_maintenance_notes` | 3,304 | 来源维护文字化及缺失维护的演示跟进建议 |
| `synthetic_work_orders` | 3,304 | 工时、停机、人工和备件成本 |
| `synthetic_parts_usage` | 5,417 | 工单备件消耗 |
| `synthetic_daily_operations` | 36,500 | 100 台设备 × 365 天的产量、停机、能耗和 OEE |

这些表全部带 `synthetic_text_flag=1` 或 `synthetic_record_flag=1`、生成器版本和来源关联字段。它们足以演示复杂 SQL、图表与报告，但不是现场日志、实际工单、财务成本、真实产量或已确认根因。

### 分析审计层

`analysis_runs`、`analysis_step_logs`、`detected_anomalies`、`root_cause_candidates` 和 `analysis_reports` 保存一次分析运行的范围、步骤、耗时、结果与限制。

### 型号语义层

公开样例中的 `model1`～`model4` 只是匿名型号类别，不是单台设备编号；单台设备由 `machineID` 标识。项目用 `dim_machine_models` 提供中性演示别名：

| 来源编码 | 业务显示名称 |
|---|---|
| `model1` | A 型机组 |
| `model2` | B 型机组 |
| `model3` | C 型机组 |
| `model4` | D 型机组 |

这些名称不表示真实厂商、规格或性能等级。查询和报告面向用户显示业务名，同时保留来源编码以便审计。

## 一键本地验证

```bash
python -m pip install -r requirements.txt
python scripts/run_all.py
```

流程依次执行：

1. 校验来源文件并生成全部 `synthetic_*` CSV。
2. 构建本地 SQLite 镜像和分析视图。
3. 生成带元数据的 RAG JSONL。
4. 执行数据血缘、数量、关联、成本平衡和 OEE 公式校验。
5. 执行 20 个自然语言问题对应的参考 SQL。
6. 运行设备 90 的事件级异常检测和根因候选分析。
7. 生成 HTML/JSON 报告。

输出统一放在可重建的 `generated/`：

- `generated/runtime/industrial_pdm.sqlite`
- `generated/rag/industrial_pdm_documents.jsonl`
- `generated/reports/validation_results.json`
- `generated/reports/nl2sql_verification.html`
- `generated/reports/industrial_pdm_analysis_report.html`
- `generated/logs/analysis.jsonl`

## MySQL 8 数据源

先生成合成数据，再启动只监听本机的 MySQL：

```bash
python scripts/generate_synthetic_data.py
copy .env.example .env
docker compose up -d
```

PowerShell 也可使用 `Copy-Item .env.example .env`。修改 `.env` 中的密码后再启动。默认连接参数：

- Host：`127.0.0.1`
- Port：`3307`
- Database：`industrial_pdm`
- User：`industrial`

初始化会执行 `mysql/schema.sql` 和 `mysql/docker_import.sql`。校验方法见 `mysql/README.md`。

## 接入 DataAgent

1. 在 DataAgent 新建 MySQL 数据源。宿主机运行后端时使用 `127.0.0.1:3307`；容器中的后端通常使用 `host.docker.internal:3307`。
2. 数据源参数示例见 `dataagent/datasource.example.json`。
3. 推荐表和视图见 `dataagent/recommended_tables.json`。默认分析优先选 `pdm_*`、`synthetic_work_orders`、`synthetic_parts_usage`、`synthetic_daily_operations` 及汇总视图。
4. 将 `dataagent/business_knowledge.md` 和 `knowledge/industrial_pdm_manual.md` 加入 Agent 知识，初始化所选表的 Schema。
5. 在对话框输入 `dataagent/nl2sql_cases.json` 中的问题。文件同时提供 SQLite/MySQL 参考 SQL，便于核对模型生成结果。

参考问题包括：

- “不同型号的每千观测小时故障率是多少？”
- “不同错误代码发生后 72 小时内出现故障的比例是多少？”
- “合成平均 OEE 最低的 10 台设备有哪些，同时显示来源故障数。”
- “按部件汇总合成工单数、停机小时、人工成本、备件成本和总成本。”
- “来源故障日与无故障日的合成平均停机、OEE 和报废率有什么差别？”

`nl2sql_cases.json` 是执行过的参考答案集，不替代 DataAgent 的自然语言生成 SQL 能力。

### 单问题端到端演示

将 DataAgent 或其他模型生成的只读 SQL 保存为 UTF-8 文件后，可执行：

```bash
python scripts/run_nl_sql_demo.py --sql-file generated_query.sql
```

不传 `--sql-file` 时，脚本会复现一个不在 20 条参考问题原句中的示例：比较各型号平均 OEE、来源故障次数和合成维修成本，并输出查询 CSV、JSON、HTML 分析建议，以及柱状小多图、气泡散点图、成本构成堆叠图、月度趋势折线图、型号×部件故障热力图。每次运行还会生成 `provenance_manifest.json`，用一个 `run_id` 串联问题、计划、Schema/数据版本、SQL、结果、图表、结论和报告；每个产物带 SHA-256、父级产物和证据等级。默认读取 SQLite；通过 `INDUSTRIAL_PDM_DB_URL` 可改为 MySQL。脚本只接受 `SELECT/WITH` 等只读 SQL。

## MCP/分析工具

```bash
python -m pip install -e .
python -m industrial_pdm.mcp_server
```

可用工具：`get_schema`、`query_database`、`calculate_kpi`、`detect_anomaly`、`get_related_events`、`root_cause_analysis`、`generate_report`、`get_run_trace`、`get_artifact`。生产连接通过 `INDUSTRIAL_PDM_DB_URL` 设置；未配置时使用生成的 SQLite 镜像。

## 项目结构

```text
original/                 未修改的来源 CSV，本地存在但默认不提交
synthetic/                可复现的合成 CSV，本地生成且默认不提交
config/                   数据源、指标和根因候选规则
dataagent/                DataAgent 数据源示例、业务知识和 NL2SQL 案例
knowledge/                RAG 证据边界与分析手册
mysql/                    MySQL Schema、容器导入和校验
scripts/                  生成、建库、验证、NL2SQL 校验和全流程脚本
src/industrial_pdm/       数据库适配、异常检测、归因、RAG、报告和 MCP
tests/                    数据与分析逻辑测试
generated/                SQLite、RAG、报告和审计日志；可随时重建
```

## 设计约束

- 合成表统一使用 `synthetic_` 前缀，不依赖含义模糊的单个标记字段。
- 根因评分只读取故障前来源遥测、错误、维护、机龄和规则；不会把合成参考标签反向作为证据。
- RAG 提供文字和规则，数值必须通过数据库实时计算。
- 日运营停机与工单停机是不同口径，报告不得直接相加。
- 来源 CSV 的公开页面将许可证标为 Unknown；工程默认不把大文件纳入 Git，分发前需自行确认授权。
