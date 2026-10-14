from __future__ import annotations

import json
from datetime import UTC, datetime
from time import perf_counter
from typing import TYPE_CHECKING, Any

import sqlglot
from sqlalchemy.ext.asyncio import AsyncSession
from sqlglot.optimizer.scope import traverse_scope

from dataagent.artifacts import ArtifactStore, fingerprint
from dataagent.config import Settings
from dataagent.connectors import (
    ConnectorService,
    database_and_schema,
    dialect_name,
    limited_sql,
    validate_read_only_sql,
)
from dataagent.errors import DataAgentError, UnsafeSqlError
from dataagent.prompts import render_prompt
from dataagent.visualization import ChartSpec, chart_from_result, result_profile

if TYPE_CHECKING:
    from dataagent.llm import LLMClient


class AnalysisService:
    """One implementation for workflow nodes and externally callable MCP tools."""

    def __init__(self, settings: Settings, connectors: ConnectorService | None = None):
        self.settings = settings
        self.connectors = connectors or ConnectorService(settings)
        self.store = ArtifactStore(settings)

    async def selected_tables(self, session: AsyncSession, agent_id: int) -> list[str]:
        return await self.connectors.selected_tables(session, agent_id)

    async def run_connectors(self, session: AsyncSession, agent_id: int, run_id: str | None):
        if not run_id:
            return self.connectors
        manifest = self.store.load(run_id, agent_id)
        for item in manifest["artifacts"]:
            if item["kind"] != "evidence":
                continue
            content = self.store.get(run_id, agent_id, item["artifactId"])["content"]
            if isinstance(content, dict) and content.get("attachments"):
                from dataagent.attachments import AttachmentService

                assets = content["attachments"]
                connectors, _ = await AttachmentService(self.settings).scoped_connectors(
                    session, agent_id, assets[0]["conversationId"], [a["id"] for a in assets]
                )
                return connectors
        return self.connectors

    async def schema(self, session: AsyncSession, agent_id: int, run_id: str | None = None) -> dict[str, Any]:
        connectors = await self.run_connectors(session, agent_id, run_id)
        tables = await connectors.selected_tables(session, agent_id)
        schema = await connectors.build_schema(session, agent_id, tables)
        content = schema.model_dump(by_alias=True, mode="json")
        return {"schema": content, "schemaVersion": fingerprint(content)}

    async def execute(
        self,
        session: AsyncSession,
        agent_id: int,
        run_id: str,
        sql: str,
    ) -> dict[str, Any]:
        self.store.load(run_id, agent_id)
        connectors = await self.run_connectors(session, agent_id, run_id)
        source = await connectors.active_source(session, agent_id)
        safe = validate_read_only_sql(sql, source)
        allowed = {name.casefold() for name in await connectors.selected_tables(session, agent_id)}
        expression = sqlglot.parse_one(safe, read=dialect_name(source))
        database, schema = database_and_schema(source)
        namespaces = {database.casefold(), (schema or "").casefold()}
        referenced = set()
        for scope in traverse_scope(expression):
            for table in scope.sources.values():
                if not isinstance(table, sqlglot.exp.Table):
                    continue  # Subqueries and CTEs are scopes, not physical tables.
                if table.name.casefold() not in allowed or table.catalog:
                    raise UnsafeSqlError(f"Table is not selected for this agent: {table.name}")
                if table.db and table.db.casefold() not in namespaces:
                    raise UnsafeSqlError("Cross-datasource/schema queries are not allowed")
                referenced.add(table.name)
        manifest = self.store.load(run_id, agent_id)
        parents = [
            item["artifactId"]
            for item in manifest["artifacts"]
            if item["kind"]
            in {
                "question",
                "plan",
                "schema",
                "generated_sql",
                "query_result",
            }
        ]
        sql_artifact = self.store.add(
            run_id,
            agent_id,
            "sql",
            {
                "sql": limited_sql(safe, source, self.settings.sql_max_rows + 1),
                "requestedSql": safe,
                "sourceId": source.id,
                "sourceName": source.name,
                "database": database,
                "schema": schema,
                "dialect": source.type,
                "tables": sorted(referenced),
            },
            parents,
        )
        executed_sql = self.store.get(run_id, agent_id, sql_artifact["artifactId"])["content"]["sql"]
        started = perf_counter()
        try:
            result = await connectors.for_source(source).execute(executed_sql)
        except Exception:
            self.store.step(
                run_id,
                agent_id,
                "execute_query",
                status="failed",
                duration_ms=(perf_counter() - started) * 1000,
                inputs=[sql_artifact["artifactId"]],
                outputs=[],
            )
            raise
        payload = result.as_dict()
        execution = {
            "executedAt": datetime.now(UTC).isoformat(),
            "elapsedMs": round((perf_counter() - started) * 1000, 2),
            "rowLimit": self.settings.sql_max_rows,
        }
        payload["execution"] = execution
        artifact = self.store.add(run_id, agent_id, "query_result", payload, [sql_artifact["artifactId"]])
        self.store.step(
            run_id,
            agent_id,
            "execute_query",
            status="succeeded",
            duration_ms=execution["elapsedMs"],
            inputs=[sql_artifact["artifactId"]],
            outputs=[artifact["artifactId"]],
        )
        payload["provenance"] = {
            "runId": run_id,
            "artifactId": artifact["artifactId"],
            "sqlArtifactId": sql_artifact["artifactId"],
            "sourceId": source.id,
            "sourceName": source.name,
            "sql": executed_sql,
            "sha256": artifact["sha256"],
            **execution,
        }
        return payload

    async def choose_chart(
        self,
        session: AsyncSession,
        llm: LLMClient,
        agent_id: int,
        run_id: str,
        result_id: str,
        question: str,
        instruction: str = "",
    ) -> ChartSpec:
        result = self.store.get(run_id, agent_id, result_id)
        if result["kind"] != "query_result":
            raise DataAgentError("Chart selection requires a stored query_result artifact")
        payload = result["content"]
        template = render_prompt(
            "data-view-analyze", format=json.dumps(ChartSpec.model_json_schema(), ensure_ascii=False)
        )
        system, _, _ = template.partition("=== 用户输入 ===")
        task = json.dumps(
            {
                "question": question,
                "queryTask": instruction,
                "columns": payload["column"],
                "profile": result_profile(payload),
                "sampleRows": payload["data"][:20],
            },
            ensure_ascii=False,
            default=str,
        )
        feedback = ""
        for attempt in range(self.settings.chart_selection_retries + 1):
            style = await llm.complete(
                session,
                system=system,
                user=task + feedback,
                purpose="data-view",
                json_model=ChartSpec,
            )
            assert isinstance(style, ChartSpec)
            error = None
            try:
                chart_from_result(payload, style)  # Validate against ALL stored rows, not just the sample.
            except ValueError as exc:
                error = str(exc)
            self.store.add(
                run_id,
                agent_id,
                "chart_decision",
                {
                    "attempt": attempt + 1,
                    "spec": style.model_dump(),
                    "status": "rejected" if error else "accepted",
                    "validationError": error,
                    "resultArtifactId": result_id,
                },
                [result_id],
            )
            if error is None:
                return style
            if attempt == self.settings.chart_selection_retries:
                raise DataAgentError(f"Chart selection failed data validation: {error}")
            feedback = (
                "\n上次提案未通过全量结果校验。请依据原问题和数据重新选型；不能擅自交换字段或改造数值。\n"
            )
            feedback += json.dumps(
                {"rejectedSpec": style.model_dump(), "validationError": error}, ensure_ascii=False
            )
        raise AssertionError("Chart selection budget must be non-negative")

    def chart(self, agent_id: int, run_id: str, result_id: str, spec: ChartSpec) -> dict[str, Any]:
        started = perf_counter()
        result = self.store.get(run_id, agent_id, result_id)
        if result["kind"] != "query_result":
            raise DataAgentError("Charts require a stored query_result artifact")
        option = chart_from_result(result["content"], spec)
        if option is None:
            raise DataAgentError("Table display does not create a chart artifact")
        content = {
            "spec": spec.model_dump(),
            "option": option,
            "resultArtifactId": result_id,
            "profile": result_profile(result["content"]),
        }
        decisions = [
            item["artifactId"]
            for item in self.store.load(run_id, agent_id)["artifacts"]
            if item["kind"] == "chart_decision" and result_id in item["parentIds"]
        ]
        artifact = self.store.add(run_id, agent_id, "chart", content, [result_id, *decisions])
        self.store.step(
            run_id,
            agent_id,
            "create_chart",
            status="succeeded",
            duration_ms=(perf_counter() - started) * 1000,
            inputs=[result_id],
            outputs=[artifact["artifactId"]],
        )
        return {**artifact, "content": content}

    def analyze(self, agent_id: int, run_id: str, result_id: str, chart_id: str) -> dict[str, Any]:
        started = perf_counter()
        result = self.store.get(run_id, agent_id, result_id)
        chart = self.store.get(run_id, agent_id, chart_id)
        if (
            result["kind"] != "query_result"
            or chart["kind"] != "chart"
            or result_id not in chart["parentIds"]
        ):
            raise DataAgentError("Analysis requires a chart derived from the same query result")
        content = {
            "profile": result_profile(result["content"]),
            "chartSpec": chart["content"]["spec"],
            "evidenceRefs": [result_id, chart_id],
            "interpretation": "Descriptive evidence only; causes and recommendations require validation.",
        }
        artifact = self.store.add(run_id, agent_id, "analysis", content, [result_id, chart_id])
        self.store.step(
            run_id,
            agent_id,
            "analyze_result",
            status="succeeded",
            duration_ms=(perf_counter() - started) * 1000,
            inputs=[result_id, chart_id],
            outputs=[artifact["artifactId"]],
        )
        return {**artifact, "content": content}
