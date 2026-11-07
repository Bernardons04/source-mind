from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class SemanticResult(BaseModel):
    chunk_id: str
    document_id: str
    document_name: str
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    similarity_score: float  # 1.0 - cosine_distance (higher is more similar)
    rank: int  # 1-indexed


class LexicalResult(BaseModel):
    chunk_id: str
    document_id: str
    document_name: str
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    rank_score: float  # ts_rank_cd score
    rank: int  # 1-indexed


class RetrievedChunk(BaseModel):
    chunk_id: str
    document_id: str
    document_name: str
    content: str
    page: Optional[int] = None
    page_end: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    semantic_rank: Optional[int] = None
    semantic_score: Optional[float] = None
    lexical_rank: Optional[int] = None
    lexical_score: Optional[float] = None
    rrf_score: float
    retrieval_source: Literal["semantic", "lexical", "hybrid"]


class RetrievalDiagnostics(BaseModel):
    semantic_count: int = 0
    lexical_count: int = 0
    fusion_count: int = 0
    semantic_failed: bool = False
    lexical_failed: bool = False
    semantic_error: Optional[str] = None
    lexical_error: Optional[str] = None


class HybridSearchResult(BaseModel):
    query: str
    total_results: int
    results: List[RetrievedChunk] = Field(default_factory=list)
    has_retrieved_results: bool = False
    diagnostics: RetrievalDiagnostics = Field(default_factory=RetrievalDiagnostics)
