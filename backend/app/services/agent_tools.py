"""
agent_tools.py — Tool factory for the ManualMind agent.

Provides the ``create_retrieval_tool`` factory, which injects ``assistant_id``
via closure so the LLM can never override it.  The returned callable returns a
``ToolResult`` named-tuple with both the JSON-serialisable LLM payload AND the
original ``HybridSearchResult`` so the workflow can collect ``RetrievedChunk``
objects without executing ``search_hybrid`` a second time.
"""

import copy
import logging
from typing import Any, Callable, Coroutine, Dict, NamedTuple, Tuple

from app.schemas.agent import SearchKnowledgeBaseInput
from app.schemas.retrieval import HybridSearchResult
from app.services.retrieval import HybridRetriever

logger = logging.getLogger("manualmind.agent_tools")

# ---------------------------------------------------------------------------
# JSON Schema for the tool — strict: true requires additionalProperties: false
# and all properties to be listed in "required".
# We build it manually from the Pydantic schema so we can enforce those fields.
# ---------------------------------------------------------------------------

_PYDANTIC_SCHEMA = SearchKnowledgeBaseInput.model_json_schema()
_PYDANTIC_SCHEMA.pop("$defs", None)
_PYDANTIC_SCHEMA.pop("title", None)

# Ensure strict-mode requirements: additionalProperties: false + all required
_PYDANTIC_SCHEMA.setdefault("additionalProperties", False)
_props = _PYDANTIC_SCHEMA.get("properties", {})
_PYDANTIC_SCHEMA["required"] = list(_props.keys())

TOOL_SPEC: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "search_knowledge_base",
        "description": (
            "Consulta os documentos indexados do assistente usando busca híbrida "
            "(semântica + lexical). Use quando a pergunta exigir informações específicas "
            "que podem estar nos manuais ou documentos internos da empresa. "
            "Não use para perguntas de conhecimento geral que você já sabe responder."
        ),
        "parameters": _PYDANTIC_SCHEMA,
        "strict": True,
    },
}


class ToolResult(NamedTuple):
    """
    Returned by the search_knowledge_base callable.

    Attributes:
        llm_payload: JSON-serialisable dict sent back to the LLM as a ToolMessage.
        retrieval_result: Original ``HybridSearchResult`` with ``RetrievedChunk``
            objects for citation persistence — no second ``search_hybrid`` call needed.
    """

    llm_payload: Dict[str, Any]
    retrieval_result: HybridSearchResult


# Type alias for the async callable returned by the factory.
ToolCallable = Callable[[str], Coroutine[Any, Any, ToolResult]]


def create_retrieval_tool(
    retriever: HybridRetriever,
    assistant_id: str,
    limit: int = 5,
) -> Tuple[ToolCallable, Dict[str, Any]]:
    """
    Returns ``(callable, tool_spec)`` with ``assistant_id`` locked via closure.

    The callable accepts only ``query`` — it is impossible for the LLM to pass
    a different ``assistant_id`` because that parameter is not part of the tool
    schema and is captured from the backend auth chain.

    Args:
        retriever: Instantiated ``HybridRetriever`` for the current request.
        assistant_id: UUID string from the authenticated assistant record.
        limit: Maximum number of chunks to return per call (default 5).

    Returns:
        A 2-tuple of ``(async callable -> ToolResult, tool spec dict)``.
    """

    async def search_knowledge_base(query: str) -> ToolResult:
        """Execute hybrid retrieval; return both LLM payload and RetrievedChunks."""
        logger.debug(
            "search_knowledge_base called: query=%r assistant_id=%s limit=%d",
            query,
            assistant_id,
            limit,
        )
        result: HybridSearchResult = await retriever.search_hybrid(
            query=query,
            assistant_id=assistant_id,
            limit=limit,
        )

        chunks_payload = [
            {
                "chunk_id": chunk.chunk_id,
                "document_name": chunk.document_name,
                "content": chunk.content,
                "page": chunk.page,
                "rrf_score": chunk.rrf_score,
                "retrieval_source": chunk.retrieval_source,
            }
            for chunk in result.results
        ]

        llm_payload = {
            "query": result.query,
            "total_results": result.total_results,
            "has_results": result.has_retrieved_results,
            "chunks": chunks_payload,
        }

        return ToolResult(llm_payload=llm_payload, retrieval_result=result)

    return search_knowledge_base, copy.deepcopy(TOOL_SPEC)
