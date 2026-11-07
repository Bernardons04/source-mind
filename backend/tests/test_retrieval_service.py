import uuid
from unittest.mock import AsyncMock, MagicMock
import pytest

from app.schemas.retrieval import (
    HybridSearchResult,
    LexicalResult,
    RetrievalDiagnostics,
    RetrievedChunk,
    SemanticResult,
)
from app.services.retrieval import HybridRetriever, RetrievalError
from app.services.openrouter import OpenRouterClient, OpenRouterNonRecoverableError


def create_mock_session():
    from sqlalchemy.ext.asyncio import AsyncSession
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock()
    nested = MagicMock()
    nested.__aenter__ = AsyncMock(return_value=nested)
    nested.__aexit__ = AsyncMock(return_value=None)
    session.begin_nested = MagicMock(return_value=nested)
    session.new = set()
    session.dirty = set()
    session.deleted = set()
    return session


def create_mock_openrouter():
    client = MagicMock(spec=OpenRouterClient)
    client.generate_embeddings = AsyncMock(return_value=[[0.1] * 1536])
    return client


@pytest.mark.anyio
async def test_semantic_search_returns_structured_results():
    """1. Semantic search returns correctly structured results."""
    mock_db = create_mock_session()
    mock_openrouter = create_mock_openrouter()

    chunk_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    mock_result = MagicMock()
    mock_result.mappings.return_value.all.return_value = [
        {
            "chunk_id": chunk_id,
            "document_id": doc_id,
            "document_name": "manual.pdf",
            "content": "Conteúdo de exemplo do manual.",
            "metadata": {"page": 2, "page_end": 3},
            "distance": 0.15,
        }
    ]
    mock_db.execute.return_value = mock_result

    retriever = HybridRetriever(db=mock_db, openrouter=mock_openrouter)
    assistant_id = str(uuid.uuid4())
    results = await retriever.search_semantic("como configurar", assistant_id, limit=5)

    assert len(results) == 1
    res = results[0]
    assert isinstance(res, SemanticResult)
    assert res.chunk_id == str(chunk_id)
    assert res.document_id == str(doc_id)
    assert res.document_name == "manual.pdf"
    assert res.similarity_score == pytest.approx(0.85, 0.0001)  # 1.0 - 0.15
    assert res.rank == 1
    assert res.metadata["page"] == 2


@pytest.mark.anyio
async def test_lexical_search_returns_structured_results():
    """2. Lexical search returns correctly structured results."""
    mock_db = create_mock_session()
    mock_openrouter = create_mock_openrouter()

    chunk_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    mock_result = MagicMock()
    mock_result.mappings.return_value.all.return_value = [
        {
            "chunk_id": chunk_id,
            "document_id": doc_id,
            "document_name": "guia.docx",
            "content": "Instruções passo a passo.",
            "metadata": {"page": None},
            "rank_score": 0.42,
        }
    ]
    mock_db.execute.return_value = mock_result

    retriever = HybridRetriever(db=mock_db, openrouter=mock_openrouter)
    assistant_id = str(uuid.uuid4())
    results = await retriever.search_lexical("instruções", assistant_id, limit=5)

    assert len(results) == 1
    res = results[0]
    assert isinstance(res, LexicalResult)
    assert res.chunk_id == str(chunk_id)
    assert res.rank_score == 0.42
    assert res.rank == 1


