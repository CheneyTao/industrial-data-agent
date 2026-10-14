"""Export a verified live analysis run; never substitute reference SQL or mock output."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx
from offline_report import offline_report_html

from dataagent.artifacts import fingerprint
from dataagent.reports import report_html


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--agent-id", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--frontend-node-modules", type=Path, help="Bundle existing JS for offline HTML")
    args = parser.parse_args()
    prefix = f"http://127.0.0.1:8065/api/analysis/runs/{args.run_id}"
    with httpx.Client(timeout=30, trust_env=False) as client:
        response = client.get(prefix, params={"agentId": args.agent_id})
        response.raise_for_status()
        manifest = response.json()
        artifacts = []
        for item in manifest["artifacts"]:
            response = client.get(
                prefix + "/artifacts/" + item["artifactId"], params={"agentId": args.agent_id}
            )
            response.raise_for_status()
            artifact = response.json()
            if fingerprint(artifact["content"]) != artifact["sha256"]:
                raise RuntimeError("Evidence hash mismatch")
            artifacts.append(artifact)
    args.output.mkdir(parents=True, exist_ok=True)

    def render(content):
        if args.frontend_node_modules:
            return offline_report_html(content, args.output, args.frontend_node_modules)
        return report_html(content)

    (args.output / "evidence.json").write_text(
        json.dumps({"manifest": manifest, "artifacts": artifacts}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    reports = [a for a in artifacts if a["kind"] == "report"]
    if reports:
        latest_report = reports[-1]
        content = latest_report["content"]
        rejected = [
            item
            for item in artifacts
            if item["kind"] == "acceptance_review"
            and item["content"].get("reportId") == latest_report["artifactId"]
            and not item["content"].get("passed")
        ]
        if rejected:
            content = (
                "# 未通过独立验收的模型草稿\n\n以下原始报告保留用于排查，不能作为已核验的结论使用。"
                "原文和内容哈希保存在 evidence.json；拒绝原因：\n\n"
                + "\n".join(
                    f"- {item['content']['explanation']}（证据 {item['artifactId']}）" for item in rejected
                )
                + "\n\n---\n\n"
                + content
            )
        (args.output / "report.md").write_text(content, encoding="utf-8")
        (args.output / "report.html").write_text(render(content), encoding="utf-8")
    charts = [a for a in artifacts if a["kind"] == "chart"]
    if charts:
        sections = [
            "# 已验证图表证据\n\n这里仅展示保存的图表，不冒充模型生成的文字报告。",
            f"运行编号：`{args.run_id}`；完整来源和 SQL 见同目录 evidence.json。",
        ]
        for chart in charts:
            sections.append(f"## 图表 {chart['artifactId']}")
            sections.append("来源结果：" + ", ".join(chart["parentIds"]))
            sections.append(
                "```echarts\n" + json.dumps(chart["content"]["option"], ensure_ascii=False) + "\n```"
            )
        gallery = "\n\n".join(sections)
        (args.output / "charts.md").write_text(gallery, encoding="utf-8")
        (args.output / "charts.html").write_text(render(gallery), encoding="utf-8")
    print(
        json.dumps(
            {
                "runId": args.run_id,
                "status": manifest["status"],
                "verifiedArtifacts": len(artifacts),
                "hasReport": bool(reports),
                "output": str(args.output),
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
