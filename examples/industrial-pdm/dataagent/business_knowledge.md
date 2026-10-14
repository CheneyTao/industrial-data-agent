# Industrial PdM DataAgent 业务知识

## 证据等级

1. `pdm_*`：未修改的公开样例来源层，可用于描述数据集中观察到的设备、遥测、错误、维护和故障事件。
2. `synthetic_*`：由项目脚本确定性生成的演示衍生层，只能用于展示查询、分析、RAG 和报告能力。
3. `analysis_*`：Agent 运行、异常和根因候选的审计结果。

不得把“来源层”改写成“已核验的真实工厂记录”。不得把 `synthetic_*` 描述成现场日志、真实工单、财务成本或已确认根因。

## 业务术语映射

- 设备、机器：`machineID`
- 型号来源编码：`pdm_machines.model`，公开样例只提供 `model1`～`model4`
- 型号业务显示名：`dim_machine_models.display_name_zh`；分别显示为 A、B、C、D 型机组
- 机龄：`age`
- 电压：`volt`
- 转速：`rotate`
- 压力：`pressure`
- 振动：`vibration`
- 错误、告警：`pdm_errors`
- 维护、更换：`pdm_maint`
- 故障：`pdm_failures`
- 工单、维修成本、人工成本、备件成本：`synthetic_work_orders`
- 备件、物料消耗：`synthetic_parts_usage`
- 产量、良品、报废、停机、能耗、OEE：`synthetic_daily_operations`
- 根因、故障报告：`synthetic_fault_reports`，只能称为“合成规则候选”

## 关键关系

- 所有业务表通过 `machineID` 关联 `pdm_machines`。
- `pdm_machines.model` 和 `synthetic_daily_operations.model` 通过 `source_model_code` 关联 `dim_machine_models`。面向用户优先显示 `display_name_zh`，同时返回 `model_code` 以便溯源。
- `pdm_failures.failure` 与其他表的 `component` 表达部件。
- `synthetic_fault_reports` 通过 `(machineID, datetime, component)` 一一对应 `pdm_failures`。
- 合成日志、维修说明和工单通过 `linked_fault_id` 关联 `synthetic_fault_reports.fault_id`。
- 备件使用通过 `work_order_id` 关联工单。
- 日运营表通过 `(machineID, operation_date)` 表达设备日粒度。

## 查询规则

- 计数、求和、均值、趋势和排名必须由 SQL 计算，不能从 RAG 文本猜测。
- 遥测与事件关联默认使用同设备，并明确时间窗。
- 故障前证据必须满足 `e.datetime < f.datetime`；常用窗口为 72 小时。
- 每千观测小时故障率使用遥测点数近似设备运行小时，结论要说明局限。
- 查询合成表时，回答标题或正文必须出现“合成演示数据”。
- A/B/C/D 型机组只是中性演示别名，不代表真实厂商、规格、性能等级或现场命名；不得根据别名推断设备能力。
- 日运营停机和工单停机是两个不同口径，不能直接相加。

## 报告结构

报告按以下顺序输出：来源事实概览、SQL 计算结果、合成运营指标、异常发现、根因候选、限制与下一步验证。每个结论应标明来源表和时间范围。