@pytest.mark.anyio
async def test_hybrid_search_merges_results_and_calculates_rrf():
    """3. Hybrid search merges results correctly.
       4. RRF scores are calculated correctly using 1-indexed ranks.
    """
    mock_db = create_mock_session()
    mock_openrouter = create_mock_openrouter()

    chunk_a = str(uuid.uuid4())
    chunk_b = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())

    retriever = HybridRetriever(db=mock_db, openrouter=mock_openrouter, rrf_k=60.0)

    # Mock search_semantic: chunk_a is rank 1, chunk_b is rank 2
    sem_res = [
        SemanticResult(
            chunk_id=chunk_a,
            document_id=doc_id,
            document_name="doc.pdf",
            content="A",
            metadata={"page": 1},
            similarity_score=0.9,
            rank=1,
        ),
        SemanticResult(
            chunk_id=chunk_b,
            document_id=doc_id,
            document_name="doc.pdf",
            content="B",
            metadata={"page": 2},
            similarity_score=0.8,
            rank=2,
        ),
    ]

    # Mock search_lexical: chunk_b is rank 1
    lex_res = [
        LexicalResult(
            chunk_id=chunk_b,
            document_id=doc_id,
            document_name="doc.pdf",
            content="B",
            metadata={"page": 2},
            rank_score=0.5,
            rank=1,
        )
    ]

    retriever.search_semantic = AsyncMock(return_value=sem_res)
    retriever.search_lexical = AsyncMock(return_value=lex_res)

    assistant_id = str(uuid.uuid4())
    hybrid_res = await retriever.search_hybrid("query", assistant_id, limit=6)

    assert isinstance(hybrid_res, HybridSearchResult)
    assert len(hybrid_res.results) == 2

    # Verify RRF 1-indexed formula:
    # chunk_a: 1.0 / (60 + 1) = 1/61 = 0.016393
    # chunk_b: 1.0 / (60 + 2) + 1.0 / (60 + 1) = 1/62 + 1/61 = 0.016129 + 0.016393 = 0.032522
    # chunk_b must be top result!
    top = hybrid_res.results[0]
    second = hybrid_res.results[1]

    assert top.chunk_id == chunk_b
    assert top.retrieval_source == "hybrid"
    assert top.semantic_rank == 2
    assert top.lexical_rank == 1
    assert top.rrf_score == pytest.approx(1.0 / 62 + 1.0 / 61, abs=1e-5)

    assert second.chunk_id == chunk_a
    assert second.retrieval_source == "semantic"
    assert second.semantic_rank == 1
    assert second.lexical_rank is None
    assert second.rrf_score == pytest.approx(1.0 / 61, abs=1e-5)


@pytest.mark.anyio
async def test_duplicate_chunks_merged_preserving_ranks():
    """5. Duplicate chunks are merged without losing individual ranking information."""
    mock_db = create_mock_session()
    mock_openrouter = create_mock_openrouter()

    chunk_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())

    retriever = HybridRetriever(db=mock_db, openrouter=mock_openrouter, rrf_k=60.0)
    retriever.search_semantic = AsyncMock(
        return_value=[
            SemanticResult(
                chunk_id=chunk_id,
                document_id=doc_id,
                document_name="manual.pdf",
                content="Same content",
                metadata={"page": 5},
                similarity_score=0.95,
                rank=1,
            )
        ]
    )
    retriever.search_lexical = AsyncMock(
        return_value=[
            LexicalResult(
                chunk_id=chunk_id,
                document_id=doc_id,
                document_name="manual.pdf",
                content="Same content",
                metadata={"page": 5},
                rank_score=0.88,
                rank=1,
            )
        ]
    )

    res = await retriever.search_hybrid("termo", str(uuid.uuid4()))
    assert len(res.results) == 1
    c = res.results[0]
    assert c.chunk_id == chunk_id
    assert c.semantic_rank == 1
    assert c.semantic_score == 0.95
    assert c.lexical_rank == 1
    assert c.lexical_score == 0.88
    assert c.retrieval_source == "hybrid"
    assert c.rrf_score == pytest.approx(2.0 / 61.0, 0.00001)


