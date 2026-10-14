from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Generic, Literal, TypeVar

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


def to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(word[:1].upper() + word[1:] for word in rest)


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
        use_enum_values=True,
        extra="ignore",
    )


T = TypeVar("T")


class ApiResponse(CamelModel, Generic[T]):
    success: bool
    message: str
    data: T | None = None


class PageResponse(ApiResponse[T], Generic[T]):
    total: int | None = None
    page_num: int | None = None
    page_size: int | None = None
    total_pages: int | None = None


class AgentStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    OFFLINE = "offline"


class ModelType(StrEnum):
    CHAT = "CHAT"
    EMBEDDING = "EMBEDDING"


class EmbeddingStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class KnowledgeType(StrEnum):
    DOCUMENT = "DOCUMENT"
    QA = "QA"
    FAQ = "FAQ"


class SplitterType(StrEnum):
    TOKEN = "token"
    RECURSIVE = "recursive"
    SENTENCE = "sentence"
    SEMANTIC = "semantic"
    PARAGRAPH = "paragraph"


class AgentPayload(CamelModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    avatar: str | None = None
    status: str = "draft"
    prompt: str | None = None
    category: str | None = None
    admin_id: int | None = None
    tags: str | None = None
    human_review_enabled: int | bool = 0

    @field_validator("human_review_enabled", mode="after")
    @classmethod
    def normalize_review(cls, value: int | bool) -> int:
        return int(bool(value))


class AgentView(AgentPayload):
    id: int
    api_key: str | None = None
    api_key_enabled: int = 0
    create_time: datetime | None = None
    update_time: datetime | None = None


class ApiKeyResponse(CamelModel):
    agent_id: int
    api_key: str | None = None
    api_key_masked: str | None = None
    enabled: bool = False


class DatasourcePayload(CamelModel):
    name: str
    type: str
    host: str = "localhost"
    port: int
    database_name: str
    schema_name: str | None = None
    username: str = ""
    password: str = ""
    connection_url: str | None = None
    status: str = "active"
    test_status: str = "unknown"
    description: str | None = None
    creator_id: int | None = None


class DatasourceView(DatasourcePayload):
    id: int
    create_time: datetime | None = None
    update_time: datetime | None = None


class ToggleDatasourceDTO(CamelModel):
    datasource_id: int
    is_active: bool


class UpdateDatasourceTablesDTO(CamelModel):
    datasource_id: int
    tables: list[str] = Field(default_factory=list)


class LogicalRelationPayload(CamelModel):
    source_table_name: str
    source_column_name: str
    target_table_name: str
    target_column_name: str
    relation_type: str | None = None
    description: str | None = None


class LogicalRelationSavePayload(LogicalRelationPayload):
    id: int | None = None


class BusinessKnowledgeCreate(CamelModel):
    business_term: str
    description: str | None = None
    synonyms: str | None = None
    is_recall: int = 1
    agent_id: int


class BusinessKnowledgeUpdate(CamelModel):
    business_term: str
    description: str | None = None
    synonyms: str | None = None


class AgentKnowledgeUpdate(CamelModel):
    title: str | None = None
    content: str | None = None


class AgentKnowledgeQuery(CamelModel):
    agent_id: int
    title: str | None = None
    type: str | None = None
    is_recall: int | None = None
    embedding_status: str | None = None
    page_num: int = Field(1, ge=1)
    page_size: int = Field(10, ge=1, le=200)


class PresetQuestionPayload(CamelModel):
    id: int | None = None
    question: str
    sort_order: int = 0
    is_active: bool = False


class ChatMessagePayload(CamelModel):
    role: str
    content: str
    message_type: str = "text"
    metadata: str | None = None
    title_needed: bool = False


class PromptConfigDTO(CamelModel):
    id: str | None = None
    name: str
    prompt_type: str
    agent_id: int | None = None
    optimization_prompt: str
    enabled: bool = True
    description: str | None = None
    creator: str | None = None
    priority: int = 0
    display_order: int = 0


PROMPT_TYPE_ALIASES = {
    "rewrite": "rewrite",
    "general-chat": "general-chat",
    "report-generator": "report-generator",
    "planner": "planner",
    "sql-generator": "sql-generator",
    "python-generator": "python-generator",
}


class ModelConfigDTO(CamelModel):
    id: int | None = None
    provider: str
    base_url: str
    api_key: str
    model_name: str
    temperature: float = Field(0.0, ge=0.0, le=2.0)
    is_active: bool = False
    max_tokens: int = Field(2000, ge=1)
    model_type: ModelType = ModelType.CHAT
    completions_path: str | None = None
    embeddings_path: str | None = None
    proxy_enabled: bool = False
    proxy_host: str | None = None
    proxy_port: int | None = None
    proxy_username: str | None = None
    proxy_password: str | None = None
    created_time: datetime | None = None
    updated_time: datetime | None = None


class SemanticModelPayload(CamelModel):
    agent_id: int
    datasource_id: int | None = None
    table_name: str
    column_name: str
    business_name: str
    synonyms: str | None = None
    business_description: str | None = None
    column_comment: str | None = None
    data_type: str
    status: int = 1

    @field_validator("table_name", "column_name", "business_name", "data_type")
    @classmethod
    def required_semantic_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("semantic model required fields must not be blank")
        return value.strip()


class SemanticModelImportItem(CamelModel):
    table_name: str
    column_name: str
    business_name: str
    synonyms: str | None = None
    business_description: str | None = Field(
        default=None,
        validation_alias=AliasChoices("businessDescription", "businessDesc", "description", "desc"),
    )
    column_comment: str | None = None
    data_type: str
    status: int = 1
    created_time: datetime | None = None

    @field_validator("table_name", "column_name", "business_name", "data_type")
    @classmethod
    def required_semantic_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("semantic model required fields must not be blank")
        return value.strip()


class SemanticModelBatchImport(CamelModel):
    agent_id: int
    items: list[SemanticModelImportItem] = Field(min_length=1)


class ToolParameters(CamelModel):
    instruction: str | None = None
    summary_and_recommendations: str | None = None
    sql_query: str | None = None


class ExecutionStep(CamelModel):
    step: int = Field(ge=1)
    tool_to_use: str
    tool_parameters: ToolParameters = Field(default_factory=ToolParameters)


class Plan(CamelModel):
    thought_process: str = ""
    execution_plan: list[ExecutionStep]
    deliverables: list[Literal["result", "chart", "analysis", "report", "recommendations"]] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def sequential_steps(self) -> Plan:
        expected = list(range(1, len(self.execution_plan) + 1))
        actual = [step.step for step in self.execution_plan]
        if actual != expected:
            raise ValueError(f"steps must be sequential and 1-based: {actual}")
        return self


class GraphRequest(CamelModel):
    agent_id: str
    conversation_id: str | None = None
    thread_id: str | None = None
    query: str
    human_feedback: bool = False
    human_feedback_content: str | None = None
    rejected_plan: bool = False
    nl2sql_only: bool = False
    bi_request: dict[str, Any] | None = None
    attachment_ids: list[str] = Field(default_factory=list, max_length=6)


class GraphEventType(StrEnum):
    NODE_OUTPUT = "NODE_OUTPUT"
    FINAL_ANSWER = "FINAL_ANSWER"
    HUMAN_FEEDBACK_REQUIRED = "HUMAN_FEEDBACK_REQUIRED"


class TextType(StrEnum):
    JSON = "JSON"
    PYTHON = "PYTHON"
    SQL = "SQL"
    HTML = "HTML"
    MARK_DOWN = "MARK_DOWN"
    RESULT_SET = "RESULT_SET"
    TEXT = "TEXT"


class GraphNodeResponse(CamelModel):
    agent_id: str
    thread_id: str
    event_type: GraphEventType = GraphEventType.NODE_OUTPUT
    step_id: str | None = None
    attempt: int | None = None
    node_name: str = ""
    text: str = ""
    text_type: TextType = TextType.TEXT
    error: bool = False
    complete: bool = False


class ColumnDTO(CamelModel):
    name: str
    description: str | None = None
    enumeration: int = 0
    range: str | None = None
    type: str | None = None
    data: list[str] = Field(default_factory=list)
    mapping: dict[str, Any] | None = None


class TableDTO(CamelModel):
    name: str
    description: str | None = None
    column: list[ColumnDTO] = Field(default_factory=list)
    primary_keys: list[str] = Field(default_factory=list)


class SchemaDTO(CamelModel):
    name: str | None = None
    description: str | None = None
    table_count: int | None = None
    table: list[TableDTO] = Field(default_factory=list)
    foreign_keys: list[str] = Field(default_factory=list)


class SqlRetry(CamelModel):
    reason: str
    semantic_fail: bool = False
    sql_execute_fail: bool = False


def mask_secret(secret: str | None) -> str | None:
    if not secret:
        return secret
    if secret.startswith("****"):
        return secret
    return "****" + secret[-4:] if len(secret) >= 4 else "****"


def normalize_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$#@.\-]*", value):
        raise ValueError(f"unsafe identifier: {value}")
    return value
