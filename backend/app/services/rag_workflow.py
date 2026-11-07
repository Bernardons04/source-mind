"""
rag_workflow.py — Agent-loop workflow for ManualMind (Etapa 5).

Key behaviors:
1. Unified tool execution: `search_knowledge_base` is invoked via its callable,
   which returns a `ToolResult(llm_payload, retrieval_result)`. No duplicate
   `search_hybrid()` calls.
2. System Prompt: Allows general knowledge, conversational continuity,
   autonomous tool calling for company-specific docs, and graceful abstention
   when specific documents lack evidence.
3. Tool limits handling: All emitted tool calls in an iteration MUST receive
   a response, even if the limit is exceeded. Excess tool calls receive a
   structured limit error message.
4. Strict JSON Schema: For Phase 2 synthesis, enforce `additionalProperties: false`
   and `required`.
"""

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.agent import AgentResponsePayload, SearchKnowledgeBaseInput
from app.schemas.retrieval import RetrievedChunk
from app.services.agent_tools import ToolResult, create_retrieval_tool
from app.services.openrouter import OpenRouterClient
from app.services.retrieval import HybridRetriever

from datetime import datetime

logger = logging.getLogger("manualmind.rag_workflow")

# ---------------------------------------------------------------------------
# Hard limits
# ---------------------------------------------------------------------------
MAX_ITERATIONS = 3   # agent→tools→agent cycles
MAX_TOOL_CALLS = 3   # total individual tool calls executed across all iterations
CHUNKS_PER_SEARCH = 5

# ---------------------------------------------------------------------------
# JSON Schema for AgentResponsePayload (Phase 2 synthesis)
# ---------------------------------------------------------------------------
_RESPONSE_JSON_SCHEMA = AgentResponsePayload.model_json_schema()
_RESPONSE_JSON_SCHEMA.pop("$defs", None)
_RESPONSE_JSON_SCHEMA.pop("title", None)
_RESPONSE_JSON_SCHEMA.setdefault("additionalProperties", False)
_resp_props = _RESPONSE_JSON_SCHEMA.get("properties", {})
_RESPONSE_JSON_SCHEMA["required"] = list(_resp_props.keys())

SYNTHESIS_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "AgentResponsePayload",
        "strict": True,
        "schema": _RESPONSE_JSON_SCHEMA,
    },
}

# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------

def _build_system_prompt(
    assistant_name: str,
    custom_instructions: str,
    indexed_documents: List[str],
) -> str:
    """
    Build the dynamic system prompt with assistant configuration,
    current temporal context, and available knowledge base documents.
    """
    current_time = datetime.now().astimezone()

    temporal_context = (
        f"- Data atual: {current_time.strftime('%d/%m/%Y')}\n"
        f"- Dia da semana: {current_time.strftime('%A')}\n"
        f"- Horário atual: {current_time.strftime('%H:%M')}\n"
        f"- Fuso horário: {current_time.tzname() or current_time.strftime('%z')}"
    )

    doc_list = (
        "\n".join(f"  - {name}" for name in indexed_documents)
        if indexed_documents
        else "  (nenhum documento indexado)"
    )

    assistant_context = (
        f"Nome: {assistant_name}\n"
        "O nome e as configurações abaixo foram definidos pelo usuário.\n"
        f"{'Instruções personalizadas:\n' + custom_instructions if custom_instructions.strip() else 'Nenhuma instrução personalizada definida.'}"
    )

    return f"""\
Você é um assistente inteligente com acesso aos documentos e manuais da empresa.

CONFIGURAÇÕES DO ASSISTENTE:
{assistant_context}

CONTEXTO TEMPORAL:
{temporal_context}
Utilize essas informações para responder perguntas relacionadas a datas e horários.
Considere o horário informado como referência para a execução atual.

BASE DE CONHECIMENTO:
Os documentos abaixo estão indexados e disponíveis para consulta:
{doc_list}

DIRETRIZES DE ATUAÇÃO:
1. Respostas Gerais e Continuidade: Você pode responder perguntas de conhecimento geral, cumprimentos e dar continuidade ao diálogo naturalmente.
2. Consulta Documental Autônoma: Sempre que a dúvida do usuário envolver procedimentos, regras, políticas, especificações ou fatos internos da empresa, utilize a ferramenta `search_knowledge_base` para buscar as evidências antes de responder.
3. Fundamentação e Fidelidade: Ao responder com base nos documentos consultados, seja rigorosamente fiel aos fatos encontrados e não invente regras ou dados.
4. Abstenção Justificada: Se a pergunta depender de informações específicas da empresa e os documentos consultados não contiverem evidências suficientes, informe com clareza ao usuário o que não foi encontrado em vez de tentar adivinhar.
5. Idioma: Responda no mesmo idioma utilizado pelo usuário.
"""