@pytest.mark.anyio
async def test_assistant_filtering_is_always_applied():
    """6. Assistant filtering is always applied and mandatory on both semantic and lexical searches."""
    mock_db = create_mock_session()
    mock_openrouter = create_mock_openrouter()

    mock_result = MagicMock()
    mock_result.mappings.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result

    retriever = HybridRetriever(db=mock_db, openrouter=mock_openrouter)

    # Empty or invalid assistant_id must raise ValueError
    with pytest.raises(ValueError, match="assistant_id is mandatory"):
        await retriever.search_semantic("query", "")

    with pytest.raises(ValueError, match="Invalid assistant_id"):
        await retriever.search_semantic("query", "not-a-uuid")

    with pytest.raises(ValueError, match="assistant_id is mandatory"):
        await retriever.search_lexical("query", "   ")

    with pytest.raises(ValueError, match="Invalid assistant_id"):
        await retriever.search_lexical("query", "invalid-uuid-123")

    # Valid UUID must pass assistant_id as bind parameter in semantic search
    asst_id = str(uuid.uuid4())
    await retriever.search_semantic("query", asst_id)
    call_args_sem = mock_db.execute.call_args
    assert call_args_sem is not None
    sql_sem = str(call_args_sem[0][0])
    params_sem = call_args_sem[0][1]
    assert "d.assistant_id = :assistant_id" in sql_sem
    assert "d.processing_status = 'indexed'" in sql_sem
    assert params_sem["assistant_id"] == uuid.UUID(asst_id)

    # Valid UUID must pass assistant_id as bind parameter in lexical search
    await retriever.search_lexical("query", asst_id)
    call_args_lex = mock_db.execute.call_args
    assert call_args_lex is not None
    sql_lex = str(call_args_lex[0][0])
    params_lex = call_args_lex[0][1]
    assert "d.assistant_id = :assistant_id" in sql_lex
    assert "d.processing_status = 'indexed'" in sql_lex
    assert params_lex["assistant_id"] == uuid.UUID(asst_id)


@pytest.mark.anyio
async def test_optional_document_filtering_cannot_bypass_assistant_isolation():
    """7. Optional document filtering cannot bypass assistant isolation and rejects empty/invalid items."""
    mock_db = create_mock_session()
    mock_openrouter = create_mock_openrouter()

    mock_result = MagicMock()
    mock_result.mappings.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result

    retriever = HybridRetriever(db=mock_db, openrouter=mock_openrouter)
    asst_id = str(uuid.uuid4())
    doc_id1 = str(uuid.uuid4())
    doc_id2 = str(uuid.uuid4())

    await retriever.search_semantic("query", asst_id, document_ids=[doc_id1, doc_id2])
    call_args = mock_db.execute.call_args
    sql_text = str(call_args[0][0])
    params = call_args[0][1]

    # Both assistant_id AND doc_ids must be present in the query
    assert "d.assistant_id = :assistant_id" in sql_text
    assert "d.id = ANY(:doc_ids)" in sql_text
    assert params["assistant_id"] == uuid.UUID(asst_id)
    assert params["doc_ids"] == [uuid.UUID(doc_id1), uuid.UUID(doc_id2)]

    # Empty list evaluates to None (no filter added)
    await retriever.search_semantic("query", asst_id, document_ids=[])
    call_empty_list = mock_db.execute.call_args
    assert "d.id = ANY(:doc_ids)" not in str(call_empty_list[0][0])

    # None evaluates to None (no filter added)
    await retriever.search_semantic("query", asst_id, document_ids=None)
    call_none = mock_db.execute.call_args
    assert "d.id = ANY(:doc_ids)" not in str(call_none[0][0])

    # Non-list type raises ValueError
    with pytest.raises(ValueError, match="document_ids must be a list"):
        await retriever.search_semantic("query", asst_id, document_ids="not-a-list")  # type: ignore

    # List containing empty string or None must raise ValueError, not be silently skipped
    with pytest.raises(ValueError, match="empty or null element"):
        await retriever.search_semantic("query", asst_id, document_ids=[doc_id1, ""])

    with pytest.raises(ValueError, match="empty or null element"):
        await retriever.search_semantic("query", asst_id, document_ids=[doc_id1, None])  # type: ignore

    # Invalid document UUID format raises ValueError
    with pytest.raises(ValueError, match="Invalid document_id UUID format"):
        await retriever.search_semantic("query", asst_id, document_ids=["invalid-doc-id"])


