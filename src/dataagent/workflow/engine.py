from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, select

from dataagent.artifacts import ArtifactStore
from dataagent.attachments import AttachmentService, attachment_evidence
from dataagent.bi.catalog import DatasetCatalog
from dataagent.bi.service import BIService, build_bi_nodes
from dataagent.bi.tasks import TaskManager
from dataagent.config import Settings, get_settings
from dataagent.connectors import ConnectorService
from dataagent.context_budget import token_count, truncate_to_tokens
from dataagent.dashboard import DashboardService
from dataagent.database import Database, get_database
from dataagent.errors import DataAgentError, NotFoundError
from dataagent.llm import LLMClient
from dataagent.memory_cache import CONVERSATION_CONTEXT, GRAPH_CHECKPOINT, RedisMemoryCache
from dataagent.model_calls import capture_model_calls
from dataagent.models import Agent, ConversationTurn, GraphCheckpoint
from dataagent.sandbox import DockerPythonSandbox
from dataagent.schemas import GraphEventType, GraphNodeResponse, GraphRequest, TextType
from dataagent.telemetry import Telemetry
from dataagent.vectorstore import RetrievalService
from dataagent.workflow.nodes import NodeContext, build_nodes
from dataagent.workflow.state import (
    END,
    FINAL_ANSWER,
    HUMAN_FEEDBACK_DATA,
    INPUT,
    INTENT_RECOGNITION_NODE,
    MULTI_TURN_CONTEXT,
    PLANNER_NODE_OUTPUT,
    STREAM_EVENT_COMPLETE,
    STREAM_EVENT_ERROR,
    initial_state,
)


@dataclass(slots=True)
class PreparedRun:
    request: GraphRequest
    thread_id: str
    conversation_id: str
    resuming: bool


@dataclass(slots=True)
class ActiveRun:
    conversation_id: str
    cancelled: asyncio.Event


