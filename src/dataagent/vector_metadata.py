"""Canonical vector metadata names from the audited Java implementation."""

AGENT_ID = "agentId"
DATASOURCE_ID = "datasourceId"
VECTOR_TYPE = "vectorType"
# Python safety extension: prevents vectors produced by distinct embedding
# models/configurations from sharing one retrieval space by accident.
EMBEDDING_SPACE_ID = "embeddingSpaceId"

COLUMN = "column"
TABLE = "table"
NAME = "name"
TABLE_NAME = "tableName"

AGENT_KNOWLEDGE = "agentKnowledge"
AGENT_KNOWLEDGE_ID = "agentKnowledgeId"
CONCRETE_AGENT_KNOWLEDGE_TYPE = "concreteAgentKnowledgeType"

BUSINESS_TERM = "businessTerm"
BUSINESS_TERM_ID = "businessTermId"
