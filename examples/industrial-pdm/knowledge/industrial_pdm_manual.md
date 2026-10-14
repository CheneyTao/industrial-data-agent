# Industrial PdM 分析与 RAG 手册

## 1. 数据证据边界

`pdm_machines`、`pdm_telemetry`、`pdm_errors`、`pdm_maint`、`pdm_failures` 是按字节保留的公开样例来源层。这里的“来源”只表示未被本项目改写，并不等于已经核验的真实工厂现场数据。

所有以 `synthetic_` 开头的表均由 `scripts/generate_synthetic_data.py` 确定性生成：

- `synthetic_fault_reports`、`synthetic_operator_logs`、`synthetic_maintenance_notes` 是合成文字与规则候选。
- `synthetic_work_orders`、`synthetic_parts_usage` 是合成业务工单、工时和成本。
- `synthetic_daily_operations` 是合成产量、停机、能耗与 OEE。

回答引用这些表时必须明确使用“合成”“演示”“规则候选”等措辞。不得把合成日志说成现场人员记录，不得把合成成本说成财务事实，也不得把规则根因说成已确认原因。

`pdm_machines.model` 的 `model1`～`model4` 是公开样例中的匿名类别编码，不是设备编号；单台设备由 `machineID` 标识。`dim_machine_models` 提供 A～D 型机组的中性演示别名。显示别名时应同时保留来源编码，且不得把别名解释为真实厂商或性能分级。

## 2. 推荐分析顺序

1. 用 SQL 从 `pdm_*` 确认设备、时间窗、故障、错误和维护来源事实。
2. 用 SQL 汇总 `synthetic_daily_operations` 与 `synthetic_work_orders`，并在标题或结论中标明“合成运营口径”。
3. 用 Python 对遥测执行趋势、稳健异常分数和贡献计算。
4. 从 RAG 检索同设备、相邻时间、同部件的合成叙述和规则说明。
5. 输出多个根因候选，逐项列出支持证据、反证、缺失证据和建议验证动作。
6. 报告分开列示“来源数据事实”“计算结果”“合成演示数据”“推断候选”。
7. 每个显示结论必须绑定 `run_id`、结果或图表产物 ID；完整链路通过产物父级、内容哈希和数据版本重放。

## 3. 时间约束

根因分析默认只使用事件时刻之前 72 小时的遥测与错误事件，以及之前 30 天的同部件维护。事件后的数据不能作为导致该事件的前置证据。合成故障前日志也必须严格早于其关联故障。

## 4. 指标口径

- 每千观测小时故障率：`pdm_failures` 数量除以 `pdm_telemetry` 点数再乘 1000。小时遥测点仅近似运行小时。
- OEE：合成可用率 × 合成性能率 × 合成质量率，仅用于展示多表 SQL 与报告能力。
- 维修总成本：`synthetic_work_orders.labor_cost + parts_cost`，不是财务值。
- 停机小时：日运营表与工单表有不同口径，查询和报告必须写清来源表。

## 5. 异常检测

在线异常检测对单台设备的 `volt`、`rotate`、`pressure`、`vibration` 使用前 24 个点的滚动中位数和 MAD。稳健 Z 分数绝对值达到 3.5 记为异常候选。MAD 为零时回退到滚动标准差。异常不等于故障，只是后续钻取入口。

## 6. 根因候选

根因结果必须是排序候选，不使用“确定由……导致”等表达。`confidence_score` 是规则证据分，不是统计概率。至少说明部件匹配、主导指标、异常强度、前置错误、近期维护、设备年龄、时间先后关系和局限性。

`synthetic_fault_reports.root_cause` 只用于演示与对照，不能反向参与候选评分，否则会形成循环论证。

## 7. RAG 使用规则

优先过滤 `machineID`、`event_time`、`component`、`linked_fault_id`、`doc_type`、`synthetic_text_flag`。RAG 提供文字解释和规则；计数、均值、趋势、成本和 OEE 必须由数据库实时计算。

## 8. 日志分层

`synthetic_operator_logs` 是设备事件的合成文字层；`analysis_runs`、`analysis_step_logs` 与 `generated/logs/analysis.jsonl` 是 Agent 执行审计层。后者记录任务步骤、工具、耗时和错误，不保存密码、API Key 或完整敏感提示词。
