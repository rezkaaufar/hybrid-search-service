import contextlib
from typing import AsyncIterator, Optional

import psycopg
from psycopg_pool import AsyncConnectionPool

from rag_retrieval.config import get_settings

settings = get_settings()

pool: Optional[AsyncConnectionPool] = None


async def init_pool() -> AsyncConnectionPool:
    global pool
    if pool is None:
        pool = AsyncConnectionPool(conninfo=settings.database_url, min_size=1, max_size=10, timeout=30)
    return pool


@contextlib.asynccontextmanager
async def get_conn() -> AsyncIterator[psycopg.AsyncConnection]:
    if pool is None:
        await init_pool()
    assert pool is not None
    async with pool.connection() as conn:
        yield conn


@contextlib.contextmanager
def get_sync_conn() -> AsyncIterator[psycopg.Connection]:
    # For offline scripts (ingest) that prefer synchronous access.
    with psycopg.connect(settings.database_url) as conn:
        yield conn


async def init_db():
    ddl = f"""
    CREATE EXTENSION IF NOT EXISTS vector;

    CREATE TABLE IF NOT EXISTS documents (
        id SERIAL PRIMARY KEY,
        source_id TEXT UNIQUE,
        title TEXT,
        url TEXT,
        checksum TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS chunks (
        id BIGSERIAL PRIMARY KEY,
        document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
        chunk_index INTEGER,
        content TEXT NOT NULL,
        token_count INTEGER,
        embedding vector({settings.embedding_dim}),
        tsv_content tsvector,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );
    """

    indexes = """
    CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id);
    CREATE INDEX IF NOT EXISTS idx_chunks_tsv ON chunks USING GIN (tsv_content);
    """

    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(ddl)
            await cur.execute(indexes)
        await conn.commit()


async def create_vector_index():
    # ivfflat needs rows present; safe to run after inserts
    stmt = """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = 'idx_chunks_embedding'
        ) THEN
            CREATE INDEX idx_chunks_embedding ON chunks USING ivfflat (embedding vector_l2_ops) WITH (lists = 100);
        END IF;
    END $$;
    """
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(stmt)
        await conn.commit()


def init_db_sync():
    import asyncio

    asyncio.run(init_db())


def create_vector_index_sync():
    import asyncio

    asyncio.run(create_vector_index())