@pytest.mark.anyio
async def test_empty_successful_searches_are_handled_correctly():
    """8. Empty successful searches are handled correctly without errors."""
    mock_db = create_mock_session()
    mock_openrouter = create_mock_openrouter()

    mock_result = MagicMock()
    mock_result.mappings.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result

    retriever = HybridRetriever(db=mock_db, openrouter=mock_openrouter)
    asst_id = str(uuid.uuid4())

    # Empty string query with valid IDs returns empty list immediately without hitting DB
    empty_sem = await retriever.search_semantic("  ", asst_id)
    assert empty_sem == []

    empty_lex = await retriever.search_lexical("", asst_id)
    assert empty_lex == []

    # Empty query with INVALID assistant_id must raise ValueError, not return []
    with pytest.raises(ValueError, match="assistant_id is mandatory"):
        await retriever.search_semantic("", "")

    with pytest.raises(ValueError, match="assistant_id is mandatory"):
        await retriever.search_lexical("  ", "")

    # Empty query with INVALID document_ids must raise ValueError, not return []
    with pytest.raises(ValueError, match="empty or null element"):
        await retriever.search_semantic("", asst_id, document_ids=[""])

    with pytest.raises(ValueError, match="empty or null element"):
        await retriever.search_lexical("  ", asst_id, document_ids=[""])

    # Valid query returning zero matches from DB
    res = await retriever.search_hybrid("query sem matches", asst_id)
    assert res.total_results == 0
    assert res.results == []
    assert res.has_retrieved_results is False
    assert res.diagnostics.semantic_failed is False
    assert res.diagnostics.lexical_failed is False


@pytest.mark.anyio
async def test_embedding_failures_distinguishable_from_empty_results():
    """9. Embedding failures are distinguishable from empty results."""
    mock_db = create_mock_session()
    mock_openrouter = create_mock_openrouter()
    mock_openrouter.generate_embeddings.side_effect = OpenRouterNonRecoverableError("HTTP 401 Unauthorized key")

    retriever = HybridRetriever(db=mock_db, openrouter=mock_openrouter)
    asst_id = str(uuid.uuid4())

    # Direct search_semantic propagates the error rather than returning []
    with pytest.raises(OpenRouterNonRecoverableError):
        await retriever.search_semantic("query", asst_id)

    # Embedding with wrong dimension raises RetrievalError
    mock_openrouter.generate_embeddings.side_effect = None
    mock_openrouter.generate_embeddings.return_value = [[0.1] * 512]
    with pytest.raises(RetrievalError, match="dimension mismatch"):
        await retriever.search_semantic("query", asst_id)

    # Embedding with non-numeric value raises RetrievalError
    mock_openrouter.generate_embeddings.return_value = [[0.1] * 1535 + ["not-a-number"]]
    with pytest.raises(RetrievalError, match="not numeric"):
        await retriever.search_semantic("query", asst_id)

    # Embedding with boolean value raises RetrievalError
    mock_openrouter.generate_embeddings.return_value = [[0.1] * 1535 + [True]]
    with pytest.raises(RetrievalError, match="not numeric"):
        await retriever.search_semantic("query", asst_id)

    # Embedding with NaN value raises RetrievalError
    import math
    mock_openrouter.generate_embeddings.return_value = [[0.1] * 1535 + [float("nan")]]
    with pytest.raises(RetrievalError, match="non-finite value"):
        await retriever.search_semantic("query", asst_id)

    # Embedding with infinite value raises RetrievalError
    mock_openrouter.generate_embeddings.return_value = [[0.1] * 1535 + [float("inf")]]
    with pytest.raises(RetrievalError, match="non-finite value"):
        await retriever.search_semantic("query", asst_id)

    # Empty embedding returned from provider raises RetrievalError
    mock_openrouter.generate_embeddings.return_value = []
    with pytest.raises(RetrievalError, match="empty vector"):
        await retriever.search_semantic("query", asst_id)