# ---------------------------------------------------------------------------
# Core agent execution
# ---------------------------------------------------------------------------

async def run_agent(
    query: str,
    assistant_id: str,
    assistant_name: str,
    indexed_documents: List[str],
    chat_history: List[Dict[str, str]],
    db: AsyncSession,
    openrouter: OpenRouterClient,
    custom_instructions: str = "",
) -> Dict[str, Any]:
    """
    Execute the agent loop and return the final answer with retrieved chunks.
    """
    # --- Setup retrieval tool (assistant_id locked via closure) ---
    retriever = HybridRetriever(db=db, openrouter=openrouter)
    search_callable, tool_spec = create_retrieval_tool(
        retriever=retriever,
        assistant_id=assistant_id,
        limit=CHUNKS_PER_SEARCH,
    )
    tools = [tool_spec]
    tool_registry: Dict[str, Any] = {"search_knowledge_base": search_callable}

    # --- Build initial message list ---
    system_prompt = _build_system_prompt(assistant_name, custom_instructions, indexed_documents)
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]

    for msg in chat_history:
        if msg.get("role") in ("user", "assistant"):
            messages.append({"role": msg["role"], "content": msg["content"] or ""})

    last_user_in_history = next(
        (m["content"] for m in reversed(messages) if m["role"] == "user"), None
    )
    if last_user_in_history != query.strip():
        messages.append({"role": "user", "content": query.strip()})

    # --- State ---
    all_retrieved_chunks: Dict[str, RetrievedChunk] = {}  # keyed by chunk_id
    events: List[Dict[str, Any]] = []
    seq = 0
    iteration_count = 0
    tool_call_count = 0

    def add_event(event_type: str, payload: Dict[str, Any]) -> None:
        nonlocal seq
        seq += 1
        events.append({"sequence_number": seq, "event_type": event_type, "payload": payload})

    # -----------------------------------------------------------------------
    # Phase 1 — Agent loop
    # -----------------------------------------------------------------------
    add_event(
        "agent_start",
        {"query": query, "assistant_id": assistant_id, "indexed_doc_count": len(indexed_documents)},
    )

    while iteration_count < MAX_ITERATIONS and tool_call_count < MAX_TOOL_CALLS:
        iteration_count += 1
        logger.debug("Agent iteration %d (tool_calls_so_far=%d)", iteration_count, tool_call_count)

        choice = await openrouter.chat_completion(
            messages=messages,
            tools=tools,
        )
        agent_message = choice.get("message", {})
        content = agent_message.get("content") or ""
        raw_tool_calls = agent_message.get("tool_calls") or []

        assistant_turn: Dict[str, Any] = {"role": "assistant", "content": content}
        if raw_tool_calls:
            assistant_turn["tool_calls"] = raw_tool_calls
        messages.append(assistant_turn)

        add_event(
            "agent_iteration",
            {
                "iteration": iteration_count,
                "has_tool_calls": bool(raw_tool_calls),
                "tool_call_count": len(raw_tool_calls),
                "content_preview": content[:200] if content else None,
            },
        )

        if not raw_tool_calls:
            logger.debug("No tool calls in iteration %d, proceeding to synthesis.", iteration_count)
            break

        # -------------------------------------------------------------------
        # Execute tool calls & handle excess calls
        # -------------------------------------------------------------------
        for tc in raw_tool_calls:
            tc_id = tc.get("id", str(uuid.uuid4()))
            tc_function = tc.get("function", {})
            tc_name = tc_function.get("name", "")
            tc_args_raw = tc_function.get("arguments", "{}")

            # Check if limit has already been reached
            if tool_call_count >= MAX_TOOL_CALLS:
                logger.info(
                    "Tool call limit reached (%d). Returning limit reached response for tool call %s (%s)",
                    MAX_TOOL_CALLS,
                    tc_id,
                    tc_name,
                )
                limit_payload = {
                    "error": "limit_exceeded",
                    "message": f"O limite máximo de {MAX_TOOL_CALLS} consultas à base de conhecimento foi atingido para esta interação.",
                }
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": json.dumps(limit_payload, ensure_ascii=False),
                    }
                )
                add_event(
                    "tool_limit_exceeded",
                    {"tool": tc_name, "tool_call_id": tc_id, "limit": MAX_TOOL_CALLS},
                )
                continue

            tool_call_count += 1

            try:
                tc_args = json.loads(tc_args_raw) if isinstance(tc_args_raw, str) else tc_args_raw
            except json.JSONDecodeError as e:
                logger.warning("Could not parse tool call arguments: %s", e)
                tc_args = {}

            tool_result_content: str

            if tc_name in tool_registry:
                callable_fn = tool_registry[tc_name]
                try:
                    validated = SearchKnowledgeBaseInput(**tc_args)

                    # Single execution through the tool callable
                    tool_res: ToolResult = await callable_fn(query=validated.query)

                    # Collect chunks for citations
                    for chunk in tool_res.retrieval_result.results:
                        all_retrieved_chunks[chunk.chunk_id] = chunk

                    tool_result_content = json.dumps(tool_res.llm_payload, ensure_ascii=False)

                    add_event(
                        "tool_call",
                        {
                            "tool_call_number": tool_call_count,
                            "tool": tc_name,
                            "query": validated.query,
                            "chunks_returned": len(tool_res.retrieval_result.results),
                        },
                    )
                except Exception as tool_err:
                    logger.warning("Tool %r execution error: %s", tc_name, tool_err)
                    tool_result_content = json.dumps({"error": str(tool_err)}, ensure_ascii=False)
                    add_event(
                        "tool_error",
                        {"tool": tc_name, "error": str(tool_err)},
                    )
            else:
                logger.warning("Unknown tool called by LLM: %r", tc_name)
                tool_result_content = json.dumps({"error": f"Unknown tool: {tc_name}"}, ensure_ascii=False)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": tool_result_content,
                }
            )

    add_event(
        "agent_loop_end",
        {
            "iterations": iteration_count,
            "total_tool_calls": tool_call_count,
            "total_chunks_retrieved": len(all_retrieved_chunks),
        },
    )

    # -----------------------------------------------------------------------
    # Phase 2 — Synthesis: strict JSON Schema, tools disabled
    # -----------------------------------------------------------------------
    logger.debug("Synthesis phase: %d chunks in context.", len(all_retrieved_chunks))

    synthesis_choice = await openrouter.chat_completion(
        messages=messages,
        tools=None,
        response_format=SYNTHESIS_RESPONSE_FORMAT,
    )
    synthesis_content = synthesis_choice.get("message", {}).get("content") or ""

    try:
        payload_data = json.loads(synthesis_content)
        agent_payload = AgentResponsePayload.model_validate(payload_data)
        answer = agent_payload.answer
    except (json.JSONDecodeError, ValueError) as parse_err:
        logger.error(
            "Synthesis phase returned invalid JSON or failed Pydantic validation: %s — raw: %r",
            parse_err,
            synthesis_content[:500],
        )
        raise ValueError(
            f"Agent synthesis phase produced an invalid response: {parse_err}. "
            f"Raw content: {synthesis_content[:300]!r}"
        ) from parse_err

    add_event(
        "synthesis_complete",
        {"answer_length": len(answer), "chunks_in_citations": len(all_retrieved_chunks)},
    )

    return {
        "answer": answer,
        "retrieved_chunks": list(all_retrieved_chunks.values()),
        "events": events,
    }
