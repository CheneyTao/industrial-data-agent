from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    inspect,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    create_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Agent(Base, TimestampMixin):
    __tablename__ = "agent"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    avatar: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50), default="draft", index=True)
    api_key: Mapped[str | None] = mapped_column(String(255))
    api_key_enabled: Mapped[int] = mapped_column(Integer, default=0)
    prompt: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(100), index=True)
    admin_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    tags: Mapped[str | None] = mapped_column(Text)
    # Root-cause closure: the Nuxt form already exposes this setting. Runtime query
    # parameters remain an override, but the Agent now has a durable default.
    human_review_enabled: Mapped[int] = mapped_column(Integer, default=0)


class Datasource(Base, TimestampMixin):
    __tablename__ = "datasource"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    database_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Explicit schema while preserving legacy database|schema parsing in adapters.
    schema_name: Mapped[str | None] = mapped_column(String(255))
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    password: Mapped[str] = mapped_column(String(1024), nullable=False)
    connection_url: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(50), default="inactive", index=True)
    test_status: Mapped[str] = mapped_column(String(50), default="unknown")
    description: Mapped[str | None] = mapped_column(Text)
    creator_id: Mapped[int | None] = mapped_column(BigInteger, index=True)


class AgentDatasource(Base, TimestampMixin):
    __tablename__ = "agent_datasource"
    __table_args__ = (UniqueConstraint("agent_id", "datasource_id", name="uk_agent_datasource"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agent.id", ondelete="CASCADE"), index=True)
    datasource_id: Mapped[int] = mapped_column(ForeignKey("datasource.id", ondelete="CASCADE"), index=True)
    is_active: Mapped[int] = mapped_column(Integer, default=0, index=True)
    datasource: Mapped[Datasource] = relationship(lazy="selectin")
    selected_tables: Mapped[list[AgentDatasourceTable]] = relationship(
        back_populates="agent_datasource", cascade="all, delete-orphan", lazy="selectin"
    )


class AgentDatasourceTable(Base, TimestampMixin):
    __tablename__ = "agent_datasource_tables"
    __table_args__ = (
        UniqueConstraint("agent_datasource_id", "table_name", name="uk_agent_datasource_table"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_datasource_id: Mapped[int] = mapped_column(
        ForeignKey("agent_datasource.id", ondelete="CASCADE"), index=True
    )
    table_name: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_datasource: Mapped[AgentDatasource] = relationship(back_populates="selected_tables")


class LogicalRelation(Base):
    __tablename__ = "logical_relation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    datasource_id: Mapped[int] = mapped_column(ForeignKey("datasource.id", ondelete="CASCADE"), index=True)
    source_table_name: Mapped[str] = mapped_column(String(100), nullable=False)
    source_column_name: Mapped[str] = mapped_column(String(100), nullable=False)
    target_table_name: Mapped[str] = mapped_column(String(100), nullable=False)
    target_column_name: Mapped[str] = mapped_column(String(100), nullable=False)
    relation_type: Mapped[str | None] = mapped_column(String(20))
    description: Mapped[str | None] = mapped_column(String(500))
    is_deleted: Mapped[int] = mapped_column(Integer, default=0)
    created_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class SemanticModel(Base):
    __tablename__ = "semantic_model"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agent.id", ondelete="CASCADE"), index=True)
    datasource_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    table_name: Mapped[str] = mapped_column(String(255), nullable=False)
    column_name: Mapped[str] = mapped_column(String(255), default="")
    business_name: Mapped[str] = mapped_column(String(255), default="")
    synonyms: Mapped[str | None] = mapped_column(Text)
    business_description: Mapped[str | None] = mapped_column(Text)
    column_comment: Mapped[str | None] = mapped_column(String(255))
    data_type: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[int] = mapped_column(Integer, default=1, index=True)
    created_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        "updated_time", DateTime, server_default=func.now(), onupdate=func.now()
    )
    __table_args__ = (Index("idx_semantic_agent_table", "agent_id", "table_name"),)


class BusinessKnowledge(Base):
    __tablename__ = "business_knowledge"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    business_term: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    synonyms: Mapped[str | None] = mapped_column(Text)
    is_recall: Mapped[int] = mapped_column(Integer, default=1, index=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agent.id", ondelete="CASCADE"), index=True)
    created_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    embedding_status: Mapped[str | None] = mapped_column(String(20), index=True)
    error_msg: Mapped[str | None] = mapped_column(String(255))
    is_deleted: Mapped[int] = mapped_column(Integer, default=0, index=True)


class AgentKnowledge(Base):
    __tablename__ = "agent_knowledge"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agent.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    question: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    is_recall: Mapped[int] = mapped_column(Integer, default=1)
    embedding_status: Mapped[str | None] = mapped_column(String(20), index=True)
    error_msg: Mapped[str | None] = mapped_column(String(255))
    source_filename: Mapped[str | None] = mapped_column(String(500))
    file_path: Mapped[str | None] = mapped_column(String(500))
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    file_type: Mapped[str | None] = mapped_column(String(255))
    splitter_type: Mapped[str] = mapped_column(String(50), default="token")
    created_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    is_deleted: Mapped[int] = mapped_column(Integer, default=0, index=True)
    is_resource_cleaned: Mapped[int] = mapped_column(Integer, default=0)


class AgentPresetQuestion(Base, TimestampMixin):
    __tablename__ = "agent_preset_question"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agent.id", ondelete="CASCADE"), index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)


class ChatSession(Base, TimestampMixin):
    __tablename__ = "chat_session"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agent.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255), default="新会话")
    status: Mapped[str] = mapped_column(String(50), default="active", index=True)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)


class ChatMessage(Base):
    __tablename__ = "chat_message"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_session.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column(String(50), default="text", index=True)
    metadata_json: Mapped[str | None] = mapped_column("metadata", Text)
    create_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class UserPromptConfig(Base, TimestampMixin):
    __tablename__ = "user_prompt_config"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    agent_id: Mapped[int | None] = mapped_column(Integer, index=True)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    creator: Mapped[str | None] = mapped_column(String(255))


class ModelConfig(Base):
    __tablename__ = "model_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    api_key: Mapped[str] = mapped_column(String(1000), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    temperature: Mapped[float] = mapped_column(Float, default=0.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    max_tokens: Mapped[int] = mapped_column(Integer, default=2000)
    model_type: Mapped[str] = mapped_column(String(20), default="CHAT", index=True)
    completions_path: Mapped[str | None] = mapped_column(String(255))
    embeddings_path: Mapped[str | None] = mapped_column(String(255))
    created_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    is_deleted: Mapped[int] = mapped_column(Integer, default=0, index=True)
    proxy_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    proxy_host: Mapped[str | None] = mapped_column(String(255))
    proxy_port: Mapped[int | None] = mapped_column(Integer)
    proxy_username: Mapped[str | None] = mapped_column(String(255))
    proxy_password: Mapped[str | None] = mapped_column(String(1000))


class GraphCheckpoint(Base):
    __tablename__ = "graph_checkpoint"

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    agent_id: Mapped[str] = mapped_column(String(64), index=True)
    next_node: Mapped[str] = mapped_column(String(100), nullable=False)
    state_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ConversationTurn(Base):
    __tablename__ = "conversation_turn"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    planner_output: Mapped[str] = mapped_column(Text, nullable=False)
    created_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


def model_columns(instance: Base, *, exclude: set[str] | None = None) -> dict[str, Any]:
    """Return mapped column values without triggering relationship loading."""

    excluded = exclude or set()
    return {
        attribute.key: getattr(instance, attribute.key)
        for attribute in inspect(type(instance)).column_attrs
        if attribute.key not in excluded
    }
