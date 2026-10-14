from __future__ import annotations

from time import perf_counter
from typing import Any

from sqlalchemy import or_, select

from dataagent.analysis import AnalysisService
from dataagent.bi.contracts import BIRequest
from dataagent.calculations import CalculationSpec
from dataagent.calculations import calculate_result as compute_result
from dataagent.config import get_settings
from dataagent.database import get_database
from dataagent.errors import NotFoundError
from dataagent.llm import LLMClient
from dataagent.memory_cache import RedisMemoryCache
from dataagent.model_calls import capture_model_calls
from dataagent.models import Agent
from dataagent.reports import generate_grounded_report
from dataagent.schemas import GraphEventType, GraphRequest
from dataagent.security import verify_api_key
from dataagent.serialization import serialize
from dataagent.visualization import ChartSpec
from dataagent.workflow.engine import GraphEngine


def create_mcp_server() -> Any:
    try:
        from mcp.server import MCPServer
    except ImportError as exc:
        raise RuntimeError("Install the MCP extra: pip install -e '.[mcp]'") from exc

    settings = get_settings()
    database = get_database()
    memory_cache = RedisMemoryCache(settings)
    graph = GraphEngine(settings, database, memory_cache=memory_cache)
    analysis = AnalysisService(settings)
    mcp = MCPServer(
        name="spring-ai-alibaba-dataagent-python",
        instructions=(
            "Use begin_analysis, get_schema, execute_query, create_chart and analyze_result as needed. "
            "All charts and analysis must reference stored result artifact IDs. "
            "Use analyze_request for the complete workflow, and get_run_trace/get_artifact for evidence. "
            "Use generate_report for a narrative; otherwise finish_analysis after the needed outputs."
            "Use calculate_result for exact grouped counts, totals and period changes over saved rows."
        ),
    )

    @mcp.tool()
    async def listAgentsToolCallback(
        status: str | None = None, keyword: str | None = None
    ) -> list[dict[str, Any]]:
        """查询智能体列表，可按状态及名称、描述或标签关键词过滤。"""

        await memory_cache.start()
        await database.create_schema()
        async with database.session_factory() as session:
            statement = select(Agent)
            if status:
                statement = statement.where(Agent.status == status.lower())
            if keyword:
                like = f"%{keyword}%"
                statement = statement.where(
                    or_(Agent.name.ilike(like), Agent.description.ilike(like), Agent.tags.ilike(like))
                )
            rows = (await session.execute(statement.order_by(Agent.create_time.desc()))).scalars()
            return [serialize(item, exclude={"api_key"}) for item in rows]

    @mcp.tool()
    async def nl2SqlToolCallback(naturalQuery: str, agentId: str, apiKey: str | None = None) -> str:
        """使用指定智能体把自然语言数据查询转换为一条可执行的只读 SQL。"""

        if not agentId.strip() or not naturalQuery.strip():
            raise ValueError("agentId and naturalQuery cannot be empty")
        await memory_cache.start()
        await database.create_schema()
        await authorize(int(agentId), apiKey)
        return await graph.nl2sql(naturalQuery, agentId)

    async def authorize(agent_id: int, api_key: str | None) -> None:
        await database.create_schema()
        async with database.session_factory() as session:
            agent = await session.get(Agent, agent_id)
            if agent is None:
                raise ValueError("Agent not found")
            if agent.api_key_enabled and (not api_key or not verify_api_key(api_key, agent.api_key)):
                raise ValueError("Invalid agent API credentials")

    @mcp.tool()
    async def begin_analysis(agentId: int, question: str, apiKey: str | None = None) -> dict[str, Any]:
        """开始一次可追溯分析，返回 runId；用户问题决定后续工具选择。"""
        await authorize(agentId, apiKey)
        if not question.strip():
            raise ValueError("Question cannot be empty")
        return analysis.store.create(agentId, question)

    @mcp.tool()
    async def list_bi_datasets() -> list[dict[str, Any]]:
        """列出已注册工业 BI 数据集及可用性；不下载或导入数据。"""
        return graph.catalog.listing()

    @mcp.tool()
    async def get_bi_catalog(datasetId: str) -> dict[str, Any]:
        """获取真实字段语义、指标和数据限制。匿名字段不猜物理含义。"""
        adapter = graph.catalog.get(datasetId)
        return {
            "dataset": adapter.definition().model_dump(),
            "metrics": [m.model_dump() for m in adapter.metrics()],
        }

    @mcp.tool()
    async def analyze_bi(
        agentId: int,
        question: str,
        datasetId: str | None = None,
        filters: dict[str, Any] | None = None,
        conversationId: str | None = None,
        report: bool = False,
        apiKey: str | None = None,
    ) -> dict[str, Any]:
        """使用同一个 GraphEngine 异步执行 BI；返回 task_id，结果引用持久化证据。"""
        await authorize(agentId, apiKey)
        payload = BIRequest(
            agent_id=agentId,
            question=question,
            dataset_id=datasetId,
            filters=filters,
            conversation_id=conversationId,
            report=report,
        )
        return await graph.start_bi(
            GraphRequest(
                agent_id=str(agentId),
                query=question,
                conversation_id=conversationId,
                bi_request=payload.model_dump(mode="json"),
            )
        )

    @mcp.tool()
    async def get_bi_task(agentId: int, taskId: str, apiKey: str | None = None) -> dict[str, Any]:
        """读取 BI 任务状态、当前节点、结果与图表；支持 HTTP/MCP 跨进程读取。"""
        await authorize(agentId, apiKey)
        return await graph.tasks.get(taskId, agentId)

    @mcp.tool()
    async def cancel_bi_task(agentId: int, taskId: str, apiKey: str | None = None) -> dict[str, Any]:
        """请求协作式取消；保留已完成证据，不结束主进程。"""
        await authorize(agentId, apiKey)
        return await graph.tasks.cancel(taskId, agentId)

    @mcp.tool()
    async def get_schema(
        agentId: int,
        runId: str | None = None,
        apiKey: str | None = None,
    ) -> dict[str, Any]:
        """读取该智能体已选表的真实字段、关系和 Schema 内容版本，供生成 SQL。"""
        await authorize(agentId, apiKey)
        async with database.session_factory() as session:
            result = await analysis.schema(session, agentId, runId)
            if runId:
                result["artifact"] = analysis.store.add(runId, agentId, "schema", result)
            return result

    @mcp.tool()
    async def execute_query(
        agentId: int,
        runId: str,
        sql: str,
        apiKey: str | None = None,
    ) -> dict[str, Any]:
        """执行一条已选表范围内的只读 SQL；返回数据、截断状态和来源 artifactId，不自动绘图。"""
        await authorize(agentId, apiKey)
        async with database.session_factory() as session:
            return await analysis.execute(session, agentId, runId, sql)

    @mcp.tool()
    async def create_chart(
        agentId: int,
        runId: str,
        resultArtifactId: str,
        spec: ChartSpec,
        apiKey: str | None = None,
    ) -> dict[str, Any]:
        """用已有查询结果绘图：column/bar/line/pie/scatter/heatmap/stacked_bar。

        模型只选择类型和字段；数值从结果读取。返回可直接渲染的 ECharts Option 和证据编号。
        dimension 为类别/时间字段，measures 为数值指标，横条图也不交换两者。
        heatmap 的第二维度、长表折线的系列分组用 group_by；stacked_bar 需同单位可相加的指标。
        """
        await authorize(agentId, apiKey)
        return analysis.chart(agentId, runId, resultArtifactId, spec)

    @mcp.tool()
    async def analyze_result(
        agentId: int,
        runId: str,
        resultArtifactId: str,
        chartArtifactId: str,
        apiKey: str | None = None,
    ) -> dict[str, Any]:
        """分析同源图表和查询结果，返回统计事实、范围限制及证据引用；不将相关性当成因果。"""
        await authorize(agentId, apiKey)
        return analysis.analyze(agentId, runId, resultArtifactId, chartArtifactId)

    @mcp.tool()
    async def calculate_result(
        agentId: int,
        runId: str,
        spec: CalculationSpec,
        apiKey: str | None = None,
    ) -> dict[str, Any]:
        """从已有查询结果按声明分组计数/求和/均值/极值或比较两期；本地精确计算，保留来源，不调用模型。"""
        await authorize(agentId, apiKey)
        return compute_result(analysis.store, runId, agentId, spec)

    @mcp.tool()
    async def get_run_trace(agentId: int, runId: str, apiKey: str | None = None) -> dict[str, Any]:
        """读取运行步骤、问题、工件哈希和父子依赖；不重新查询数据库。"""
        await authorize(agentId, apiKey)
        return analysis.store.load(runId, agentId)

    @mcp.tool()
    async def get_artifact(
        agentId: int,
        runId: str,
        artifactId: str,
        apiKey: str | None = None,
    ) -> dict[str, Any]:
        """取回 SQL、结果、图表或报告，并校验内容哈希和智能体归属。"""
        await authorize(agentId, apiKey)
        return analysis.store.get(runId, agentId, artifactId)

    @mcp.tool()
    async def generate_report(
        agentId: int,
        runId: str,
        focus: str = "",
        apiKey: str | None = None,
    ) -> dict[str, Any]:
        """基于当前运行的真实结果、图表分析生成报告及有依据的建议；返回报告及证据编号。"""
        await authorize(agentId, apiKey)
        manifest = analysis.store.load(runId, agentId)
        results = [
            analysis.store.get(runId, agentId, item["artifactId"])
            for item in manifest["artifacts"]
            if item["kind"] in {"sql", "query_result", "chart", "analysis"}
        ]
        if not any(item["kind"] == "query_result" for item in results):
            raise ValueError("Execute a query before generating a report")
        chart_ids = {item["artifactId"] for item in results if item["kind"] == "chart"}
        analyzed = {ref for item in results if item["kind"] == "analysis" for ref in item["parentIds"]}
        if chart_ids - analyzed:
            raise ValueError("Call analyze_result for each chart before generating the report")
        inputs = [item["artifactId"] for item in manifest["artifacts"]]
        started = perf_counter()
        analysis.store.finish(runId, agentId, "running")
        try:
            with capture_model_calls(
                lambda call: analysis.store.add(
                    runId, agentId, "model_call", {"node": "MCP.generate_report", **call}, inputs
                )
            ):
                async with database.session_factory() as session:
                    agent = await session.get(Agent, agentId)
                    report = await generate_grounded_report(
                        analysis.store,
                        runId,
                        agentId,
                        session,
                        LLMClient(settings),
                        requirements=manifest["question"],
                        results=results,
                        summary=focus or "回答用户问题；建议须有证据，没有图表时明确分析限制。",
                        optimization=agent.prompt or "",
                    )
        except Exception as exc:
            error = analysis.store.add(
                runId,
                agentId,
                "node_error",
                {
                    "node": "MCP.generate_report",
                    "errorType": type(exc).__name__,
                    "message": str(exc),
                },
                inputs,
            )
            analysis.store.step(
                runId,
                agentId,
                "MCP.generate_report",
                status="failed",
                duration_ms=(perf_counter() - started) * 1000,
                inputs=inputs,
                outputs=[error["artifactId"]],
            )
            analysis.store.finish(runId, agentId, "failed")
            raise
        references = analysis.store.load(runId, agentId)["artifacts"]
        artifact = analysis.store.add(runId, agentId, "report", report, [r["artifactId"] for r in references])
        analysis.store.step(
            runId,
            agentId,
            "MCP.generate_report",
            status="succeeded",
            duration_ms=(perf_counter() - started) * 1000,
            inputs=inputs,
            outputs=[artifact["artifactId"]],
        )
        analysis.store.finish(runId, agentId, "succeeded")
        return {**artifact, "content": report}

    @mcp.tool()
    async def finish_analysis(agentId: int, runId: str, apiKey: str | None = None) -> dict[str, Any]:
        """当查询或绘图已回答问题、无需报告时结束运行，不调用模型或强制增加报告。"""
        await authorize(agentId, apiKey)
        manifest = analysis.store.load(runId, agentId)
        if not any(a["kind"] == "query_result" for a in manifest["artifacts"]):
            raise ValueError("No executed query result to finish")
        for item in manifest["artifacts"]:
            analysis.store.get(runId, agentId, item["artifactId"])
        analysis.store.finish(runId, agentId, "succeeded")
        return analysis.store.load(runId, agentId)

    @mcp.tool()
    async def analyze_request(agentId: int, question: str, apiKey: str | None = None) -> dict[str, Any]:
        """完整自然语言分析入口，运行与网页相同的动态规划流程；返回回答和持久化证据运行编号。"""
        await authorize(agentId, apiKey)
        await memory_cache.start()
        answer, run_id, status = "", "", "succeeded"
        async for event_name, event in graph.stream(GraphRequest(agent_id=str(agentId), query=question)):
            run_id = event.thread_id
            if event_name == "error":
                status, answer = "failed", event.text
            elif event.event_type == GraphEventType.FINAL_ANSWER:
                answer = event.text
            elif event.event_type == GraphEventType.HUMAN_FEEDBACK_REQUIRED:
                status, answer = "waiting", "此智能体需要人工复核，请在网页中批准该运行计划。"
        if run_id:
            try:
                status = analysis.store.load(run_id, agentId)["status"]
            except NotFoundError:
                # A failure before evidence initialization must not look successful.
                status = "failed"
        return {"runId": run_id, "status": status, "answer": answer}

    return mcp


def run() -> None:
    settings = get_settings()
    server = create_mcp_server()
    if settings.mcp_transport == "stdio":
        server.run(transport="stdio")
    elif settings.mcp_transport == "sse":
        server.run(
            transport="sse",
            host=settings.mcp_host,
            port=settings.mcp_port,
            sse_path=settings.mcp_sse_path,
            message_path=settings.mcp_message_path,
        )
    else:
        server.run(
            transport="streamable-http",
            host=settings.mcp_host,
            port=settings.mcp_port,
            streamable_http_path=settings.mcp_streamable_http_path,
            stateless_http=True,
            json_response=True,
        )


if __name__ == "__main__":
    run()
