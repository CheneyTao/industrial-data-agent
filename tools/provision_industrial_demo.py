"""Idempotently bind the preserved industrial demo to the running Python app.

Run locally with Docker access. Creates a SELECT/SHOW VIEW-only database account;
credentials are sent only to the local management API and never printed.
"""

from __future__ import annotations

import json
import secrets
import subprocess

import httpx

BASE = "http://127.0.0.1:8065"
NAME = "工业设备运行分析助手"
SOURCE = "工业预测性维护演示数据"
LEGACY_SOURCE_TEXT = "synthetic_* 及其运营、OEE、成本、工单分析视图均为确定性生成的合成演示数据。"
SOURCE_LINEAGE_TEXT = (
    "synthetic_daily_operations 的运营/OEE/停机、synthetic_work_orders 的工单和成本是确定性合成演示数据。"
    "synthetic_fault_reports 是混合来源衍生表：其 machineID、datetime、component 事件键一一承接"
    "pdm_failures 的公开样例故障事件，不是另造一批故障；故障报告文字、严重度、根因候选等扩展字段"
    "由规则生成。故障计数描述公开样例事件，不能因为 synthetic 表名前缀把事件也称为随机合成。"
    "comp1-comp4 只是来源部件代码，不能根据根因标签推断部件的真实设备名称。"
)
PROMPT = (
    "你分析工业预测性维护演示数据。pdm_* 为未改写的公开样例，不是已经核验的真实工厂记录；"
    + SOURCE_LINEAGE_TEXT
    + "涉及这些指标时，回答开头必须声明‘合成演示数据，不代表真实生产绩效’，不得把假设当根因。"
    "model1-model4 是匿名设备型号类别，不是大模型，不是设备编号；用 dim_machine_models 的"
    "display_name_zh 或视图 model_name 显示 A-D 型机组，保留 model_code 原编码。"
    "单台设备由 machineID 标识。月度视图的 avg_oee_pct 是设备日 OEE 的均值，汇总时说明均值口径，"
    "不冒充按计划工时加权的工厂 OEE。时间范围根据已查询数据确定；不要把历史数据称为实时数据。"
    "仅基于实际 SQL 结果与图表分析给出结论；建议说明依据与待验证项。"
)
TABLES = [
    "pdm_machines",
    "pdm_telemetry",
    "pdm_errors",
    "pdm_maint",
    "pdm_failures",
    "dim_machine_models",
    "synthetic_fault_reports",
    "synthetic_operator_logs",
    "synthetic_maintenance_notes",
    "synthetic_work_orders",
    "synthetic_parts_usage",
    "synthetic_daily_operations",
    "v_machine_daily_kpi",
    "v_event_timeline",
    "v_failure_rca_evidence",
    "v_machine_reliability",
    "v_monthly_operations_kpi",
    "v_work_order_kpi",
]


def main() -> None:
    with httpx.Client(base_url=BASE, timeout=300, trust_env=False) as client:

        def api(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.is_error:
                raise RuntimeError(f"Local API failed: {method} {path}, status={response.status_code}")
            result = response.json()
            if isinstance(result, dict) and result.get("success") is False:
                raise RuntimeError(f"Local API operation failed: {path}")
            return result

        source = next((s for s in api("GET", "/api/datasource") if s["name"] == SOURCE), None)
        if source is None:
            username = "pdm_reader_" + secrets.token_hex(4)
            password = secrets.token_urlsafe(32)
            sql = (
                f"CREATE USER '{username}'@'%' IDENTIFIED BY '{password}';\n"
                f"GRANT SELECT, SHOW VIEW ON industrial_pdm.* TO '{username}'@'%';\n"
            )
            completed = subprocess.run(
                [
                    "docker",
                    "exec",
                    "-i",
                    "industrial-pdm-mysql",
                    "sh",
                    "-c",
                    'exec mysql --protocol=tcp -h 127.0.0.1 -uroot -p"$MYSQL_ROOT_PASSWORD"',
                ],
                input=sql,
                text=True,
                capture_output=True,
                timeout=30,
            )
            if completed.returncode:
                raise RuntimeError("Could not create the scoped read-only industrial account")
            source = api(
                "POST",
                "/api/datasource",
                json={
                    "name": SOURCE,
                    "type": "mysql",
                    "host": "host.docker.internal",
                    "port": 3307,
                    "databaseName": "industrial_pdm",
                    "username": username,
                    "password": password,
                    "status": "active",
                    "description": "公开样例与标注的合成演示数据；只读连接。",
                },
            )
        available = set(api("GET", f"/api/datasource/{source['id']}/tables"))
        if set(TABLES) - available:
            raise RuntimeError(f"Missing industrial tables: {sorted(set(TABLES) - available)}")
        agent = next((a for a in api("GET", "/api/agent/list") if a["name"] == NAME), None)
        if agent is None:
            agent = api(
                "POST",
                "/api/agent",
                json={
                    "name": NAME,
                    "description": "自然语言查询、同源图表与可追溯报告（公开样例 + 合成演示数据）",
                    "status": "published",
                    "category": "工业分析",
                    "tags": "工业,设备,OEE,演示,可追溯",
                    "prompt": PROMPT,
                },
            )
        agent_id = agent["id"]
        api("POST", f"/api/agent/{agent_id}/datasources/{source['id']}")
        api(
            "POST",
            f"/api/agent/{agent_id}/datasources/tables",
            json={"datasourceId": source["id"], "tables": TABLES},
        )
        indexed = api("POST", f"/api/agent/{agent_id}/datasources/init")
        known = {
            item["businessTerm"]
            for item in api(
                "GET",
                "/api/business-knowledge",
                params={"agentId": agent_id},
            )["data"]
        }
        facts = {
            "工业数据来源与合成边界": PROMPT,
            "设备综合效率 OEE": (
                "运营效率可用 OEE、可用率、性能率或质量率衡量，具体口径由用户问题决定。"
                "v_monthly_operations_kpi 提供 operation_month、model_name、model_code、avg_oee_pct、"
                "downtime_hours 等月度指标；synthetic_daily_operations 提供设备日级运营指标。"
                "这些运营指标都是合成演示数据。缺少原因证据时先呈现比较事实，改进建议作为待验证行动，不能确认故障因果。"
            ),
            "匿名型号与设备编号": (
                "model1、model2、model3、model4 分别显示为 A 型机组、B 型机组、C 型机组、D 型机组。"
                "dim_machine_models.source_model_code 对应原编码，display_name_zh 为展示名。"
                "这是中性演示别名，不表示真实品牌或性能等级。machineID 标识单台设备。"
            ),
        }
        for term, description in facts.items():
            if term not in known:
                api(
                    "POST",
                    "/api/business-knowledge",
                    json={
                        "agentId": agent_id,
                        "businessTerm": term,
                        "description": description,
                        "isRecall": 1,
                    },
                )
        print(
            json.dumps(
                {
                    "agentId": agent_id,
                    "sourceId": source["id"],
                    "selectedTables": len(TABLES),
                    "schemaIndex": indexed,
                    "name": NAME,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