@pytest.mark.anyio
async def test_lexical_failures_never_return_arbitrary_chunks():
    """10. Lexical failures never return arbitrary chunks."""
    mock_db = create_mock_session()
    mock_db.execute.side_effect = Exception("Database connection timeout")
    mock_openrouter = create_mock_openrouter()

    retriever = HybridRetriever(db=mock_db, openrouter=mock_openrouter)
    asst_id = str(uuid.uuid4())

    # Direct lexical search raises rather than falling back to unconditional LIMIT 6
    with pytest.raises(Exception, match="Database connection timeout"):
        await retriever.search_lexical("query", asst_id)


@pytest.mark.anyio
async def test_hybrid_search_degrades_to_lexical_only_when_semantic_fails():
    """11. Hybrid search degrades to lexical-only when semantic retrieval fails with OpenRouterError or SQLAlchemyError."""
    from sqlalchemy.exc import OperationalError
    from app.services.openrouter import OpenRouterTransientError

    mock_db = create_mock_session()
    mock_openrouter = create_mock_openrouter()

    chunk_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())

    retriever = HybridRetriever(db=mock_db, openrouter=mock_openrouter)
    # Semantic fails with OpenRouterTransientError
    retriever.search_semantic = AsyncMock(side_effect=OpenRouterTransientError("Embedding quota exceeded"))
    # Lexical succeeds
    retriever.search_lexical = AsyncMock(
        return_value=[
            LexicalResult(
                chunk_id=chunk_id,
                document_id=doc_id,
                document_name="policy.pdf",
                content="Conteúdo da política",
                rank_score=0.7,
                rank=1,
            )
        ]
    )

    asst_id = str(uuid.uuid4())
    hybrid_res = await retriever.search_hybrid("termo", asst_id)

    assert hybrid_res.total_results == 1
    assert hybrid_res.results[0].chunk_id == chunk_id
    assert hybrid_res.results[0].retrieval_source == "lexical"
    assert hybrid_res.diagnostics.semantic_failed is True
    assert "OpenRouterTransientError" in hybrid_res.diagnostics.semantic_error
    assert hybrid_res.diagnostics.lexical_failed is False


@pytest.mark.anyio
async def test_hybrid_search_degrades_to_semantic_only_when_lexical_fails():
    """12. Hybrid search degrades to semantic-only when lexical retrieval fails with SQLAlchemyError."""
    from sqlalchemy.exc import ProgrammingError

    mock_db = create_mock_session()
    mock_openrouter = create_mock_openrouter()

    chunk_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())

    retriever = HybridRetriever(db=mock_db, openrouter=mock_openrouter)
    # Semantic succeeds
    retriever.search_semantic = AsyncMock(
        return_value=[
            SemanticResult(
                chunk_id=chunk_id,
                document_id=doc_id,
                document_name="manual.pdf",
                content="Conteúdo semântico",
                similarity_score=0.88,
                rank=1,
            )
        ]
    )
    # Lexical fails with SQLAlchemyError
    retriever.search_lexical = AsyncMock(side_effect=ProgrammingError("statement", {}, Exception("FTS syntax error")))

    asst_id = str(uuid.uuid4())
    hybrid_res = await retriever.search_hybrid("termo", asst_id)

    assert hybrid_res.total_results == 1
    assert hybrid_res.results[0].chunk_id == chunk_id
    assert hybrid_res.results[0].retrieval_source == "semantic"
    assert hybrid_res.diagnostics.semantic_failed is False
    assert hybrid_res.diagnostics.lexical_failed is True
    assert "ProgrammingError" in hybrid_res.diagnostics.lexical_error


@pytest.mark.anyio
async def test_both_retrieval_methods_failing_raises_retrieval_error():
    """13. Both retrieval methods failing with expected errors raises RetrievalError."""
    from sqlalchemy.exc import OperationalError
    from app.services.openrouter import OpenRouterTransientError

    mock_db = create_mock_session()
    mock_openrouter = create_mock_openrouter()

    retriever = HybridRetriever(db=mock_db, openrouter=mock_openrouter)
    retriever.search_semantic = AsyncMock(side_effect=OpenRouterTransientError("OpenRouter timeout"))
    retriever.search_lexical = AsyncMock(side_effect=OperationalError("statement", {}, Exception("DB connection drop")))

    asst_id = str(uuid.uuid4())
    with pytest.raises(RetrievalError, match="Both retrieval methods failed"):
        await retriever.search_hybrid("termo", asst_id)


