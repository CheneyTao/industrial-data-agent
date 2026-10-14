"""Live acceptance against an already-running app; never imports/downloads source data."""

from __future__ import annotations

import argparse
import json
import time

import httpx

TERMINAL = {"COMPLETED", "PARTIAL", "FAILED", "TIMED_OUT", "CANCELLED", "WAITING"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8075")
    parser.add_argument("--agent-id", type=int)
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=75, trust_env=False) as client:

        def call(method: str, path: str, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        for route in [
            "filters",
            "overview",
            "failure-trend",
            "failure-by-model",
            "failure-types",
            "error-ranking",
            "machine-ranking",
            "maintenance-analysis",
            "machine/13/telemetry",
        ]:
            result = call("GET", f"/api/dashboard/{route}")
            assert result
        print(json.dumps({"pdm_endpoints": 9, "status": "passed"}), flush=True)
        for split, vehicles, readouts in [
            ("train", 23550, 1122452),
            ("validation", 5046, 196227),
            ("test", 5045, 198140),
        ]:
            result = call(
                "POST", "/api/bi/datasets/scania_component_x/dashboard", json={"filters": {"split": split}}
            )
            assert result["overview"]["vehicle_count"] == vehicles
            assert result["overview"]["readout_count"] == readouts
            print(
                json.dumps({"split": split, "vehicles": vehicles, "readouts": readouts, "status": "passed"}),
                flush=True,
            )
        if not args.agent_id:
            return
        agent = args.agent_id

        def launch(question, dataset, plan=None, **extra):
            return call(
                "POST",
                "/api/bi/analyze",
                json={
                    "agent_id": agent,
                    "question": question,
                    "dataset_id": dataset,
                    "plan": plan,
                    **extra,
                },
            )

        def wait(task):
            started = time.monotonic()
            while time.monotonic() - started < 240:
                result = call("GET", f"/api/bi/tasks/{task['task_id']}", params={"agent_id": agent})
                if result["status"] in TERMINAL:
                    return result
                time.sleep(0.5)
            raise TimeoutError(f"Task did not complete: {task['task_id']}")

        ranking = launch(
            "按已知标签等级列出验证集前5辆车，不解释为预测风险",
            "scania_component_x",
            {
                "dataset_id": "scania_component_x",
                "intent": "ranking",
                "metric_ids": ["known_label_level"],
                "dimension": "vehicle_id",
                "filters": {"split": "validation"},
                "limit": 5,
            },
        )
        report = launch(
            "报告训练集按 Spec_0 的车辆数；引用来源、筛选、SQL、数据限制",
            "scania_component_x",
            {
                "dataset_id": "scania_component_x",
                "intent": "comparison",
                "metric_ids": ["vehicle_count"],
                "dimension": "Spec_0",
                "filters": {"split": "train"},
            },
            report=True,
        )
        pdm = launch("按设备型号比较故障总次数，用柱状图展示", "industrial_pdm")
        for name, task in [("ranking", ranking), ("report", report), ("pdm_bi", pdm)]:
            result = wait(task)
            assert result["status"] == "COMPLETED", result["error"]
            assert result["result"]["result"]["data"]
            if name == "ranking":
                assert result["result"]["result"]["data"][0]["vehicle_id"] == 189
            if name == "report":
                assert "查询依据" in result["result"]["answer"]
            print(
                json.dumps({"check": name, "task_id": task["task_id"], "status": result["status"]}),
                flush=True,
            )
        followup = launch("看一下第一辆", None, conversation_id=ranking["conversation_id"])
        result = wait(followup)
        assert result["result"]["filters"]["vehicle_id"] == 189
        assert result["result"]["filters"]["split"] == "validation"
        print(json.dumps({"followup": "passed", "vehicle_id": 189}), flush=True)
        routed = call(
            "POST",
            "/api/bi/plan",
            json={
                "agent_id": agent,
                "question": "打开 SCANIA 验证集看板，车辆189，time_step 0到100，Spec_0=Cat0",
            },
        )
        assert routed["action"] == "open_dashboard" and routed["filters"]["vehicle_id"] == 189
        legacy = client.get(
            "/api/stream/search", params={"agentId": agent, "query": "打开 SCANIA 验证集看板"}
        )
        assert legacy.status_code == 200 and "open_dashboard" in legacy.text
        print(json.dumps({"nl_dashboard_and_existing_sse": "passed"}), flush=True)
        cancel = launch("解释匿名运行特征的相关性", "scania_component_x")
        call("POST", f"/api/bi/tasks/{cancel['task_id']}/cancel", params={"agent_id": agent})
        assert wait(cancel)["status"] == "CANCELLED"
        print(json.dumps({"cancel": "passed", "task_id": cancel["task_id"]}), flush=True)


if __name__ == "__main__":
    main()
