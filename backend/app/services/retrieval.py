import math
import logging
import uuid
from collections import defaultdict
from typing import Any, Dict, List, Optional
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.retrieval import (
    HybridSearchResult,
    LexicalResult,
    RetrievalDiagnostics,
    RetrievedChunk,
    SemanticResult,
)
from app.services.openrouter import OpenRouterClient, OpenRouterError

logger = logging.getLogger("manualmind.retrieval")


class RetrievalError(Exception):
    """Base exception for retrieval errors."""
    pass



class HybridRetriever:
    """
    Independent hybrid retrieval service combining semantic search (pgvector),
    lexical search (PostgreSQL Full-Text Search with Portuguese configuration),
    and Reciprocal Rank Fusion (RRF).

    Transaction & Session Contract:
    - The injected `AsyncSession` belongs entirely to the caller.
    - Each database query is executed inside a savepoint (`begin_nested()`) to isolate SQL
      failures and prevent invalidating the caller's outer transaction.
    - To prevent `begin_nested()` from executing an implicit flush of unflushed ORM modifications
      from the caller (which would not be protected by the savepoint and could fail or corrupt state),
      the service strictly enforces that `session.new`, `session.dirty`, and `session.deleted`
      must be empty before entering a savepoint. If pending alterations exist, `RetrievalError` is raised
      without flushing, committing, or rolling back any caller changes.
    """

    def __init__(
        self,
        db: AsyncSession,
        openrouter: OpenRouterClient,
        rrf_k: float = 60.0,
    ) -> None:
        self.db = db
        self.openrouter = openrouter
        try:
            val_k = float(rrf_k)
        except (ValueError, TypeError) as e:
            raise ValueError(f"rrf_k must be a valid positive finite float: {rrf_k}") from e

        if math.isnan(val_k) or math.isinf(val_k) or val_k <= 0:
            raise ValueError(f"rrf_k must be a finite positive number (> 0), got: {rrf_k}")
        self.rrf_k = val_k

    def _validate_assistant_id(self, assistant_id: str) -> uuid.UUID:
        if not assistant_id or not str(assistant_id).strip():
            raise ValueError("assistant_id is mandatory, non-nullable, and cannot be empty.")
        try:
            return uuid.UUID(str(assistant_id).strip())
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid assistant_id UUID format: {assistant_id}") from e

    def _validate_document_ids(self, document_ids: Optional[List[str]]) -> Optional[List[uuid.UUID]]:
        if document_ids is None:
            return None
        if not isinstance(document_ids, list):
            raise ValueError(f"document_ids must be a list of UUID strings or None, got: {type(document_ids).__name__}")
        if len(document_ids) == 0:
            return None

        valid_uuids: List[uuid.UUID] = []
        for d in document_ids:
            if d is None or not str(d).strip():
                raise ValueError(f"Invalid document_id in filter: empty or null element encountered ({d!r}).")
            try:
                valid_uuids.append(uuid.UUID(str(d).strip()))
            except (ValueError, TypeError) as e:
                raise ValueError(f"Invalid document_id UUID format in filter: {d}") from e
        return valid_uuids

    def _validate_session_clean(self) -> None:
        """
        Ensures the caller's session has no pending unflushed ORM modifications.
        SQLAlchemy's `begin_nested()` triggers an implicit flush before establishing
        the savepoint. An error during that implicit flush would not be isolated
        by the savepoint and would compromise the outer transaction.
        We reject execution explicitly if pending changes are detected.
        """
        new_objs = getattr(self.db, "new", None)
        dirty_objs = getattr(self.db, "dirty", None)
        deleted_objs = getattr(self.db, "deleted", None)

        if bool(new_objs) or bool(dirty_objs) or bool(deleted_objs):
            counts = f"new={len(new_objs) if new_objs else 0}, dirty={len(dirty_objs) if dirty_objs else 0}, deleted={len(deleted_objs) if deleted_objs else 0}"
            raise RetrievalError(
                f"Cannot execute retrieval: AsyncSession has pending unflushed alterations ({counts}). "
                "The caller must flush or commit its pending changes before invoking retrieval to protect transaction integrity."
            )

    async def search_semantic(
        self,
        query: str,
        assistant_id: str,
        limit: int = 10,
        document_ids: Optional[List[str]] = None,
    ) -> List[SemanticResult]:
        """
        Executes semantic vector search using pgvector cosine distance on document_chunks.
        Converts cosine distance to cosine similarity: (1.0 - distance). Higher is better.
        Strictly enforces assistant_id isolation and indexed status.
        """
        asst_uuid = self._validate_assistant_id(assistant_id)
        doc_uuids = self._validate_document_ids(document_ids)

        clean_query = (query or "").strip()
        if not clean_query:
            return []

        if not isinstance(limit, int) or limit <= 0:
            limit = 10

        # Generate query embedding
        query_emb_list = await self.openrouter.generate_embeddings([clean_query])
        if not query_emb_list or not query_emb_list[0]:
            raise RetrievalError("Failed to generate embedding: empty vector returned from provider.")
        query_emb = query_emb_list[0]

        # Validate embedding dimensions and finite numeric components
        if len(query_emb) != 1536:
            raise RetrievalError(
                f"Embedding dimension mismatch: expected 1536, provider returned {len(query_emb)}."
            )

        for comp_idx, val in enumerate(query_emb):
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                raise RetrievalError(
                    f"Embedding component {comp_idx} is not numeric ({type(val).__name__}: {val})."
                )
            if math.isnan(val) or math.isinf(val):
                raise RetrievalError(
                    f"Embedding component {comp_idx} contains non-finite value ({val})."
                )

        query_emb_str = f"[{','.join(str(x) for x in query_emb)}]"

        doc_filter_clause = ""
        params: Dict[str, Any] = {
            "query_emb": query_emb_str,
            "assistant_id": asst_uuid,
            "limit": limit,
        }

        if doc_uuids:
            doc_filter_clause = "AND d.id = ANY(:doc_ids)"
            params["doc_ids"] = doc_uuids

        sql = f"""
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
              {doc_filter_clause}
            ORDER BY distance ASC
            LIMIT :limit;
        """

        self._validate_session_clean()
        async with self.db.begin_nested():
            res = await self.db.execute(text(sql), params)
            rows = res.mappings().all()

        results: List[SemanticResult] = []
        for idx, row in enumerate(rows, start=1):
            distance = float(row["distance"])
            similarity = 1.0 - distance
            results.append(
                SemanticResult(
                    chunk_id=str(row["chunk_id"]),
                    document_id=str(row["document_id"]),
                    document_name=row["document_name"],
                    content=row["content"],
                    metadata=dict(row["metadata"] or {}),
                    similarity_score=round(similarity, 6),
                    rank=idx,
                )
            )

        return results

    async def search_lexical(
        self,
        query: str,
        assistant_id: str,
        limit: int = 10,
        document_ids: Optional[List[str]] = None,
    ) -> List[LexicalResult]:
        """
        Executes lexical full-text search using PostgreSQL tsvector with 'portuguese' configuration.
        Uses OR-based tsquery across extracted lexemes so multi-term queries match documents with any term,
        ranked by ts_rank_cd.
        Strictly enforces assistant_id isolation and indexed status.
        Never executes unconditional arbitrary fallbacks on failure.
        """
        asst_uuid = self._validate_assistant_id(assistant_id)
        doc_uuids = self._validate_document_ids(document_ids)

        clean_query = (query or "").strip()
        if not clean_query:
            return []

        if not isinstance(limit, int) or limit <= 0:
            limit = 10

        doc_filter_clause = ""
        params: Dict[str, Any] = {
            "query": clean_query,
            "assistant_id": asst_uuid,
            "limit": limit,
        }

        if doc_uuids:
            doc_filter_clause = "AND d.id = ANY(:doc_ids)"
            params["doc_ids"] = doc_uuids

        # Construct OR-based tsquery using unnest(to_tsvector('portuguese', :query)).
        # If the query contains only punctuation/stopwords, array_to_string returns empty string.
        # coalesce / nullif ensures safety.
        sql = f"""
            WITH q AS (
                SELECT to_tsquery(
                    'portuguese',
                    NULLIF(
                        array_to_string(
                            ARRAY(
                                SELECT DISTINCT lexeme
                                FROM unnest(to_tsvector('portuguese', :query))
                                WHERE lexeme IS NOT NULL AND lexeme != ''
                            ),
                            ' | '
                        ),
                        ''
                    )
                ) AS query_ts
            )
            SELECT
                dc.id AS chunk_id,
                d.id AS document_id,
                d.name AS document_name,
                dc.content,
                dc.metadata,
                ts_rank_cd(to_tsvector('portuguese', dc.content), q.query_ts) AS rank_score
            FROM document_chunks dc
            JOIN documents d ON dc.document_id = d.id
            CROSS JOIN q
            WHERE q.query_ts IS NOT NULL
              AND d.assistant_id = :assistant_id
              AND d.processing_status = 'indexed'
              AND to_tsvector('portuguese', dc.content) @@ q.query_ts
              {doc_filter_clause}
            ORDER BY rank_score DESC
            LIMIT :limit;
        """

        self._validate_session_clean()
        async with self.db.begin_nested():
            res = await self.db.execute(text(sql), params)
            rows = res.mappings().all()

        results: List[LexicalResult] = []
        for idx, row in enumerate(rows, start=1):
            results.append(
                LexicalResult(
                    chunk_id=str(row["chunk_id"]),
                    document_id=str(row["document_id"]),
                    document_name=row["document_name"],
                    content=row["content"],
                    metadata=dict(row["metadata"] or {}),
                    rank_score=round(float(row["rank_score"]), 6),
                    rank=idx,
                )
            )

        return results

    async def search_hybrid(
        self,
        query: str,
        assistant_id: str,
        limit: int = 6,
        semantic_limit: int = 10,
        lexical_limit: int = 10,
        document_ids: Optional[List[str]] = None,
    ) -> HybridSearchResult:
        """
        Performs hybrid retrieval combining semantic search (pgvector) and lexical search (FTS)
        via Reciprocal Rank Fusion (RRF).
        Handles graceful degradation if one retrieval method fails.
        Raises RetrievalError if both methods fail.
        Validates input arguments (assistant_id, document_ids) first to propagate ValueErrors directly.
        """
        # Validate inputs first so ValueError is never swallowed into search diagnostics
        self._validate_assistant_id(assistant_id)
        self._validate_document_ids(document_ids)

        if not isinstance(limit, int) or limit <= 0:
            limit = 6
        if not isinstance(semantic_limit, int) or semantic_limit <= 0:
            semantic_limit = 10
        if not isinstance(lexical_limit, int) or lexical_limit <= 0:
            lexical_limit = 10

        clean_query = (query or "").strip()
        diagnostics = RetrievalDiagnostics()

        if not clean_query:
            return HybridSearchResult(
                query=query,
                total_results=0,
                results=[],
                has_retrieved_results=False,
                diagnostics=diagnostics,
            )

        semantic_results: List[SemanticResult] = []
        lexical_results: List[LexicalResult] = []

        # 1. Semantic Search execution with error capture
        try:
            semantic_results = await self.search_semantic(
                query=clean_query,
                assistant_id=assistant_id,
                limit=semantic_limit,
                document_ids=document_ids,
            )
            diagnostics.semantic_count = len(semantic_results)
        except (OpenRouterError, SQLAlchemyError, RetrievalError) as sem_err:
            diagnostics.semantic_failed = True
            diagnostics.semantic_error = f"{type(sem_err).__name__}: {str(sem_err)}"
            logger.warning("Semantic retrieval failed: %s", diagnostics.semantic_error)

        # 2. Lexical Search execution with error capture
        try:
            lexical_results = await self.search_lexical(
                query=clean_query,
                assistant_id=assistant_id,
                limit=lexical_limit,
                document_ids=document_ids,
            )
            diagnostics.lexical_count = len(lexical_results)
        except (SQLAlchemyError, RetrievalError) as lex_err:
            diagnostics.lexical_failed = True
            diagnostics.lexical_error = f"{type(lex_err).__name__}: {str(lex_err)}"
            logger.warning("Lexical retrieval failed: %s", diagnostics.lexical_error)

        # 3. If both retrieval methods failed, raise RetrievalError
        if diagnostics.semantic_failed and diagnostics.lexical_failed:
            raise RetrievalError(
                f"Both retrieval methods failed. Semantic error: {diagnostics.semantic_error}. "
                f"Lexical error: {diagnostics.lexical_error}"
            )

        # 4. RRF Fusion (1-indexed ranks)
        # RRF formula: sum_{m in methods} 1.0 / (rrf_k + rank_m)
        rrf_scores: Dict[str, float] = defaultdict(float)
        chunk_data: Dict[str, Dict[str, Any]] = {}

        for item in semantic_results:
            cid = item.chunk_id
            rrf_scores[cid] += 1.0 / (self.rrf_k + item.rank)
            chunk_data[cid] = {
                "chunk_id": cid,
                "document_id": item.document_id,
                "document_name": item.document_name,
                "content": item.content,
                "metadata": item.metadata,
                "semantic_rank": item.rank,
                "semantic_score": item.similarity_score,
                "lexical_rank": None,
                "lexical_score": None,
                "retrieval_source": "semantic",
            }

        for item in lexical_results:
            cid = item.chunk_id
            rrf_scores[cid] += 1.0 / (self.rrf_k + item.rank)
            if cid in chunk_data:
                chunk_data[cid]["lexical_rank"] = item.rank
                chunk_data[cid]["lexical_score"] = item.rank_score
                chunk_data[cid]["retrieval_source"] = "hybrid"
            else:
                chunk_data[cid] = {
                    "chunk_id": cid,
                    "document_id": item.document_id,
                    "document_name": item.document_name,
                    "content": item.content,
                    "metadata": item.metadata,
                    "semantic_rank": None,
                    "semantic_score": None,
                    "lexical_rank": item.rank,
                    "lexical_score": item.rank_score,
                    "retrieval_source": "lexical",
                }

        # Sort descending by RRF score
        sorted_cids = sorted(rrf_scores.keys(), key=lambda cid: rrf_scores[cid], reverse=True)
        top_cids = sorted_cids[:limit]

        retrieved_chunks: List[RetrievedChunk] = []
        for cid in top_cids:
            data = chunk_data[cid]
            meta = data["metadata"] or {}
            page = meta.get("page")
            page_end = meta.get("page_end")

            retrieved_chunks.append(
                RetrievedChunk(
                    chunk_id=data["chunk_id"],
                    document_id=data["document_id"],
                    document_name=data["document_name"],
                    content=data["content"],
                    page=page,
                    page_end=page_end,
                    metadata=meta,
                    semantic_rank=data["semantic_rank"],
                    semantic_score=data["semantic_score"],
                    lexical_rank=data["lexical_rank"],
                    lexical_score=data["lexical_score"],
                    rrf_score=round(rrf_scores[cid], 6),
                    retrieval_source=data["retrieval_source"],
                )
            )

        diagnostics.fusion_count = len(retrieved_chunks)

        return HybridSearchResult(
            query=clean_query,
            total_results=len(retrieved_chunks),
            results=retrieved_chunks,
            has_retrieved_results=bool(retrieved_chunks),
            diagnostics=diagnostics,
        )