@pytest.mark.anyio
async def test_unexpected_programming_errors_propagate_without_conversion():
    """14. Unexpected programming bugs (TypeError, AttributeError, NameError) are NOT converted to retrieval failures."""
    mock_db = create_mock_session()
    mock_openrouter = create_mock_openrouter()

    retriever = HybridRetriever(db=mock_db, openrouter=mock_openrouter)
    asst_id = str(uuid.uuid4())

    # Bug in semantic search: TypeError
    retriever.search_semantic = AsyncMock(side_effect=TypeError("Unexpected NoneType in code"))
    retriever.search_lexical = AsyncMock(return_value=[])

    with pytest.raises(TypeError, match="Unexpected NoneType in code"):
        await retriever.search_hybrid("termo", asst_id)

    # Bug in lexical search: AttributeError
    retriever.search_semantic = AsyncMock(return_value=[])
    retriever.search_lexical = AsyncMock(side_effect=AttributeError("'NoneType' object has no attribute 'foo'"))

    with pytest.raises(AttributeError, match="has no attribute 'foo'"):
        await retriever.search_hybrid("termo", asst_id)


def test_retriever_rrf_k_validation():
    """14. rrf_k must be a finite positive number (> 0)."""
    mock_db = create_mock_session()
    mock_openrouter = create_mock_openrouter()

    # Valid values
    r1 = HybridRetriever(db=mock_db, openrouter=mock_openrouter, rrf_k=60.0)
    assert r1.rrf_k == 60.0
    r2 = HybridRetriever(db=mock_db, openrouter=mock_openrouter, rrf_k=1)
    assert r2.rrf_k == 1.0

    # Invalid values
    for invalid_k in [0, -1, -60.0, float("nan"), float("inf"), float("-inf"), "abc", None]:
        with pytest.raises(ValueError):
            HybridRetriever(db=mock_db, openrouter=mock_openrouter, rrf_k=invalid_k)


@pytest.mark.anyio
async def test_search_limits_validation_and_normalization():
    """15. Limits <= 0 or invalid must be normalized to standard defaults."""
    mock_db = create_mock_session()
    mock_openrouter = create_mock_openrouter()

    mock_result = MagicMock()
    mock_result.mappings.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result

    retriever = HybridRetriever(db=mock_db, openrouter=mock_openrouter)
    asst_id = str(uuid.uuid4())

    # search_semantic normalizes limit <= 0 to 10
    await retriever.search_semantic("query", asst_id, limit=-5)
    params_sem = mock_db.execute.call_args[0][1]
    assert params_sem["limit"] == 10

    # search_lexical normalizes limit <= 0 to 10
    await retriever.search_lexical("query", asst_id, limit=0)
    params_lex = mock_db.execute.call_args[0][1]
    assert params_lex["limit"] == 10

    # search_hybrid normalizes limit <= 0 to 6 and sub-limits to 10
    retriever.search_semantic = AsyncMock(return_value=[])
    retriever.search_lexical = AsyncMock(return_value=[])
    res = await retriever.search_hybrid("query", asst_id, limit=-1, semantic_limit=0, lexical_limit=-10)
    assert res.total_results == 0
    # verify that search_semantic was invoked with normalized semantic_limit=10
    retriever.search_semantic.assert_called_once_with(
        query="query", assistant_id=asst_id, limit=10, document_ids=None
    )
    # verify that search_lexical was invoked with normalized lexical_limit=10
    retriever.search_lexical.assert_called_once_with(
        query="query", assistant_id=asst_id, limit=10, document_ids=None
    )


