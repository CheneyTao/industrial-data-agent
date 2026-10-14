from __future__ import annotations

import json

import pytest

from dataagent.artifacts import ArtifactStore
from dataagent.query_constraints import missing_numeric_anchors
from dataagent.schemas import GraphRequest
from dataagent.workflow.nodes import QueryEnhanceOutput


@pytest.mark.parametrize(
    ("original", "normalized", "rejected"),
    [
        ("比较2015年 OEE", "比较2025年 OEE", True),
        ("前5台，OEE低于80%", "前五台，OEE低于80%", True),
        ("前5台，OEE低于80%", "OEE低于80%的前5台设备", False),
        ("去年 OEE", "2025 年 OEE", False),
        ("至少1,000条", "至少1000条", False),
        ("温度低于-5度", "温度低于5度", True),
    ],
)
def test_rewrite_preserves_explicit_numbers(original, normalized, rejected):
    assert bool(missing_numeric_anchors(original, normalized)) is rejected


async def test_original_question_survives_model_rewrite_drift(app, client, tmp_path):
    # Reuse the standard management fixture; the drift is caught before data access.
    from dataagent.models import Agent

    async with app.state.database.session_factory() as session:
        agent = Agent(name="Constraint preservation", status="published")
        session.add(agent)
        await session.commit()
        agent_id = agent.id
    original = app.state.llm.complete

    async def complete(session, **kwargs):
        if kwargs["purpose"] == "query-enhance":
            return QueryEnhanceOutput(canonical_query="比较2025年 OEE", expanded_queries=["2025 OEE"])
        return await original(session, **kwargs)

    app.state.llm.complete = complete
    events = [
        e
        async for _, e in app.state.graph.stream(GraphRequest(agent_id=str(agent_id), query="比较2015年 OEE"))
    ]
    store = ArtifactStore(app.state.settings)
    trace = store.load(events[0].thread_id, agent_id)
    enhanced = next(a for a in trace["artifacts"] if a["kind"] == "enhanced_question")
    content = store.get(trace["runId"], agent_id, enhanced["artifactId"])["content"]
    assert content["canonical_query"] == "比较2015年 OEE"
    assert not content["rewrite_guard"]["accepted"]
    assert "2025" in json.dumps(content["rewrite_guard"]["proposal"])
