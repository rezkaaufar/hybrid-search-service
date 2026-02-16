from typing import List, Literal, Optional

import anyio
import asyncio
import logging
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from rag_retrieval.config import get_settings
from rag_retrieval.db import get_conn, init_db, init_pool
from rag_retrieval.embedding import get_embedder

settings = get_settings()
app = FastAPI(title="RAG Retrieval Service", version="0.1.0")
embed_semaphore = anyio.Semaphore(settings.embed_concurrency)
logger = logging.getLogger(__name__)


class QueryRequest(BaseModel):
    query: str
    k: int = 5
    mode: Literal["lexical", "semantic", "hybrid"] = "hybrid"


class ChunkResult(BaseModel):
    chunk_id: int
    document_id: int
    content: str
    score: float
    source_url: Optional[str] = None
    source_title: Optional[str] = None


class QueryResponse(BaseModel):
    mode: str
    results: List[ChunkResult]


@app.on_event("startup")
async def startup_event():
    # Initialize pool; if DB is unavailable at startup, log and continue so the container becomes healthy.
    try:
        await init_pool()
    except Exception as exc:  # pragma: no cover
        logger.warning("DB pool init failed at startup: %s", exc)
    try:
        await init_db()
    except Exception as exc:  # pragma: no cover
        logger.warning("DB init failed at startup (will retry on demand): %s", exc)
    # Warm embedder (may download model on first run).
    try:
        get_embedder()
    except Exception as exc:  # pragma: no cover
        logger.warning("Embedder warmup failed at startup: %s", exc)


@app.get("/health")
def health():
    return {"status": "ok"}


async def run_lexical(query: str, k: int) -> List[ChunkResult]:
    stmt = """
    SELECT c.id, c.document_id, c.content, d.url, d.title,
    ts_rank_cd(c.tsv_content, plainto_tsquery('english', %s)) AS score
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    WHERE c.tsv_content @@ plainto_tsquery('english', %s)
    ORDER BY score DESC
    LIMIT %s;
    """
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(stmt, (query, query, k))
            rows = await cur.fetchall()
    return [
        ChunkResult(
            chunk_id=row[0],
            document_id=row[1],
            content=row[2],
            source_url=row[3],
            source_title=row[4],
            score=float(row[5]),
        )
        for row in rows
    ]


async def run_semantic(query: str, k: int) -> List[ChunkResult]:
    vector = _embed_one(query)
    return await run_semantic_with_vector(vector, k)


async def run_semantic_with_vector(vector: List[float], k: int) -> List[ChunkResult]:
    stmt = """
    SELECT c.id, c.document_id, c.content, d.url, d.title, (c.embedding <-> %s::vector) AS distance
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    ORDER BY c.embedding <-> %s::vector
    LIMIT %s;
    """
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(stmt, (vector, vector, k))
            rows = await cur.fetchall()
    return [
        ChunkResult(
            chunk_id=row[0],
            document_id=row[1],
            content=row[2],
            source_url=row[3],
            source_title=row[4],
            score=float(row[5]),
        )
        for row in rows
    ]


def _embed_one(query: str) -> List[float]:
    embedder = get_embedder()
    return embedder.embed([query])[0].tolist()


async def embed_async_one(query: str) -> List[float]:
    # Limit concurrent embeddings to avoid CPU saturation and potential model thread-safety issues
    async with embed_semaphore:
        return await anyio.to_thread.run_sync(_embed_one, query)


def reciprocal_rank_fusion(
    lexical: List[ChunkResult],
    semantic: List[ChunkResult],
    k: int,
    rrf_k: int = 60
) -> List[ChunkResult]:
    """
    Reciprocal Rank Fusion (RRF) for combining lexical and semantic search results.

    RRF score for a document = sum over all ranklists of: 1 / (rrf_k + rank)

    This approach is more robust than weighted score fusion because:
    - No score normalization needed (rank-based, not score-based)
    - Handles different score scales naturally
    - Used by Elasticsearch and other production systems
    - Simple and efficient for high-throughput scenarios

    Args:
        lexical: Ranked list of lexical search results (already sorted by score desc)
        semantic: Ranked list of semantic search results (already sorted by distance asc)
        k: Number of final results to return
        rrf_k: RRF constant (default 60, standard value from literature)
               Lower values give more weight to top ranks, higher values flatten the curve

    Returns:
        Fused and re-ranked list of top-k results with RRF scores

    Time Complexity: O(n + m + (n+m)log(n+m)) where n=len(lexical), m=len(semantic)
    Space Complexity: O(n + m) for the fused dictionary
    """
    # Early exit for edge cases
    if not lexical and not semantic:
        return []
    if not lexical:
        return semantic[:k]
    if not semantic:
        return lexical[:k]

    # Pre-allocate dict with estimated size to reduce rehashing
    # Use chunk_id as key for O(1) lookups during merging
    fused: dict[int, dict] = {}

    # Process lexical results: enumerate gives 0-based index, we want 1-based ranks
    for rank, result in enumerate(lexical, start=1):
        rrf_score = 1.0 / (rrf_k + rank)
        chunk_id = result.chunk_id

        if chunk_id not in fused:
            # First occurrence: store the result object and initialize score
            fused[chunk_id] = {"item": result, "score": rrf_score}
        else:
            # Document appears in both lists: accumulate RRF score
            fused[chunk_id]["score"] += rrf_score

    # Process semantic results
    for rank, result in enumerate(semantic, start=1):
        rrf_score = 1.0 / (rrf_k + rank)
        chunk_id = result.chunk_id

        if chunk_id not in fused:
            fused[chunk_id] = {"item": result, "score": rrf_score}
        else:
            fused[chunk_id]["score"] += rrf_score

    # Sort by RRF score (descending) and return top-k
    # Using itemgetter or lambda x: x["score"] - lambda is more readable here
    # Avoid creating intermediate list - use heapq.nlargest for very large result sets
    if len(fused) > k * 10:  # Heuristic: use heap for large result sets
        import heapq
        top_entries = heapq.nlargest(k, fused.values(), key=lambda x: x["score"])
    else:
        # For smaller result sets, full sort is faster due to lower overhead
        sorted_entries = sorted(fused.values(), key=lambda x: x["score"], reverse=True)
        top_entries = sorted_entries[:k]

    # Update the score field in ChunkResult objects with the fused RRF score
    results = []
    for entry in top_entries:
        item = entry["item"]
        item.score = entry["score"]
        results.append(item)

    return results


@app.post("/query", response_model=QueryResponse)
async def query(payload: QueryRequest):
    if not payload.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    mode = payload.mode
    if mode == "lexical":
        results = await run_lexical(payload.query, payload.k)
    elif mode == "semantic":
        vector = await embed_async_one(payload.query)
        results = await run_semantic_with_vector(vector, payload.k)
    else:
        lexical_task = run_lexical(payload.query, payload.k)
        vector_task = embed_async_one(payload.query)
        lexical, vector = await asyncio.gather(lexical_task, vector_task)
        semantic = await run_semantic_with_vector(vector, payload.k)
        results = reciprocal_rank_fusion(lexical, semantic, payload.k, rrf_k=settings.rrf_k)

    return QueryResponse(mode=mode, results=results)