@pytest.mark.anyio
async def test_search_hybrid_propagates_validation_errors():
    """16. search_hybrid must propagate ValueError on invalid assistant_id/document_ids immediately."""
    mock_db = create_mock_session()
    mock_openrouter = create_mock_openrouter()

    retriever = HybridRetriever(db=mock_db, openrouter=mock_openrouter)

    # Invalid assistant_id raises ValueError immediately without calling search methods
    with pytest.raises(ValueError, match="assistant_id is mandatory"):
        await retriever.search_hybrid("query", "")

    with pytest.raises(ValueError, match="Invalid assistant_id UUID format"):
        await retriever.search_hybrid("query", "not-a-uuid")

    # Invalid document_ids raises ValueError immediately
    asst_id = str(uuid.uuid4())
    with pytest.raises(ValueError, match="empty or null element"):
        await retriever.search_hybrid("query", asst_id, document_ids=[""])

    with pytest.raises(ValueError, match="Invalid document_id UUID format"):
        await retriever.search_hybrid("query", asst_id, document_ids=["not-valid-uuid"])


def test_retrieved_chunk_schema_retrieval_source_literal():
    """17. retrieval_source must be strictly 'semantic', 'lexical', or 'hybrid'."""
    from pydantic import ValidationError

    # Valid sources
    for valid_source in ["semantic", "lexical", "hybrid"]:
        chunk = RetrievedChunk(
            chunk_id=str(uuid.uuid4()),
            document_id=str(uuid.uuid4()),
            document_name="doc.pdf",
            content="content",
            rrf_score=0.5,
            retrieval_source=valid_source,  # type: ignore
        )
        assert chunk.retrieval_source == valid_source

    # Invalid sources must be rejected by Pydantic
    for invalid_source in ["other", "keyword", "", "HYBRID"]:
        with pytest.raises(ValidationError):
            RetrievedChunk(
                chunk_id=str(uuid.uuid4()),
                document_id=str(uuid.uuid4()),
                document_name="doc.pdf",
                content="content",
                rrf_score=0.5,
                retrieval_source=invalid_source,  # type: ignore
            )


@pytest.mark.anyio
async def test_real_postgres_lexical_expression_and_uuid_array():
    """18. Integration test with real PostgreSQL if available: tests FTS query CTE and ANY(:doc_ids).
    Only skips if connection to DB fails. Any assertion or query error after connection fails the test.
    """
    from app.database import AsyncSessionLocal
    from sqlalchemy import text

    # Step 1: Attempt connection check exclusively
    from sqlalchemy.exc import OperationalError

    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except (OperationalError, OSError) as conn_err:
        pytest.skip(f"PostgreSQL not accessible in test environment: {conn_err}")

    # Step 2: Connection verified. Execute queries and assert directly (errors will fail the test, never skip)
    async with AsyncSessionLocal() as session:
        # 1. Test FTS CTE with Portuguese dictionary
        sql_fts = text("""
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
            SELECT q.query_ts FROM q;
        """)

        # Multi-term query with accents
        res_multi = await session.execute(sql_fts, {"query": "política de férias e benefícios"})
        val_multi = res_multi.scalar()
        assert val_multi is not None, "to_tsquery for multi-term query returned None unexpectedly"
        val_multi_str = str(val_multi)
        assert "polit" in val_multi_str or "polít" in val_multi_str
        assert "benefic" in val_multi_str or "benefíc" in val_multi_str
        assert "fer" in val_multi_str or "fér" in val_multi_str

        # Query with only stopwords returns None (clean empty tsquery)
        res_stop = await session.execute(sql_fts, {"query": "de a o em e para"})
        val_stop = res_stop.scalar()
        assert val_stop is None, f"Expected None for stopwords query, got: {val_stop!r}"

        # Query with only punctuation returns None
        res_punct = await session.execute(sql_fts, {"query": "??? !!! ... ,,, ;;"})
        val_punct = res_punct.scalar()
        assert val_punct is None, f"Expected None for punctuation query, got: {val_punct!r}"

        # 2. Test ANY(:doc_ids) with list of uuid.UUID objects on documents table
        test_uuid = uuid.uuid4()
        sql_any = text("SELECT id FROM documents WHERE id = ANY(:doc_ids)")
        res_any = await session.execute(sql_any, {"doc_ids": [test_uuid]})
        assert res_any.all() == []



