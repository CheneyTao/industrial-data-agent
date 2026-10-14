from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from dataagent.connectors import ConnectorService
from dataagent.errors import DataAgentError
from dataagent.models import AgentDatasource
from dataagent.vector_metadata import (
    AGENT_ID,
    COLUMN,
    DATASOURCE_ID,
    NAME,
    TABLE,
    TABLE_NAME,
    VECTOR_TYPE,
)
from dataagent.vectorstore import RetrievalService


class SchemaIndexer:
    def __init__(self, connectors: ConnectorService, retrieval: RetrievalService) -> None:
        self.connectors = connectors
        self.retrieval = retrieval

    async def initialize(self, session: AsyncSession, agent_id: int) -> dict[str, int]:
        association = (
            await session.execute(
                select(AgentDatasource)
                .where(AgentDatasource.agent_id == agent_id, AgentDatasource.is_active == 1)
                .options(
                    selectinload(AgentDatasource.datasource), selectinload(AgentDatasource.selected_tables)
                )
            )
        ).scalar_one_or_none()
        if association is None:
            raise DataAgentError("Agent has no active datasource")
        tables = [item.table_name for item in association.selected_tables]
        if not tables:
            raise DataAgentError("Select at least one table before initializing Schema")
        table_texts: list[str] = []
        table_metadata: list[dict[str, object]] = []
        column_texts: list[str] = []
        column_metadata: list[dict[str, object]] = []
        table_columns: dict[str, list[dict[str, object]]] = {}
        table_samples: dict[str, dict[str, list[str]]] = {}
        foreign_keys_by_table: dict[str, list[str]] = {name: [] for name in tables}
        connector = self.connectors.for_source(association.datasource)

        for table_name in tables:
            columns = await connector.columns(table_name)
            table_columns[table_name] = columns
            table_samples[table_name] = await connector.sample_values(
                table_name, [str(item["name"]) for item in columns], limit=5
            )
            for foreign_key in await connector.foreign_keys(table_name):
                relation = (
                    f"{foreign_key['sourceTableName']}.{foreign_key['sourceColumnName']} -> "
                    f"{foreign_key['targetTableName']}.{foreign_key['targetColumnName']}"
                )
                for related_table in {
                    str(foreign_key["sourceTableName"]),
                    str(foreign_key["targetTableName"]),
                }:
                    if related_table in foreign_keys_by_table:
                        foreign_keys_by_table[related_table].append(relation)

        for table_name in tables:
            columns = table_columns[table_name]
            table_texts.append(
                f"table {table_name}: "
                + ", ".join(
                    f"{item['name']} {item.get('type', '')} {item.get('description') or ''}".strip()
                    for item in columns
                )
            )
            common = {
                AGENT_ID: agent_id,
                DATASOURCE_ID: association.datasource_id,
                TABLE_NAME: table_name,
            }
            table_metadata.append(
                {
                    **common,
                    VECTOR_TYPE: TABLE,
                    NAME: table_name,
                    "description": "",
                    "primaryKey": [
                        str(item["name"]) for item in columns if item.get("primaryKey")
                    ],
                    "foreignKey": "、".join(dict.fromkeys(foreign_keys_by_table[table_name])),
                }
            )
            for column in columns:
                description = column.get("description") or ""
                column_texts.append(
                    f"{table_name}.{column['name']} {column.get('type', '')}: {description}".strip()
                )
                column_metadata.append(
                    {
                        **common,
                        VECTOR_TYPE: COLUMN,
                        NAME: column["name"],
                        "columnName": column["name"],
                        "description": description,
                        "type": column.get("type"),
                        "primary": bool(column.get("primaryKey")),
                        "notnull": not bool(column.get("nullable", True)),
                        "samples": table_samples[table_name].get(str(column["name"]), []),
                    }
                )
        base = {AGENT_ID: agent_id, DATASOURCE_ID: association.datasource_id}
        await self.retrieval.replace(
            session,
            texts=table_texts,
            metadata=table_metadata,
            old_filter={**base, VECTOR_TYPE: TABLE},
        )
        await self.retrieval.replace(
            session,
            texts=column_texts,
            metadata=column_metadata,
            old_filter={**base, VECTOR_TYPE: COLUMN},
        )
        return {"tableCount": len(table_texts), "columnCount": len(column_texts)}
