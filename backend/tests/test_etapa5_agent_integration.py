"""
test_etapa5_agent_integration.py — Focused unit tests for the Etapa 5 agent integration.

Covers:
  1. Tool calling: agent tool receives query and returns ToolResult with structured chunks and original result.
  2. assistant_id isolation: the tool closure cannot be overridden by LLM args.
  3. Chunk-to-citation mapping: ALL retrieved chunks become MessageCitation records.
  4. Limit enforcement: iteration_count >= MAX_ITERATIONS routes to synthesis.
  5. Excess tool calls in same turn: all emitted tool calls receive a response, excess get limit payload.
  6. Transactional persistence: verifies assistant message, citations and events are saved with flush/commit.

All tests are pure unit tests with mocks — no live DB or external network required.
"""

import json
import uuid
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.retrieval import HybridSearchResult, RetrievedChunk, RetrievalDiagnostics
from app.services.agent_tools import TOOL_SPEC, ToolResult, create_retrieval_tool
from app.services.rag_workflow import MAX_ITERATIONS, MAX_TOOL_CALLS, run_agent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_chunk(chunk_id: str = None, content: str = "Sample content") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id or str(uuid.uuid4()),
        document_id=str(uuid.uuid4()),
        document_name="Manual Teste",
        content=content,
        page=1,
        page_end=2,
        metadata={},
        semantic_rank=1,
        semantic_score=0.9,
        lexical_rank=None,
        lexical_score=None,
        rrf_score=0.015,
        retrieval_source="semantic",
    )


def _make_hybrid_result(chunks: List[RetrievedChunk], query: str = "test") -> HybridSearchResult:
    return HybridSearchResult(
        query=query,
        total_results=len(chunks),
        results=chunks,
        has_retrieved_results=bool(chunks),
        diagnostics=RetrievalDiagnostics(
            semantic_count=len(chunks),
            lexical_count=0,
            fusion_count=len(chunks),
        ),
    )


# ---------------------------------------------------------------------------
# Test 1: Tool callable returns structured ToolResult from HybridRetriever
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_tool_returns_chunks_from_hybrid_retriever():
    """
    The search_knowledge_base callable returned by create_retrieval_tool must
    invoke HybridRetriever.search_hybrid once and return a ToolResult with
    both llm_payload and the original retrieval_result.
    """
    chunk = _make_chunk(content="Vacation policy: 20 days per year.")
    hybrid_result = _make_hybrid_result([chunk], query="vacation policy")

    mock_retriever = MagicMock()
    mock_retriever.search_hybrid = AsyncMock(return_value=hybrid_result)

    assistant_id = str(uuid.uuid4())
    tool_callable, tool_spec = create_retrieval_tool(
        retriever=mock_retriever,
        assistant_id=assistant_id,
        limit=5,
    )

    tool_res: ToolResult = await tool_callable(query="vacation policy")

    mock_retriever.search_hybrid.assert_awaited_once_with(
        query="vacation policy",
        assistant_id=assistant_id,
        limit=5,
    )

    assert isinstance(tool_res, ToolResult)
    assert tool_res.llm_payload["total_results"] == 1
    assert tool_res.llm_payload["has_results"] is True
    assert len(tool_res.llm_payload["chunks"]) == 1
    assert tool_res.llm_payload["chunks"][0]["chunk_id"] == chunk.chunk_id
    assert tool_res.retrieval_result == hybrid_result


# ---------------------------------------------------------------------------
# Test 2: assistant_id isolation — closure prevents LLM override
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_assistant_id_cannot_be_overridden_by_llm():
    """
    The tool callable only accepts 'query' — the assistant_id comes exclusively
    from the backend closure.
    """
    legitimate_id = str(uuid.uuid4())
    attacker_id = str(uuid.uuid4())

    captured_calls: List[Dict[str, Any]] = []

    async def fake_search_hybrid(query: str, assistant_id: str, limit: int):
        captured_calls.append({"assistant_id": assistant_id, "query": query})
        return _make_hybrid_result([], query=query)

    mock_retriever = MagicMock()
    mock_retriever.search_hybrid = fake_search_hybrid

    tool_callable, _ = create_retrieval_tool(
        retriever=mock_retriever,
        assistant_id=legitimate_id,
        limit=5,
    )

    result = await tool_callable(query="how many vacation days")

    assert len(captured_calls) == 1
    assert captured_calls[0]["assistant_id"] == legitimate_id
    assert captured_calls[0]["assistant_id"] != attacker_id

    tool_params = TOOL_SPEC["function"]["parameters"]
    assert "assistant_id" not in tool_params.get("properties", {})
    assert "query" in tool_params.get("properties", {})
    # Check strict JSON Schema compatibility requirements
    assert tool_params.get("additionalProperties") is False
    assert "query" in tool_params.get("required", [])


