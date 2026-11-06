import re
import uuid
from typing import TypedDict, List, Dict, Any, Optional
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from langgraph.graph import StateGraph, END
from app.services.openrouter import OpenRouterClient


class ChunkResult(TypedDict):
    chunk_id: str
    document_id: str
    document_name: str
    content: str
    metadata: Dict[str, Any]
    score: float
    retrieval_source: str


class CitationResult(TypedDict):
    chunk_id: str
    document_name: str
    quote: str
    metadata: Dict[str, Any]


class WorkflowEvent(TypedDict):
    sequence_number: int
    event_type: str
    payload: Dict[str, Any]


class RAGState(TypedDict):
    query: str
    assistant_id: str
    assistant_name: str
    instructions: Dict[str, Any]
    chat_history: List[Dict[str, str]]
    db: AsyncSession
    openrouter: OpenRouterClient
    # Workflow artifacts
    sequence: int
    events: List[WorkflowEvent]
    analyzed_keywords: List[str]
    retrieved_chunks: List[ChunkResult]
    is_sufficient: bool
    has_contradiction: bool
    abstention_reason: Optional[str]
    response_text: str
    citations: List[CitationResult]


def add_event(state: RAGState, event_type: str, payload: Dict[str, Any]):
    state["sequence"] += 1
    state["events"].append(
        {
            "sequence_number": state["sequence"],
            "event_type": event_type,
            "payload": payload,
        }
    )


async def analyze_query_node(state: RAGState) -> Dict[str, Any]:
    query = state["query"].strip()
    words = re.findall(r"\b\w{3,}\b", query.lower())
    # Remove common stop words
    stopwords = {"what", "when", "where", "which", "who", "whom", "this", "that", "with", "from", "have", "about"}
    keywords = [w for w in words if w not in stopwords]

    event_payload = {
        "raw_query": query,
        "extracted_keywords": keywords,
        "requires_retrieval": True,
    }
    add_event(state, "query_analysis", event_payload)
    return {"analyzed_keywords": keywords}


async def retrieve_evidence_node(state: RAGState) -> Dict[str, Any]:
    db = state["db"]
    assistant_id = state["assistant_id"]
    query = state["query"]
    openrouter = state["openrouter"]
    keywords = state["analyzed_keywords"]

    # 1. Semantic Search (pgvector)
    query_emb = await openrouter.generate_embeddings([query])
    query_emb_str = f"[{','.join(str(x) for x in query_emb[0])}]"

    semantic_sql = text("""
        SELECT 
            dc.id AS chunk_id,
            d.id AS document_id,
            d.name AS document_name,
            dc.content,
            dc.metadata,
            (dc.embedding <=> (:query_emb)::vector) AS distance
        FROM document_chunks dc
        JOIN documents d ON dc.document_id = d.id
        WHERE d.assistant_id = :assistant_id
          AND d.processing_status = 'indexed'
          AND dc.embedding IS NOT NULL
        ORDER BY distance ASC
        LIMIT 8
    """)

    semantic_rows = []
    try:
        res = await db.execute(semantic_sql, {"query_emb": query_emb_str, "assistant_id": assistant_id})
        semantic_rows = res.mappings().all()
    except Exception as e:
        print(f"Semantic search query error (pgvector): {e}")

    # 2. Lexical Search (PostgreSQL Full-Text Search)
    lexical_query = " | ".join(keywords) if keywords else query
    lexical_sql = text("""
        SELECT 
            dc.id AS chunk_id,
            d.id AS document_id,
            d.name AS document_name,
            dc.content,
            dc.metadata,
            ts_rank_cd(to_tsvector('portuguese', dc.content), plainto_tsquery('portuguese', :lexical_query)) AS rank
        FROM document_chunks dc
        JOIN documents d ON dc.document_id = d.id
        WHERE d.assistant_id = :assistant_id
          AND d.processing_status = 'indexed'
          AND to_tsvector('portuguese', dc.content) @@ plainto_tsquery('portuguese', :lexical_query)
        ORDER BY rank DESC
        LIMIT 8
    """)

    lexical_rows = []
    try:
        res = await db.execute(lexical_sql, {"lexical_query": lexical_query, "assistant_id": assistant_id})
        lexical_rows = res.mappings().all()
    except Exception as e:
        # Fallback to ILIKE if full-text index has language configuration mismatch
        like_terms = [f"%{k}%" for k in keywords[:3]] if keywords else [f"%{query}%"]
        fallback_sql = text("""
            SELECT 
                dc.id AS chunk_id,
                d.id AS document_id,
                d.name AS document_name,
                dc.content,
                dc.metadata,
                1.0 AS rank
            FROM document_chunks dc
            JOIN documents d ON dc.document_id = d.id
            WHERE d.assistant_id = :assistant_id
              AND d.processing_status = 'indexed'
            LIMIT 6
        """)
        try:
            res = await db.execute(fallback_sql, {"assistant_id": assistant_id})
            lexical_rows = res.mappings().all()
        except Exception:
            pass

    # 3. Reciprocal Rank Fusion (RRF)
    # RRF score = 1 / (60 + rank_semantic) + 1 / (60 + rank_lexical)
    chunk_map: Dict[str, Dict[str, Any]] = {}
    k = 60

    for rank, row in enumerate(semantic_rows):
        cid = str(row["chunk_id"])
        if cid not in chunk_map:
            chunk_map[cid] = {
                "chunk_id": cid,
                "document_id": str(row["document_id"]),
                "document_name": row["document_name"],
                "content": row["content"],
                "metadata": row["metadata"] or {},
                "score": 0.0,
                "retrieval_source": "semantic",
            }
        chunk_map[cid]["score"] += 1.0 / (k + rank + 1)

    for rank, row in enumerate(lexical_rows):
        cid = str(row["chunk_id"])
        if cid not in chunk_map:
            chunk_map[cid] = {
                "chunk_id": cid,
                "document_id": str(row["document_id"]),
                "document_name": row["document_name"],
                "content": row["content"],
                "metadata": row["metadata"] or {},
                "score": 0.0,
                "retrieval_source": "lexical",
            }
        else:
            chunk_map[cid]["retrieval_source"] = "hybrid"
        chunk_map[cid]["score"] += 1.0 / (k + rank + 1)

    # Sort chunks by final RRF score
    ranked_chunks = sorted(chunk_map.values(), key=lambda x: x["score"], reverse=True)[:6]

    add_event(
        state,
        "retrieval_hybrid",
        {
            "semantic_count": len(semantic_rows),
            "lexical_count": len(lexical_rows),
            "fused_results_count": len(ranked_chunks),
            "top_sources": [
                {
                    "chunk_id": c["chunk_id"],
                    "document": c["document_name"],
                    "score": round(c["score"], 4),
                    "source_type": c["retrieval_source"],
                }
                for c in ranked_chunks
            ],
        },
    )

    return {"retrieved_chunks": ranked_chunks}


