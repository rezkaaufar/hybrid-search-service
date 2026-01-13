import contextlib
from typing import Iterator, Optional

import psycopg
from psycopg_pool import ConnectionPool

from rag_retrieval.config import get_settings

settings = get_settings()

pool: Optional[ConnectionPool] = None


def init_pool():
    global pool
    if pool is None:
        pool = ConnectionPool(conninfo=settings.database_url, min_size=1, max_size=10, timeout=30)
    return pool


@contextlib.contextmanager
def get_conn() -> Iterator[psycopg.Connection]:
    if pool is None:
        init_pool()
    assert pool is not None
    with pool.connection() as conn:
        yield conn


def init_db():
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

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
            cur.execute(indexes)
            conn.commit()


def create_vector_index():
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
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(stmt)
            conn.commit()
