from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ChatGenerationOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable_thinking: bool | None = None
    thinking_budget: int | None = Field(None, ge=0)


class ChatRequestProfile(ChatGenerationOptions):
    """Explicit endpoint/model capabilities, with optional task-scoped generation policy."""

    endpoint_host: str
    model: str
    purpose_options: dict[str, ChatGenerationOptions] = Field(default_factory=dict)
    # Only explicit, provider-documented MODEL-scoped errors; not arbitrary 403s.
    unavailable_error_codes: list[str] = Field(default_factory=list)


class ChatTaskBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_output_tokens: int | None = Field(None, ge=128)
    timeout_seconds: float = Field(180.0, gt=0)


class Settings(BaseSettings):
    """Runtime settings with environment names matching ``.env.example``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="DATAAGENT_",
        case_sensitive=False,
        extra="ignore",
    )

    management_database_url: str = "sqlite+aiosqlite:///./data/dataagent.db"
    database_echo: bool = False

    # Read-only industrial dashboard source.  It is intentionally independent
    # from the management database and points at the verified PdM mirror that
    # ships with this project.  Deployments may override it with the MySQL URL.
    dashboard_database_url: str = (
        "sqlite:///./examples/industrial-pdm/generated/runtime/industrial_pdm.sqlite"
    )
    dashboard_query_timeout_seconds: float = Field(30.0, gt=0, le=300)
    dashboard_telemetry_max_points: int = Field(500, ge=50, le=2000)

    # Explicit file source; original CSVs are opened read-only, never imported or rewritten.
    scania_data_dir: Path | None = None
    bi_query_timeout_seconds: float = Field(60, gt=0, le=300)
    bi_task_timeout_seconds: float = Field(600, gt=0, le=3600)
    bi_max_concurrency: int = Field(2, ge=1, le=8)
    bi_memory_limit_mb: int = Field(512, ge=128, le=4096)

    # MySQL/SQLite is authoritative. Redis is an optional low-latency hot layer
    # for chat lists/messages, bounded conversation context and HITL checkpoints.
    redis_url: str = ""
    redis_password: str = ""
    redis_required: bool = False
    redis_key_prefix: str = "dataagent"
    redis_conversation_ttl_seconds: int = Field(3600, ge=1)
    redis_chat_ttl_seconds: int = Field(300, ge=1)
    redis_checkpoint_ttl_seconds: int = Field(3600, ge=1)
    redis_connect_timeout_seconds: float = Field(1.0, gt=0)
    redis_socket_timeout_seconds: float = Field(1.0, gt=0)
    redis_retry_interval_seconds: float = Field(5.0, ge=0)
    redis_lock_timeout_seconds: float = Field(15.0, gt=0)
    redis_lock_wait_seconds: float = Field(5.0, ge=0)

    model_mode: Literal["database", "mock"] = "database"
    model_http_timeout_seconds: float = 120.0
    model_transient_retries: int = Field(2, ge=0, le=5)
    model_retry_backoff_seconds: float = Field(0.5, ge=0, le=30)
    # Ordered model IDs on the ACTIVE CHAT endpoint, with the same credentials
    # and parameter contract. Never applied to embeddings or another provider.
    chat_fallback_models: list[str] = Field(default_factory=list, max_length=8)
    chat_request_profiles: list[ChatRequestProfile] = Field(default_factory=list)
    chat_task_budgets: dict[str, ChatTaskBudget] = Field(default_factory=dict)
    # Wall-clock budget includes fallback, context compaction and JSON repair.
    chat_completion_timeout_seconds: float = Field(300.0, gt=0)
    chart_selection_retries: int = Field(1, ge=0, le=2)
    report_revision_retries: int = Field(1, ge=0, le=2)
    report_calculation_retries: int = Field(1, ge=0, le=2)
    # Off until explicitly enabled; creating a schedule never starts background
    # execution in an installation whose operator has not enabled the worker.
    report_scheduler_enabled: bool = False
    report_scheduler_poll_seconds: float = Field(30, ge=1, le=300)
    chat_require_full_context: list[str] = Field(
        default_factory=lambda: ["report", "report-review", "report-calculations", "report-claims"]
    )
    # All chat calls pass through one budget gate. The window must match the
    # smallest active CHAT model; provider overflow responses trigger stricter
    # retries because OpenAI-compatible endpoints may use different tokenizers.
    chat_context_window_tokens: int = Field(32_768, ge=2_048)
    chat_context_safety_tokens: int = Field(512, ge=0)
    chat_context_overflow_retries: int = Field(2, ge=0, le=5)
    chat_context_retry_ratio: float = Field(0.5, gt=0.0, lt=1.0)
    conversation_context_max_tokens: int = Field(4_096, ge=128)

    # Embeddings normally follow the upstream database-backed model registry.
    # ``local_bge_m3`` is an explicit, versioned opt-in for the user's verified
    # local Chinese RAG stack; it never silently reuses vectors from another model.
    embedding_mode: Literal["database", "local_bge_m3"] = "database"
    local_embedding_model: str = "BAAI/bge-m3"
    local_embedding_device: Literal["auto", "cpu", "cuda"] = "auto"
    local_embedding_use_fp16: bool = True
    local_embedding_batch_size: int = 4
    local_embedding_query_max_length: int = 512
    local_embedding_passage_max_length: int = 8192
    local_embedding_cache_dir: Path = Path("./data/models")

    vector_store_type: Literal["simple", "elasticsearch", "milvus"] = "simple"
    vector_file_path: Path = Path("./data/vectorstore.json")
    vector_embedding_dimension: int = 0
    vector_table_top_k: int = 10
    vector_table_threshold: float = 0.2
    vector_default_top_k: int = 8
    vector_default_threshold: float = 0.4
    vector_batch_delete_limit: int = 5000
    vector_enable_hybrid_search: bool = False
    vector_hybrid_timeout_ms: int = 3000
    embedding_batch_max_tokens: int = 8000
    embedding_batch_reserve_ratio: float = 0.2
    embedding_batch_max_items: int = 10
    elasticsearch_url: str = "http://localhost:9200"
    elasticsearch_index: str = "spring-ai-document-index"
    elasticsearch_min_score: float = 0.5
    milvus_uri: str = "http://localhost:19530"
    milvus_collection: str = "dataagent_documents"
    milvus_hybrid_rrf_k: int = 60
    milvus_hybrid_candidate_multiplier: int = 16

    file_storage_type: Literal["local", "oss"] = "local"
    file_path: Path = Path("./uploads")
    file_path_prefix: str = "data-agent"
    file_url_prefix: str = "/uploads"
    file_max_size: int = 10 * 1024 * 1024
    file_image_size: int = 2 * 1024 * 1024
    oss_endpoint: str = ""
    oss_access_key_id: str = ""
    oss_access_key_secret: str = ""
    oss_bucket_name: str = ""
    oss_custom_domain: str = ""
    tika_url: str = "http://tika:9998"
    tika_required: bool = False

    sandbox_docker_host: str = ""
    sandbox_image: str = (
        "agentscope-registry.ap-southeast-1.cr.aliyuncs.com/agentscope/runtime-sandbox-base:latest"
    )
    sandbox_max_concurrency: int = 4
    sandbox_queue_capacity: int = 10
    sandbox_max_connections: int = 4096
    sandbox_code_timeout_seconds: int = 60
    sandbox_dependency_timeout_seconds: int = 180
    sandbox_limit_memory_mb: int = 500
    sandbox_cpu_cores: float = 1.0
    sandbox_max_code_bytes: int = 256 * 1024
    sandbox_max_input_bytes: int = 10 * 1024 * 1024
    sandbox_max_stdout_bytes: int = 1024 * 1024
    sandbox_max_stderr_bytes: int = 256 * 1024
    sandbox_max_metadata_bytes: int = 8 * 1024
    sandbox_max_dependencies: int = 20
    sandbox_package_index_url: str = "https://pypi.org/simple"

    max_sql_retry_count: int = 10
    max_plan_repair_count: int = 2
    max_human_repair_count: int = 3
    python_max_tries_count: int = 5
    sql_query_timeout_seconds: int = 30
    sql_max_rows: int = 1000
    jdbc_h2_driver_class: str = "org.h2.Driver"
    jdbc_h2_jars: str = ""
    jdbc_dameng_driver_class: str = "dm.jdbc.driver.DmDriver"
    jdbc_dameng_jars: str = ""
    max_turn_history: int = 5
    max_plan_length: int = 2000
    enable_sql_result_chart: bool = True
    # Optional whole-stage limit. By default, use the model request/retry budget;
    # a legacy 15s decorative-enrichment limit can cancel required chart planning.
    enrich_sql_result_timeout_ms: int | None = Field(None, gt=0)

    text_chunk_size: int = 1000
    text_min_chunk_chars: int = 400
    text_chunk_overlap: int = 200
    text_sentence_overlap: int = 1
    text_semantic_min_chunk_size: int = 200
    text_semantic_max_chunk_size: int = 1000
    text_semantic_threshold: float = 0.5
    text_paragraph_overlap_chars: int = 200
    text_max_chunks: int = 5000

    embedding_recovery_initial_delay_seconds: int = 300
    embedding_recovery_interval_seconds: int = 300
    embedding_processing_stale_seconds: int = 600
    resource_cleanup_stale_seconds: int = 1800

    report_marked_url: str = "https://mirrors.sustech.edu.cn/cdnjs/ajax/libs/marked/12.0.0/marked.min.js"
    report_echarts_url: str = "https://mirrors.sustech.edu.cn/cdnjs/ajax/libs/echarts/5.5.0/echarts.min.js"
    report_dompurify_url: str = "https://mirrors.sustech.edu.cn/cdnjs/ajax/libs/dompurify/3.3.3/purify.min.js"

    langfuse_enabled: bool = False
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    host: str = "0.0.0.0"
    port: int = 8065
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8066
    mcp_transport: Literal["streamable-http", "sse", "stdio"] = "streamable-http"
    mcp_streamable_http_path: str = "/mcp"
    mcp_sse_path: str = "/sse"
    mcp_message_path: str = "/mcp/message"

    @field_validator("chat_fallback_models")
    @classmethod
    def normalize_fallback_models(cls, value: list[str]) -> list[str]:
        normalized = [name.strip() for name in value]
        if any(not name or any(char.isspace() for char in name) for name in normalized):
            raise ValueError("Fallback model IDs must be nonempty and contain no whitespace")
        return list(dict.fromkeys(normalized))

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    def ensure_directories(self) -> None:
        if self.management_database_url.startswith("sqlite"):
            db_path = self.management_database_url.split("///", 1)[-1]
            Path(db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self.file_path.expanduser().resolve().mkdir(parents=True, exist_ok=True)
        self.vector_file_path.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        if self.embedding_mode == "local_bge_m3":
            self.local_embedding_cache_dir.expanduser().resolve().mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