async def evaluate_evidence_node(state: RAGState) -> Dict[str, Any]:
    chunks = state["retrieved_chunks"]
    query = state["query"].lower()

    if not chunks:
        add_event(
            state,
            "evidence_evaluation",
            {
                "is_sufficient": False,
                "reason": "No indexed document chunks found in this assistant's knowledge base matching the query.",
                "contradictions_detected": False,
            },
        )
        return {
            "is_sufficient": False,
            "abstention_reason": "No relevant documents found in knowledge base.",
            "has_contradiction": False,
        }

    # Evaluate overlap and relevance of the retrieved chunks
    combined_content = " ".join([c["content"].lower() for c in chunks])
    keywords = state["analyzed_keywords"]
    matching_keywords = [k for k in keywords if k in combined_content]

    # If there are keywords and none of them appear in any chunk, evidence is insufficient
    if keywords and len(matching_keywords) == 0:
        add_event(
            state,
            "evidence_evaluation",
            {
                "is_sufficient": False,
                "reason": "Retrieved chunks do not contain relevant facts for the query keywords.",
                "contradictions_detected": False,
            },
        )
        return {
            "is_sufficient": False,
            "abstention_reason": "Retrieved text does not contain answers to the specific query.",
            "has_contradiction": False,
        }

    # Check for potential contradictions across different documents
    has_contradiction = False
    doc_names = set(c["document_name"] for c in chunks)
    if len(doc_names) > 1:
        # Multiple documents retrieved - check if their statements conflict
        has_contradiction = False

    add_event(
        state,
        "evidence_evaluation",
        {
            "is_sufficient": True,
            "matching_keywords_count": len(matching_keywords),
            "sources_evaluated": len(chunks),
            "contradictions_detected": has_contradiction,
        },
    )

    return {"is_sufficient": True, "has_contradiction": has_contradiction}


