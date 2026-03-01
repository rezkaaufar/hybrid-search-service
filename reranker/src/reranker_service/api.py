from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import List, Optional

import anyio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from reranker_service.config import get_settings
from reranker_service.model import get_reranker

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Semaphore – limits concurrent CPU-bound reranking calls
# ---------------------------------------------------------------------------

_rerank_sem: asyncio.Semaphore  # initialised in lifespan


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class Document(BaseModel):
    """A single document from the retrieval service."""

    chunk_id: Optional[int] = None
    document_id: Optional[int] = None
    content: str
    score: Optional[float] = None
    source_url: Optional[str] = None
    source_title: Optional[str] = None


class RerankRequest(BaseModel):
    """Payload sent by the caller (typically the retrieval service output)."""

    query: str = Field(..., min_length=1, description="User query string")
    documents: List[Document] = Field(
        ...,
        min_length=1,
        description="Ordered list of documents to rerank",
    )
    top_k: Optional[int] = Field(
        default=None,
        ge=1,
        description="Return only the top-k reranked documents (default: all)",
    )


class RankedDocument(BaseModel):
    """A document enriched with its reranker score and new rank."""

    rank: int
    chunk_id: Optional[int] = None
    document_id: Optional[int] = None
    content: str
    reranker_score: float
    original_score: Optional[float] = None
    source_url: Optional[str] = None
    source_title: Optional[str] = None


class RerankResponse(BaseModel):
    query: str
    results: List[RankedDocument]
    reranked_count: int
    returned_count: int
    latency_ms: float


# ---------------------------------------------------------------------------
# Lifespan: warm up the model once at startup
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _rerank_sem

    _rerank_sem = asyncio.Semaphore(settings.rerank_concurrency)
    logger.info(
        "Rerank concurrency semaphore set to %d", settings.rerank_concurrency
    )

    try:
        logger.info("Warming up reranker model …")
        reranker = get_reranker()
        # Tiny warm-up inference so the first real request isn't slow.
        reranker.rerank("warmup", ["warmup document"])
        logger.info("Reranker model ready.")
    except Exception:
        logger.exception("Failed to load reranker model – service may be degraded")

    yield  # application runs here


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Reranker Service",
    description="Cross-encoder reranking for retrieval results",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _rerank_async(query: str, texts: list[str]) -> list[float]:
    """Run the CPU-bound reranker in a thread pool, gated by semaphore."""
    async with _rerank_sem:
        reranker = get_reranker()
        scores = await anyio.to_thread.run_sync(
            lambda: reranker.rerank(query, texts),
            cancellable=True,
        )
    return scores.tolist()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.post("/rerank", response_model=RerankResponse)
async def rerank(request: RerankRequest) -> RerankResponse:
    """Rerank a list of documents for a given query.

    Accepts the output of the retrieval service directly (query + documents).
    Returns the same documents sorted by cross-encoder relevance score,
    optionally truncated to `top_k`.
    """
    max_docs = settings.max_docs_per_request
    if len(request.documents) > max_docs:
        raise HTTPException(
            status_code=422,
            detail=f"Too many documents: got {len(request.documents)}, max {max_docs}",
        )

    t0 = time.perf_counter()

    texts = [doc.content for doc in request.documents]

    try:
        scores = await _rerank_async(request.query, texts)
    except Exception as exc:
        logger.exception("Reranking failed")
        raise HTTPException(status_code=500, detail="Reranking error") from exc

    # Sort by score descending
    indexed = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)

    top_k = request.top_k if request.top_k is not None else len(request.documents)
    top_k = min(top_k, len(request.documents))

    results: List[RankedDocument] = []
    for rank, (orig_idx, score) in enumerate(indexed[:top_k], start=1):
        doc = request.documents[orig_idx]
        results.append(
            RankedDocument(
                rank=rank,
                chunk_id=doc.chunk_id,
                document_id=doc.document_id,
                content=doc.content,
                reranker_score=score,
                original_score=doc.score,
                source_url=doc.source_url,
                source_title=doc.source_title,
            )
        )

    latency_ms = (time.perf_counter() - t0) * 1000
    logger.info(
        "Reranked %d docs → top %d | query=%r | %.1f ms",
        len(request.documents),
        top_k,
        request.query,
        latency_ms,
    )

    return RerankResponse(
        query=request.query,
        results=results,
        reranked_count=len(request.documents),
        returned_count=len(results),
        latency_ms=round(latency_ms, 2),
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