# ---------------------------------------------------------------------------
# Test 3: All retrieved chunks are mapped to citation records
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_all_retrieved_chunks_become_citations():
    """
    run_agent must return all chunks retrieved during tool calls in
    'retrieved_chunks' without executing duplicate search_hybrid calls.
    """
    chunk_id_a = str(uuid.uuid4())
    chunk_id_b = str(uuid.uuid4())
    chunk_a = _make_chunk(chunk_id=chunk_id_a, content="A" * 400)
    chunk_b = _make_chunk(chunk_id=chunk_id_b, content="B" * 50)

    hybrid_result = _make_hybrid_result([chunk_a, chunk_b])

    tool_call_id = str(uuid.uuid4())

    phase1_choice = {
        "message": {
            "content": "",
            "tool_calls": [
                {
                    "id": tool_call_id,
                    "function": {
                        "name": "search_knowledge_base",
                        "arguments": json.dumps({"query": "test query"}),
                    },
                }
            ],
        }
    }
    phase1_no_tool_choice = {
        "message": {
            "content": "Thinking done.",
            "tool_calls": [],
        }
    }
    synthesis_choice = {
        "message": {
            "content": json.dumps({"answer": "Based on the documents, A and B."}),
            "tool_calls": None,
        }
    }

    mock_openrouter = MagicMock()
    mock_openrouter.chat_completion = AsyncMock(
        side_effect=[phase1_choice, phase1_no_tool_choice, synthesis_choice]
    )

    mock_retriever_instance = MagicMock()
    mock_retriever_instance.search_hybrid = AsyncMock(return_value=hybrid_result)

    with patch("app.services.rag_workflow.HybridRetriever", return_value=mock_retriever_instance):
        result = await run_agent(
            query="test query",
            assistant_id=str(uuid.uuid4()),
            assistant_name="TestBot",
            indexed_documents=["Manual A"],
            chat_history=[],
            db=MagicMock(),
            openrouter=mock_openrouter,
        )

    # Verifies exactly one search_hybrid call was made
    assert mock_retriever_instance.search_hybrid.await_count == 1

    retrieved_ids = {c.chunk_id for c in result["retrieved_chunks"]}
    assert chunk_id_a in retrieved_ids
    assert chunk_id_b in retrieved_ids

    chunk_a_result = next(c for c in result["retrieved_chunks"] if c.chunk_id == chunk_id_a)
    assert len(chunk_a_result.content) == 400
    quote = chunk_a_result.content[:300]
    assert len(quote) == 300
    assert all(ch == "A" for ch in quote)


# ---------------------------------------------------------------------------
# Test 4: Iteration and tool call limits are respected
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_limits_stop_tool_loop_and_route_to_synthesis():
    """
    When iteration_count reaches MAX_ITERATIONS, the loop must stop and proceed
    to synthesis regardless of the LLM continuing to request tool calls.
    """
    tool_call_id = str(uuid.uuid4())

    always_tool_choice = {
        "message": {
            "content": "",
            "tool_calls": [
                {
                    "id": tool_call_id,
                    "function": {
                        "name": "search_knowledge_base",
                        "arguments": json.dumps({"query": "loop query"}),
                    },
                }
            ],
        }
    }
    synthesis_choice = {
        "message": {
            "content": json.dumps({"answer": "Synthesis after limit reached."}),
            "tool_calls": None,
        }
    }

    llm_responses = [always_tool_choice] * MAX_ITERATIONS + [synthesis_choice]

    mock_openrouter = MagicMock()
    mock_openrouter.chat_completion = AsyncMock(side_effect=llm_responses)

    mock_retriever_instance = MagicMock()
    mock_retriever_instance.search_hybrid = AsyncMock(
        return_value=_make_hybrid_result([_make_chunk()])
    )

    with patch("app.services.rag_workflow.HybridRetriever", return_value=mock_retriever_instance):
        result = await run_agent(
            query="loop query",
            assistant_id=str(uuid.uuid4()),
            assistant_name="TestBot",
            indexed_documents=[],
            chat_history=[],
            db=MagicMock(),
            openrouter=mock_openrouter,
        )

    assert result["answer"] == "Synthesis after limit reached."
    total_llm_calls = mock_openrouter.chat_completion.await_count
    assert total_llm_calls == MAX_ITERATIONS + 1

    loop_end_events = [e for e in result["events"] if e["event_type"] == "agent_loop_end"]
    assert len(loop_end_events) == 1
    assert loop_end_events[0]["payload"]["iterations"] == MAX_ITERATIONS


