from typing import List, Literal, Optional

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from rag_retrieval.config import get_settings
from rag_retrieval.db import get_conn, init_db, init_pool
from rag_retrieval.embedding import get_embedder

settings = get_settings()
app = FastAPI(title="RAG Retrieval Service", version="0.1.0")


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
def startup_event():
    init_pool()
    init_db()
    get_embedder()


@app.get("/health")
def health():
    return {"status": "ok"}


def run_lexical(query: str, k: int) -> List[ChunkResult]:
    stmt = """
    SELECT c.id, c.document_id, c.content, d.url, d.title,
    ts_rank_cd(c.tsv_content, plainto_tsquery('english', %s)) AS score
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    WHERE c.tsv_content @@ plainto_tsquery('english', %s)
    ORDER BY score DESC
    LIMIT %s;
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(stmt, (query, query, k))
            rows = cur.fetchall()
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


def run_semantic(query: str, k: int) -> List[ChunkResult]:
    embedder = get_embedder()
    vector = embedder.embed([query])[0].tolist()
    stmt = """
    SELECT c.id, c.document_id, c.content, d.url, d.title, (c.embedding <-> %s::vector) AS distance
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    ORDER BY c.embedding <-> %s::vector
    LIMIT %s;
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(stmt, (vector, vector, k))
            rows = cur.fetchall()
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


def fuse_scores(lexical: List[ChunkResult], semantic: List[ChunkResult], k: int) -> List[ChunkResult]:
    fused: dict[int, dict] = {}
    if lexical:
        max_lex = max(r.score for r in lexical) or 1.0
        for r in lexical:
            fused.setdefault(r.chunk_id, {"item": r, "score": 0.0})
            fused[r.chunk_id]["score"] += 0.5 * (r.score / max_lex)
    if semantic:
        distances = np.array([r.score for r in semantic])
        if len(distances) == 0:
            distances = np.array([1.0])
        max_dist = distances.max() or 1.0
        for r in semantic:
            sim = 1 - (r.score / max_dist)
            fused.setdefault(r.chunk_id, {"item": r, "score": 0.0})
            fused[r.chunk_id]["score"] += 0.5 * sim
    sorted_items = sorted(fused.values(), key=lambda x: x["score"], reverse=True)
    top = []
    for entry in sorted_items[:k]:
        item = entry["item"]
        item.score = entry["score"]
        top.append(item)
    return top


@app.post("/query", response_model=QueryResponse)
def query(payload: QueryRequest):
    if not payload.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    mode = payload.mode
    if mode == "lexical":
        results = run_lexical(payload.query, payload.k)
    elif mode == "semantic":
        results = run_semantic(payload.query, payload.k)
    else:
        lexical = run_lexical(payload.query, payload.k)
        semantic = run_semantic(payload.query, payload.k)
        results = fuse_scores(lexical, semantic, payload.k)

    return QueryResponse(mode=mode, results=results)
