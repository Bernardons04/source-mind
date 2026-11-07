"""
agent.py — Pydantic schemas for the ManualMind agent (Etapa 5).

``AgentResponsePayload`` is used as a strict JSON Schema for Phase 2 synthesis.
``SearchKnowledgeBaseInput`` defines the tool parameter schema (strict: true).

Both models are designed so that ``model_json_schema()`` produces schemas
compatible with OpenRouter's ``strict: true`` requirement:
  - ``additionalProperties: false``
  - all properties listed in ``required``
"""

from pydantic import BaseModel, ConfigDict, Field


class AgentResponsePayload(BaseModel):
    """Strict JSON Schema model for the LLM synthesis phase output."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(..., description="Resposta final para o usuário.")


class SearchKnowledgeBaseInput(BaseModel):
    """Input schema for the search_knowledge_base tool."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        ...,
        min_length=2,
        description="Termos de busca nos documentos indexados da empresa.",
    )