@pytest.mark.anyio
async def test_real_postgres_savepoint_isolation_and_fallback():
    """19. Integration test verifying savepoint isolation with real PostgreSQL:
    - A SQL error inside begin_nested() does NOT invalidate the caller's outer transaction.
    - The AsyncSession remains fully operational after the failure.
    - Fallback between semantic and lexical search works seamlessly even when one query fails with a SQL error.
    """
    from app.database import db_url
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from sqlalchemy.pool import NullPool
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError, ProgrammingError
    from unittest.mock import patch

    # Use dedicated engine with NullPool so connections close cleanly within anyio's event loop
    test_engine = create_async_engine(db_url, poolclass=NullPool)
    session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
    except (OperationalError, OSError) as conn_err:
        await test_engine.dispose()
        pytest.skip(f"PostgreSQL not accessible in test environment: {conn_err}")

    asst_id = str(uuid.uuid4())
    mock_openrouter = create_mock_openrouter()

    try:
        async with session_factory() as session:
            # A. Execute an initial query in outer transaction
            initial_res = await session.execute(text("SELECT 100"))
            assert initial_res.scalar() == 100

            # B. Instantiate real HybridRetriever with this live PostgreSQL session
            retriever = HybridRetriever(db=session, openrouter=mock_openrouter)

            # Force search_semantic's internal SQL execution to raise a real SQL error (ProgrammingError)
            # while search_lexical runs against the real DB and succeeds normally
            original_execute = session.execute

            async def selective_execute(statement, params=None, *args, **kwargs):
                sql_text = str(statement)
                if "vector" in sql_text and "distance" in sql_text:
                    # Trigger a real SQL error in PostgreSQL inside the savepoint
                    return await original_execute(text("SELECT syntax_error FROM nonexistent_relation_12345"))
                return await original_execute(statement, params, *args, **kwargs)

            with patch.object(session, "execute", side_effect=selective_execute):
                hybrid_res = await retriever.search_hybrid(
                    query="manual de instrucoes",
                    assistant_id=asst_id,
                )

            # 1. Semantic search failed gracefully with ProgrammingError
            assert hybrid_res.diagnostics.semantic_failed is True
            assert "ProgrammingError" in hybrid_res.diagnostics.semantic_error

            # 2. Lexical search ran successfully and did not fail because of the semantic error
            assert hybrid_res.diagnostics.lexical_failed is False

            # 3. Outer transaction is STILL ALIVE and can execute further queries and commit
            outer_res = await session.execute(text("SELECT 200"))
            assert outer_res.scalar() == 200
    finally:
        await test_engine.dispose()


@pytest.mark.anyio
async def test_rejection_of_session_with_pending_alterations():
    """20. Rejects retrieval execution if session has unflushed ORM modifications (new, dirty, deleted).
    Protects caller's outer transaction from implicit flushes inside begin_nested()
    without flushing, committing, or rolling back caller's changes.
    """
    mock_db = create_mock_session()
    mock_openrouter = create_mock_openrouter()
    retriever = HybridRetriever(db=mock_db, openrouter=mock_openrouter)
    asst_id = str(uuid.uuid4())

    # Case A: session has pending 'new' object
    mock_db.new = {"dummy_new_entity"}
    with pytest.raises(RetrievalError, match="pending unflushed alterations"):
        await retriever.search_semantic("test", asst_id)

    # begin_nested must NOT have been called
    mock_db.begin_nested.assert_not_called()

    # Case B: session has pending 'dirty' object
    mock_db.new = set()
    mock_db.dirty = {"dummy_dirty_entity"}
    with pytest.raises(RetrievalError, match="pending unflushed alterations"):
        await retriever.search_lexical("test", asst_id)

    mock_db.begin_nested.assert_not_called()

    # Case C: session has pending 'deleted' object
    mock_db.dirty = set()
    mock_db.deleted = {"dummy_deleted_entity"}
    with pytest.raises(RetrievalError, match="pending unflushed alterations"):
        await retriever.search_semantic("test", asst_id)

    mock_db.begin_nested.assert_not_called()