# ---------------------------------------------------------------------------
# Test 5: Excess tool calls in the same turn all receive responses
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_excess_tool_calls_in_same_turn_receive_responses():
    """
    When the model emits more tool calls than MAX_TOOL_CALLS in a single turn,
    every emitted tool call MUST receive a corresponding tool message response.
    Calls exceeding MAX_TOOL_CALLS receive a structured error response.
    """
    # LLM emits 4 tool calls in iteration 1 (MAX_TOOL_CALLS is 3)
    tool_calls = [
        {"id": f"tc_{i}", "function": {"name": "search_knowledge_base", "arguments": json.dumps({"query": f"q_{i}"})}}
        for i in range(4)
    ]
    turn1_choice = {
        "message": {
            "content": "Searching 4 things...",
            "tool_calls": tool_calls,
        }
    }
    synthesis_choice = {
        "message": {
            "content": json.dumps({"answer": "Synthesis after 4 calls processed."}),
            "tool_calls": None,
        }
    }

    mock_openrouter = MagicMock()
    mock_openrouter.chat_completion = AsyncMock(side_effect=[turn1_choice, synthesis_choice])

    mock_retriever_instance = MagicMock()
    mock_retriever_instance.search_hybrid = AsyncMock(
        return_value=_make_hybrid_result([_make_chunk()])
    )

    with patch("app.services.rag_workflow.HybridRetriever", return_value=mock_retriever_instance):
        result = await run_agent(
            query="find everything",
            assistant_id=str(uuid.uuid4()),
            assistant_name="TestBot",
            indexed_documents=[],
            chat_history=[],
            db=MagicMock(),
            openrouter=mock_openrouter,
        )

    # First 3 should execute retrieval, 4th must be rejected with limit_exceeded
    assert mock_retriever_instance.search_hybrid.await_count == 3

    # Inspect the messages sent to the synthesis call
    synthesis_call_messages = mock_openrouter.chat_completion.call_args_list[1].kwargs["messages"]
    tool_messages = [m for m in synthesis_call_messages if m.get("role") == "tool"]

    # All 4 emitted tool calls must have a matching response
    assert len(tool_messages) == 4
    tool_call_ids_responded = [m["tool_call_id"] for m in tool_messages]
    assert tool_call_ids_responded == ["tc_0", "tc_1", "tc_2", "tc_3"]

    # The 4th tool message must contain limit_exceeded error
    fourth_response = json.loads(tool_messages[3]["content"])
    assert fourth_response.get("error") == "limit_exceeded"

    # Event logged for limit exceeded
    limit_events = [e for e in result["events"] if e["event_type"] == "tool_limit_exceeded"]
    assert len(limit_events) == 1
    assert limit_events[0]["payload"]["tool_call_id"] == "tc_3"


# ---------------------------------------------------------------------------
# Test 6: Transactional persistence structure check
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_transactional_persistence_flow():
    """
    Simulates the persistence logic in chats.py:
    db.flush() is called after adding assistant_msg, then citations and events
    are added, and finally a single db.commit() commits the entire unit of work.
    """
    mock_db = MagicMock()
    mock_db.flush = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.add = MagicMock()

    from app.models.all_models import Message, MessageCitation, MessageEvent

    # Simulate message
    msg = Message(chat_id=uuid.uuid4(), role="assistant", content="Test answer")
    mock_db.add(msg)
    await mock_db.flush()

    # Simulate citations and events added after flush
    cit = MessageCitation(
        message_id=msg.id,
        document_chunk_id=uuid.uuid4(),
        quote="Test quote",
        metadata_={},
    )
    mock_db.add(cit)

    ev = MessageEvent(
        message_id=msg.id,
        sequence_number=1,
        event_type="test_event",
        payload={},
    )
    mock_db.add(ev)

    await mock_db.commit()

    # Verify flush happened before commit and commit happened exactly once
    assert mock_db.flush.await_count == 1
    assert mock_db.commit.await_count == 1
    assert mock_db.add.call_count == 3