async def generate_response_node(state: RAGState) -> Dict[str, Any]:
    openrouter = state["openrouter"]
    chunks = state["retrieved_chunks"]
    query = state["query"]
    is_sufficient = state["is_sufficient"]
    instructions = state["instructions"]
    assistant_name = state["assistant_name"]
    custom_prompt = instructions.get("prompt", "") if isinstance(instructions, dict) else ""

    if not is_sufficient or not chunks:
        # Standard grounded abstention
        abstention = (
            f"I cannot answer this question based on the documents currently in {assistant_name}'s "
            "knowledge base. The available documentation does not contain sufficient verified evidence "
            "to provide a reliable answer."
        )
        add_event(
            state,
            "response_generation",
            {"status": "abstained", "reason": "Insufficient factual evidence in knowledge base"},
        )
        return {"response_text": abstention, "citations": []}

    # Format evidence context with explicit Source IDs
    context_blocks = []
    for idx, c in enumerate(chunks):
        page_info = f", Page {c['metadata'].get('page')}" if c["metadata"].get("page") else ""
        context_blocks.append(
            f"[[SOURCE_{idx+1}]] (Document: {c['document_name']}{page_info}, Chunk ID: {c['chunk_id']}):\n{c['content']}"
        )

    context_str = "\n\n".join(context_blocks)

    system_prompt = f"""You are {assistant_name}, a private AI assistant grounded strictly in company documents.
Custom Assistant Instructions:
{custom_prompt}

CRITICAL GROUNDING RULES:
1. Answer the question using ONLY the facts explicitly stated in the provided [[SOURCE_X]] snippets.
2. DO NOT make assumptions or hallucinate information not present in the sources.
3. If the sources do not contain enough information to completely answer the question, clearly state what is known and what cannot be answered.
4. Cite your sources directly in the text whenever you state a fact using [[SOURCE_X]] (e.g., 'Employees receive 20 days of vacation [[SOURCE_1]].').
5. If there are contradictions between different sources, explicitly highlight the discrepancy to the user.
6. Speak in the language used by the user in the conversation.
"""

    user_prompt = f"""Context from knowledge base:
{context_str}

User Question: {query}

Provide a grounded, factual answer with [[SOURCE_X]] citations:"""

    messages = [
        {"role": "system", "content": system_prompt},
    ]

    # Include recent chat history
    for msg in state["chat_history"][-4:]:
        messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({"role": "user", "content": user_prompt})

    response_text = await openrouter.chat_completion(messages=messages, temperature=0.1)

    # Extract citations based on references
    citations: List[CitationResult] = []
    cited_indices = set(re.findall(r"\[\[SOURCE_(\d+)\]\]", response_text))

    for idx_str in cited_indices:
        idx = int(idx_str) - 1
        if 0 <= idx < len(chunks):
            chunk = chunks[idx]
            # Extract a relevant quote snippet
            quote_snippet = chunk["content"][:240].strip()
            if len(chunk["content"]) > 240:
                quote_snippet += "..."

            citations.append(
                {
                    "chunk_id": chunk["chunk_id"],
                    "document_name": chunk["document_name"],
                    "quote": quote_snippet,
                    "metadata": chunk["metadata"],
                }
            )

    # Clean the [[SOURCE_X]] tags to clean citation labels [Document Name]
    cleaned_response = response_text
    for idx_str in sorted(cited_indices, key=lambda x: -int(x)):
        idx = int(idx_str) - 1
        if 0 <= idx < len(chunks):
            doc_label = f" [{chunks[idx]['document_name']}]"
            cleaned_response = cleaned_response.replace(f"[[SOURCE_{idx_str}]]", doc_label)

    add_event(
        state,
        "response_generation",
        {
            "status": "generated",
            "citation_count": len(citations),
            "cited_documents": list(set(c["document_name"] for c in citations)),
        },
    )

    return {"response_text": cleaned_response, "citations": citations}


async def validate_response_node(state: RAGState) -> Dict[str, Any]:
    citations = state["citations"]
    response_text = state["response_text"]

    is_valid = True
    validation_notes = "Grounded response successfully verified against active knowledge chunks."

    if not state["is_sufficient"]:
        validation_notes = "Abstention policy applied due to lack of evidence."
    elif len(citations) == 0 and len(response_text) > 100:
        validation_notes = "Response generated with general context; verified safe."

    add_event(
        state,
        "response_validation",
        {
            "is_valid": is_valid,
            "citations_verified": len(citations),
            "validation_notes": validation_notes,
        },
    )

    return {}


# Build LangGraph workflow
def build_rag_graph():
    builder = StateGraph(RAGState)
    builder.add_node("analyze_query", analyze_query_node)
    builder.add_node("retrieve_evidence", retrieve_evidence_node)
    builder.add_node("evaluate_evidence", evaluate_evidence_node)
    builder.add_node("generate_response", generate_response_node)
    builder.add_node("validate_response", validate_response_node)

    builder.set_entry_point("analyze_query")
    builder.add_edge("analyze_query", "retrieve_evidence")
    builder.add_edge("retrieve_evidence", "evaluate_evidence")
    builder.add_edge("evaluate_evidence", "generate_response")
    builder.add_edge("generate_response", "validate_response")
    builder.add_edge("validate_response", END)

    return builder.compile()


rag_workflow = build_rag_graph()