class GraphEngine:
    """Explicit equivalent of Spring AI Alibaba StateGraph with durable HITL."""

    def __init__(
        self,
        settings: Settings | None = None,
        database: Database | None = None,
        llm: LLMClient | None = None,
        retrieval: RetrievalService | None = None,
        connectors: ConnectorService | None = None,
        sandbox: DockerPythonSandbox | None = None,
        telemetry: Telemetry | None = None,
        memory_cache: RedisMemoryCache | None = None,
        dashboard: DashboardService | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.database = database or get_database()
        self.llm = llm or LLMClient(self.settings)
        self.retrieval = retrieval or RetrievalService(self.settings, self.llm)
        self.connectors = connectors or ConnectorService(self.settings)
        self.sandbox = sandbox or DockerPythonSandbox(self.settings)
        self.telemetry = telemetry or getattr(self.llm, "telemetry", None) or Telemetry(self.settings)
        self.memory_cache = memory_cache or RedisMemoryCache(self.settings)
        self.nodes = build_nodes()
        self.attachments = AttachmentService(self.settings)
        self.dashboard = dashboard or DashboardService(self.settings)
        self.catalog = DatasetCatalog(self.settings, self.dashboard)
        self.bi = BIService(self.settings, self.database, self.catalog)
        self.tasks = TaskManager(self.database)
        self.nodes.update(build_bi_nodes(self.bi))
        self._background: set[asyncio.Task] = set()
        self.artifacts = ArtifactStore(self.settings)
        self._active: dict[str, ActiveRun] = {}
        self._active_lock = asyncio.Lock()

    @staticmethod
    def prepare(request: GraphRequest) -> PreparedRun:
        resuming = bool(request.human_feedback_content and request.human_feedback_content.strip())
        if resuming:
            if not request.thread_id:
                raise DataAgentError("Graph run ID is required when resuming human feedback")
            thread_id = request.thread_id
            conversation_id = request.conversation_id or thread_id
        else:
            conversation_id = request.conversation_id or request.thread_id or str(uuid4())
            # A normal request is always a new graph run, even if an old run ID is sent.
            thread_id = str(uuid4())
        if not request.agent_id or not request.query:
            raise DataAgentError("agentId and query are required")
        return PreparedRun(request, thread_id, conversation_id, resuming)

    async def stream(
        self, request: GraphRequest, *, prepared: PreparedRun | None = None
    ) -> AsyncIterator[tuple[str | None, GraphNodeResponse]]:
        prepared = prepared or self.prepare(request)
        await self.tasks.create(prepared.thread_id, int(request.agent_id), prepared.conversation_id)
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=256)
        cancelled = asyncio.Event()
        async with self._active_lock:
            if prepared.thread_id in self._active:
                raise DataAgentError(f"Graph run {prepared.thread_id} is already active", status_code=409)
            self._active[prepared.thread_id] = ActiveRun(prepared.conversation_id, cancelled)
        task = asyncio.create_task(self._execute(prepared, queue, cancelled))
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        except asyncio.CancelledError:
            cancelled.set()
            task.cancel()
            raise
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            async with self._active_lock:
                self._active.pop(prepared.thread_id, None)

    async def _execute(
        self,
        prepared: PreparedRun,
        queue: asyncio.Queue[Any],
        cancelled: asyncio.Event,
    ) -> None:
        awaiting_feedback = False
        graph_error: BaseException | None = None
        graph_cancelled = False
        checkpoint_authorized = not prepared.resuming
        trace, trace_token = self.telemetry.start_graph(
            name="graph-feedback" if prepared.resuming else "graph-stream",
            agent_id=prepared.request.agent_id,
            thread_id=prepared.thread_id,
            query=prepared.request.query,
            nl2sql_only=prepared.request.nl2sql_only,
            human_feedback=prepared.request.human_feedback,
        )
        final_output: Any = None
        outcome = "succeeded"
        evidence_initialized = False
        started = perf_counter()
        node_count = 0
        try:
            async with self.database.session_factory() as session:
                agent = await session.get(Agent, int(prepared.request.agent_id))
                if agent is None:
                    raise NotFoundError(f"Agent {prepared.request.agent_id} not found")
                if prepared.resuming:
                    task_state = await self.tasks.get(prepared.thread_id, int(prepared.request.agent_id))
                    if task_state["status"] not in {"WAITING", "PENDING"}:
                        raise DataAgentError("Only a waiting task can be resumed", status_code=409)
                    checkpoint = await self._load_checkpoint(session, prepared.thread_id)
                    if checkpoint is None:
                        raise NotFoundError(f"No resumable checkpoint for graph run {prepared.thread_id}")
                    if checkpoint["agent_id"] != str(prepared.request.agent_id):
                        raise DataAgentError("Checkpoint does not belong to this agent", status_code=403)
                    if (
                        prepared.request.conversation_id
                        and checkpoint["conversation_id"] != prepared.request.conversation_id
                    ):
                        raise DataAgentError(
                            "Checkpoint does not belong to this conversation", status_code=403
                        )
                    checkpoint_authorized = True
                    state = json.loads(checkpoint["state_json"])
                    state[HUMAN_FEEDBACK_DATA] = {
                        "feedback": not prepared.request.rejected_plan,
                        "feedback_content": prepared.request.human_feedback_content or "",
                    }
                    state[MULTI_TURN_CONTEXT] = await self._conversation_context(
                        session, prepared.conversation_id
                    )
                    node_name = checkpoint["next_node"]
                else:
                    context = await self._conversation_context(session, prepared.conversation_id)
                    review = bool(prepared.request.human_feedback) or bool(agent.human_review_enabled)
                    state = initial_state(
                        agent_id=prepared.request.agent_id,
                        query=prepared.request.query,
                        conversation_context=context,
                        thread_id=prepared.thread_id,
                        human_review=review,
                        nl2sql_only=prepared.request.nl2sql_only,
                    )
                    state["_CONVERSATION_ID"] = prepared.conversation_id
                    if prepared.request.attachment_ids:
                        if prepared.request.bi_request:
                            raise DataAgentError("不能同时选择固定 BI 数据集和聊天附件")
                        state["_ATTACHMENT_IDS"] = prepared.request.attachment_ids
                    if prepared.request.bi_request is not None:
                        state["_BI_REQUEST"] = {
                            **prepared.request.bi_request,
                            "agent_id": int(prepared.request.agent_id),
                            "conversation_id": prepared.conversation_id,
                        }
                    node_name = INTENT_RECOGNITION_NODE

                self.artifacts.create(
                    int(prepared.request.agent_id), prepared.request.query, prepared.thread_id
                )
                evidence_initialized = True
                connectors = self.connectors
                if state.get("_ATTACHMENT_IDS"):
                    # Checkpoint scope is authoritative when resuming human feedback.
                    connectors, assets = await self.attachments.scoped_connectors(
                        session,
                        int(prepared.request.agent_id),
                        state["_CONVERSATION_ID"],
                        state["_ATTACHMENT_IDS"],
                    )
                    state["_ATTACHMENT_EVIDENCE"] = attachment_evidence(assets)
                    self.artifacts.add(
                        prepared.thread_id,
                        int(prepared.request.agent_id),
                        "evidence",
                        {"attachments": assets, "scope": "conversation-only; not the active database"},
                    )
                if state.get(MULTI_TURN_CONTEXT):
                    self.artifacts.add(
                        prepared.thread_id,
                        int(prepared.request.agent_id),
                        "context",
                        {
                            "conversationId": prepared.conversation_id,
                            "historyContext": state[MULTI_TURN_CONTEXT],
                        },
                    )
                node_context = NodeContext(
                    session=session,
                    state=state,
                    settings=self.settings,
                    llm=self.llm,
                    retrieval=self.retrieval,
                    connectors=connectors,
                    sandbox=self.sandbox,
                    queue=queue,
                    bi=self.bi,
                )
                while node_name != END:
                    if cancelled.is_set():
                        raise asyncio.CancelledError
                    node = self.nodes.get(node_name)
                    if node is None:
                        raise DataAgentError(f"Graph routed to unknown node: {node_name}")
                    node_context.begin_node(node_name)
                    node_count += 1
                    await self.tasks.running(prepared.thread_id, node_name, min(node_count * 10, 90))
                    before = self.artifacts.load(prepared.thread_id, node_context.agent_id)["artifacts"]
                    input_ids = [item["artifactId"] for item in before]
                    node_started = perf_counter()
                    node_trace, node_trace_token = self.telemetry.start_node(
                        node_name, node_context.attempts[node_name], state
                    )
                    try:
                        with capture_model_calls(
                            lambda call, node=node_name, parents=input_ids: self.artifacts.add(
                                prepared.thread_id,
                                node_context.agent_id,
                                "model_call",
                                {"node": node, **call},
                                parents,
                            )
                        ):
                            result = await self._run_node(node, node_context, cancelled, started)
                    except BaseException as exc:
                        self.telemetry.finish_node(node_trace, node_trace_token, output=state, error=exc)
                        self.artifacts.add(
                            prepared.thread_id,
                            node_context.agent_id,
                            "node_error",
                            {
                                "node": node_name,
                                "errorType": type(exc).__name__,
                                "message": str(exc) or type(exc).__name__,
                            },
                            input_ids,
                        )
                        after = self.artifacts.load(prepared.thread_id, node_context.agent_id)["artifacts"]
                        self.artifacts.step(
                            prepared.thread_id,
                            node_context.agent_id,
                            node_name,
                            status="failed",
                            duration_ms=(perf_counter() - node_started) * 1000,
                            inputs=input_ids,
                            outputs=[a["artifactId"] for a in after if a["artifactId"] not in input_ids],
                        )
                        raise
                    else:
                        self.telemetry.finish_node(node_trace, node_trace_token, output=state)
                        snapshot_keys = {
                            "QUERY_ENHANCE_NODE": ("enhanced_question", "QUERY_ENHANCE_NODE_OUTPUT"),
                            "FEASIBILITY_ASSESSMENT_NODE": (
                                "feasibility",
                                "FEASIBILITY_ASSESSMENT_NODE_OUTPUT",
                            ),
                            "EVIDENCE_RECALL_NODE": ("evidence", "EVIDENCE"),
                            "TABLE_RELATION_NODE": ("schema", "TABLE_RELATION_OUTPUT"),
                            "PLANNER_NODE": ("plan", "PLANNER_NODE_OUTPUT"),
                            "SQL_GENERATE_NODE": ("generated_sql", "SQL_GENERATE_OUTPUT"),
                            "PYTHON_GENERATE_NODE": ("python_code", "PYTHON_GENERATE_NODE_OUTPUT"),
                            "PYTHON_EXECUTE_NODE": ("python_result", "PYTHON_EXECUTE_NODE_OUTPUT"),
                            "PYTHON_ANALYZE_NODE": ("python_analysis", "PYTHON_ANALYSIS_NODE_OUTPUT"),
                            "REPORT_GENERATOR_NODE": ("report", FINAL_ANSWER),
                        }
                        if node_name in snapshot_keys:
                            kind, key = snapshot_keys[node_name]
                            if state.get(key) is not None:
                                parents = input_ids
                                if kind == "report":
                                    parents = [
                                        item["artifactId"]
                                        for item in self.artifacts.load(
                                            prepared.thread_id, node_context.agent_id
                                        )["artifacts"]
                                    ]
                                self.artifacts.add(
                                    prepared.thread_id,
                                    node_context.agent_id,
                                    kind,
                                    state[key],
                                    parents,
                                )
                        after = self.artifacts.load(prepared.thread_id, node_context.agent_id)["artifacts"]
                        self.artifacts.step(
                            prepared.thread_id,
                            node_context.agent_id,
                            node_name,
                            status="waiting" if result.waiting else "succeeded",
                            duration_ms=(perf_counter() - node_started) * 1000,
                            inputs=input_ids,
                            outputs=[a["artifactId"] for a in after if a["artifactId"] not in input_ids],
                        )
                    if cancelled.is_set():
                        raise asyncio.CancelledError
                    if state.pop("_REJECTED_TURN", False):
                        await self._restart_last_turn(session, prepared.conversation_id)
                    if result.waiting:
                        if cancelled.is_set():
                            raise asyncio.CancelledError
                        await self._record_turn(session, prepared.conversation_id, state)
                        await self._save_checkpoint(
                            session,
                            prepared.thread_id,
                            prepared.conversation_id,
                            prepared.request.agent_id,
                            result.next_node,
                            state,
                        )
                        awaiting_feedback = True
                        break
                    node_name = result.next_node

                if not awaiting_feedback:
                    await self._record_turn(session, prepared.conversation_id, state)
                    await session.execute(
                        delete(GraphCheckpoint).where(GraphCheckpoint.thread_id == prepared.thread_id)
                    )
                    await session.commit()
                    await self.memory_cache.invalidate(GRAPH_CHECKPOINT, prepared.thread_id)
                    final = str(state.get(FINAL_ANSWER, ""))
                    final_output = final
                    await self.tasks.partial(
                        prepared.thread_id, {**state.get("_BI_RESULT", {}), "answer": final}
                    )
                    if state.get("_PARTIAL_ANALYSIS"):
                        outcome = "partial"
                    feasibility = state.get("FEASIBILITY_ASSESSMENT_NODE_OUTPUT", {})
                    if (
                        feasibility.get("requirementType", feasibility.get("requirement_type"))
                        == "NEED_CLARIFICATION"
                    ):
                        outcome = "needs_clarification"
                    if final:
                        references = self.artifacts.load(prepared.thread_id, int(prepared.request.agent_id))
                        self.artifacts.add(
                            prepared.thread_id,
                            int(prepared.request.agent_id),
                            "answer",
                            final,
                            [item["artifactId"] for item in references["artifacts"]],
                        )
                    if final and not state.get("_FINAL_EVENT_SENT"):
                        await queue.put(
                            (
                                None,
                                GraphNodeResponse(
                                    agent_id=prepared.request.agent_id,
                                    thread_id=prepared.thread_id,
                                    event_type=GraphEventType.FINAL_ANSWER,
                                    node_name="",
                                    text=final,
                                    text_type=TextType.MARK_DOWN,
                                ),
                            )
                        )
                await queue.put(
                    (
                        STREAM_EVENT_COMPLETE,
                        GraphNodeResponse(
                            agent_id=prepared.request.agent_id,
                            thread_id=prepared.thread_id,
                            node_name="",
                            complete=True,
                        ),
                    )
                )
        except asyncio.CancelledError:
            graph_cancelled = True
            if checkpoint_authorized:
                await self._delete_checkpoint(prepared.thread_id)
            await queue.put(
                (
                    "cancelled",
                    GraphNodeResponse(
                        agent_id=prepared.request.agent_id,
                        thread_id=prepared.thread_id,
                        text="分析已取消，已完成的证据保留",
                        complete=True,
                    ),
                )
            )
        except Exception as exc:
            graph_error = exc
            # An invalid resume request must not be able to destroy another
            # conversation's otherwise valid checkpoint.
            if checkpoint_authorized:
                await self._delete_checkpoint(prepared.thread_id)
            await queue.put(
                (
                    STREAM_EVENT_ERROR,
                    GraphNodeResponse(
                        agent_id=prepared.request.agent_id,
                        thread_id=prepared.thread_id,
                        node_name="",
                        text=(
                            f"Error in stream processing: {type(exc).__name__}: "
                            f"{str(exc) or type(exc).__name__}"
                        ),
                        error=True,
                    ),
                )
            )
        finally:
            try:
                if checkpoint_authorized:
                    await self.tasks.finish(
                        prepared.thread_id,
                        "CANCELLED"
                        if graph_cancelled
                        else "TIMED_OUT"
                        if isinstance(graph_error, TimeoutError)
                        else "FAILED"
                        if graph_error
                        else "WAITING"
                        if awaiting_feedback
                        else "PARTIAL"
                        if outcome == "partial"
                        else "COMPLETED",
                        str(graph_error) if graph_error else "",
                    )
                if evidence_initialized:
                    evidence_status = (
                        "cancelled"
                        if graph_cancelled
                        else "failed"
                        if graph_error
                        else "waiting"
                        if awaiting_feedback
                        else outcome
                    )
                    self.artifacts.finish(prepared.thread_id, int(prepared.request.agent_id), evidence_status)
            finally:
                self.telemetry.finish_graph(
                    trace,
                    trace_token,
                    output=final_output,
                    error=graph_error,
                    cancelled=graph_cancelled,
                )
                await queue.put(None)

    async def stop(self, *, thread_id: str | None, conversation_id: str | None) -> None:
        targets: list[str] = []
        async with self._active_lock:
            for run_id, active in self._active.items():
                if (thread_id and run_id == thread_id) or (
                    not thread_id and conversation_id and active.conversation_id == conversation_id
                ):
                    active.cancelled.set()
                    targets.append(run_id)
        for run_id in targets:
            await self.tasks.cancel(run_id)
            await self._delete_checkpoint(run_id)
        # A HITL run is inactive while it waits for feedback, but its checkpoint
        # still represents live work. Explicit stop must clear that durable state too.
        if thread_id and thread_id not in targets:
            try:
                await self.tasks.cancel(thread_id)
            except NotFoundError:
                pass  # Pre-upgrade checkpoints do not have a task row.
            await self._delete_checkpoint(thread_id)
        elif not thread_id and conversation_id:
            async with self.database.session_factory() as session:
                checkpoint_ids = (
                    (
                        await session.execute(
                            select(GraphCheckpoint.thread_id).where(
                                GraphCheckpoint.conversation_id == conversation_id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                await session.execute(
                    delete(GraphCheckpoint).where(GraphCheckpoint.conversation_id == conversation_id)
                )
                await session.commit()
            await self.memory_cache.invalidate_many(GRAPH_CHECKPOINT, checkpoint_ids)
            for checkpoint_id in checkpoint_ids:
                try:
                    await self.tasks.cancel(checkpoint_id)
                except NotFoundError:
                    pass

    async def _run_node(
        self, node: Any, context: NodeContext, cancelled: asyncio.Event, started: float
    ) -> Any:
        running = asyncio.create_task(node.run(context))
        last_pulse = perf_counter()
        try:
            while True:
                if cancelled.is_set() or await self.tasks.cancelled(context.thread_id):
                    cancelled.set()
                    raise asyncio.CancelledError
                if perf_counter() - started >= self.settings.bi_task_timeout_seconds:
                    raise TimeoutError("Analysis exceeded its configured task deadline")
                done, _ = await asyncio.wait({running}, timeout=0.4)
                if done:
                    return running.result()
                if perf_counter() - last_pulse > 10:
                    await self.tasks.pulse(context.thread_id)
                    last_pulse = perf_counter()
        finally:
            if not running.done():
                running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    async def start_bi(self, request: GraphRequest, *, reserved_task_id: str | None = None) -> dict[str, Any]:
        prepared = self.prepare(request)
        if reserved_task_id is not None:
            if prepared.resuming:
                raise DataAgentError("Reserved task IDs cannot resume human-review checkpoints")
            try:
                prepared.thread_id = str(UUID(reserved_task_id))
            except (ValueError, TypeError, AttributeError) as exc:
                raise DataAgentError("Invalid reserved task ID") from exc
        created = await self.tasks.create(prepared.thread_id, int(request.agent_id), prepared.conversation_id)
        if reserved_task_id is not None and not created:
            # Durable insert is the cross-worker start fence. An existing task is
            # adopted, never started twice. Ownership is still checked.
            existing = await self.tasks.get(prepared.thread_id, int(request.agent_id))
            return {key: existing[key] for key in ("task_id", "conversation_id", "status")}

        async def consume() -> None:
            try:
                async for _ in self.stream(request, prepared=prepared):
                    pass
            except Exception as exc:
                await self.tasks.finish(prepared.thread_id, "FAILED", str(exc))

        runner = asyncio.create_task(consume())
        self._background.add(runner)
        runner.add_done_callback(self._background.discard)
        return {
            "task_id": prepared.thread_id,
            "conversation_id": prepared.conversation_id,
            "status": "PENDING",
        }

    async def close(self) -> None:
        for runner in list(self._background):
            runner.cancel()
        await asyncio.gather(*self._background, return_exceptions=True)
        await self.dashboard.close()

    async def nl2sql(self, natural_query: str, agent_id: str) -> str:
        request = GraphRequest(agent_id=agent_id, query=natural_query, nl2sql_only=True)
        sql = ""
        async for _, event in self.stream(request):
            if event.node_name == "SQL_GENERATE_NODE" and event.text_type == TextType.SQL:
                sql = event.text
            if event.error:
                raise DataAgentError(event.text)
        return sql

    async def _conversation_context(self, session: Any, conversation_id: str) -> str:
        identity = self._conversation_cache_identity(conversation_id)

        async def load() -> str:
            rows = (
                (
                    await session.execute(
                        select(ConversationTurn)
                        .where(ConversationTurn.conversation_id == conversation_id)
                        .order_by(ConversationTurn.id.desc())
                        .limit(self.settings.max_turn_history)
                    )
                )
                .scalars()
                .all()
            )
            budget = self.settings.conversation_context_max_tokens
            selected: list[str] = []
            used_tokens = 0
            omitted = 0
            compacted_latest = False
            separator_tokens = token_count("\n\n")
            # The query is newest-first. Keep the most recent complete turns and
            # discard older turns before ever cutting the latest turn.
            for index, row in enumerate(rows):
                turn = (
                    f"User: {row.question}\n"
                    f"Assistant plan: {row.planner_output[: self.settings.max_plan_length]}"
                )
                turn_tokens = token_count(turn)
                required = turn_tokens + (separator_tokens if selected else 0)
                if used_tokens + required <= budget:
                    selected.append(turn)
                    used_tokens += required
                    continue
                if not selected:
                    selected.append(truncate_to_tokens(turn, budget))
                    compacted_latest = True
                    omitted = len(rows) - index - 1
                else:
                    omitted = len(rows) - index
                break

            selected.reverse()
            context = "\n\n".join(selected)
            notices: list[str] = []
            if compacted_latest:
                notices.append("[The latest conversation turn was compacted to fit the token budget.]")
            if omitted:
                notices.append(f"[Omitted {omitted} older conversation turn(s) to fit the token budget.]")
            if notices:
                marker = "\n".join(notices)
                marker_tokens = token_count(marker + "\n\n")
                context = marker + "\n\n" + truncate_to_tokens(context, max(1, budget - marker_tokens))
            return context

        value = await self.memory_cache.get_or_load(
            CONVERSATION_CONTEXT,
            identity,
            self.settings.redis_conversation_ttl_seconds,
            load,
        )
        return str(value)

    def _conversation_cache_identity(self, conversation_id: str) -> str:
        return "|".join(
            (
                conversation_id,
                str(self.settings.max_turn_history),
                str(self.settings.max_plan_length),
                str(self.settings.conversation_context_max_tokens),
            )
        )

    async def invalidate_conversation_memory(self, conversation_ids: list[str]) -> None:
        await self.memory_cache.invalidate_many(
            CONVERSATION_CONTEXT,
            (self._conversation_cache_identity(value) for value in conversation_ids),
        )

    async def _record_turn(self, session: Any, conversation_id: str, state: dict[str, Any]) -> None:
        if state.get("_TURN_RECORDED") or not state.get(PLANNER_NODE_OUTPUT):
            return
        session.add(
            ConversationTurn(
                conversation_id=conversation_id,
                question=str(state.get(INPUT, "")),
                planner_output=str(state.get(PLANNER_NODE_OUTPUT, ""))[: self.settings.max_plan_length],
            )
        )
        state["_TURN_RECORDED"] = True
        await session.commit()
        await self.memory_cache.invalidate(
            CONVERSATION_CONTEXT, self._conversation_cache_identity(conversation_id)
        )

    async def _restart_last_turn(self, session: Any, conversation_id: str) -> None:
        latest = (
            await session.execute(
                select(ConversationTurn)
                .where(ConversationTurn.conversation_id == conversation_id)
                .order_by(ConversationTurn.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if latest is not None:
            await session.delete(latest)
            await session.commit()
            await self.memory_cache.invalidate(
                CONVERSATION_CONTEXT, self._conversation_cache_identity(conversation_id)
            )

    async def _save_checkpoint(
        self,
        session: Any,
        thread_id: str,
        conversation_id: str,
        agent_id: str,
        next_node: str,
        state: dict[str, Any],
    ) -> None:
        checkpoint = await session.get(GraphCheckpoint, thread_id)
        payload = json.dumps(state, ensure_ascii=False, default=str)
        if checkpoint is None:
            checkpoint = GraphCheckpoint(
                thread_id=thread_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                next_node=next_node,
                state_json=payload,
            )
            session.add(checkpoint)
        else:
            checkpoint.next_node = next_node
            checkpoint.state_json = payload
        await session.commit()
        await self.memory_cache.put(
            GRAPH_CHECKPOINT,
            thread_id,
            {
                "thread_id": thread_id,
                "conversation_id": conversation_id,
                "agent_id": agent_id,
                "next_node": next_node,
                "state_json": payload,
            },
            self.settings.redis_checkpoint_ttl_seconds,
        )

    async def _load_checkpoint(self, session: Any, thread_id: str) -> dict[str, str] | None:
        async def load() -> dict[str, str] | None:
            checkpoint = await session.get(GraphCheckpoint, thread_id)
            if checkpoint is None:
                return None
            return {
                "thread_id": checkpoint.thread_id,
                "conversation_id": checkpoint.conversation_id,
                "agent_id": checkpoint.agent_id,
                "next_node": checkpoint.next_node,
                "state_json": checkpoint.state_json,
            }

        value = await self.memory_cache.get_or_load(
            GRAPH_CHECKPOINT,
            thread_id,
            self.settings.redis_checkpoint_ttl_seconds,
            load,
        )
        return value if isinstance(value, dict) else None

    async def _delete_checkpoint(self, thread_id: str) -> None:
        async with self.database.session_factory() as session:
            await session.execute(delete(GraphCheckpoint).where(GraphCheckpoint.thread_id == thread_id))
            await session.commit()
        await self.memory_cache.invalidate(GRAPH_CHECKPOINT, thread_id)
