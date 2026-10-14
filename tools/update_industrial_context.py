"""Update only the demo's source-lineage metadata; never bind tables or change business rows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx
from provision_industrial_demo import LEGACY_SOURCE_TEXT, NAME, SOURCE_LINEAGE_TEXT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    with httpx.Client(base_url="http://127.0.0.1:8065", trust_env=False, timeout=120) as client:

        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        agents = request("GET", "/api/agent/list")
        matches = [agent for agent in agents if agent["name"] == NAME]
        if len(matches) != 1:
            raise RuntimeError("Expected exactly one bound industrial demo agent")
        agent = matches[0]
        agent_id = agent["id"]
        knowledge = request("GET", "/api/business-knowledge", params={"agentId": agent_id})["data"]
        terms = [item for item in knowledge if item["businessTerm"] == "工业数据来源与合成边界"]
        if len(terms) != 1:
            raise RuntimeError("Expected exactly one demo source-lineage term")
        term = terms[0]

        def revise(value):
            if LEGACY_SOURCE_TEXT in value:
                return value.replace(LEGACY_SOURCE_TEXT, SOURCE_LINEAGE_TEXT)
            if SOURCE_LINEAGE_TEXT in value:
                return value
            raise RuntimeError("Source metadata differs from known demo text; preserve custom content")

        revised_prompt = revise(agent.get("prompt") or "")
        revised_description = revise(term.get("description") or "")
        backup = {
            "agentId": agent_id,
            "agent": {key: agent.get(key) for key in ("name", "prompt")},
            "termId": term["id"],
            "term": {key: term.get(key) for key in ("businessTerm", "description", "synonyms")},
        }
        (args.output / "metadata_before.json").write_text(
            json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if args.apply:
            request("PUT", f"/api/agent/{agent_id}", json={"name": agent["name"], "prompt": revised_prompt})
            request(
                "PUT",
                f"/api/business-knowledge/{term['id']}",
                json={**backup["term"], "description": revised_description},
            )
            actual = request("GET", f"/api/agent/{agent_id}")
            fact = request("GET", f"/api/business-knowledge/{term['id']}")["data"]
            if actual["prompt"] != revised_prompt or fact["description"] != revised_description:
                raise RuntimeError("Updated metadata did not read back correctly")
        (args.output / "metadata_after.json").write_text(
            json.dumps(
                {
                    "agentId": agent_id,
                    "termId": term["id"],
                    "applied": args.apply,
                    "prompt": revised_prompt,
                    "description": revised_description,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "agentId": agent_id,
                    "termId": term["id"],
                    "applied": args.apply,
                    "businessRowsChanged": False,
                }
            )
        )


if __name__ == "__main__":
    main()
