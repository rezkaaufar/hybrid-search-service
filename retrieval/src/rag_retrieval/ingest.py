import argparse
import hashlib
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple

import psycopg
import requests
from tqdm import tqdm

from rag_retrieval.chunker import chunk_text
from rag_retrieval.config import get_settings
from rag_retrieval.db import create_vector_index, get_conn, init_db
from rag_retrieval.embedding import get_embedder

logger = logging.getLogger(__name__)


def download_text(gutenberg_id: int, urls: List[str], timeout: int = 30) -> Tuple[int, str, str]:
    for url in urls:
        try:
            resp = requests.get(url, timeout=timeout)
            if resp.status_code == 200 and len(resp.text.strip()) > 500:
                checksum = hashlib.sha256(resp.text.encode("utf-8")).hexdigest()
                return gutenberg_id, url, resp.text
        except requests.RequestException:
            continue
    raise RuntimeError(f"Failed to fetch Gutenberg id {gutenberg_id}")


def upsert_document(conn: psycopg.Connection, source_id: str, title: str, url: str, checksum: str) -> int:
    stmt = """
    INSERT INTO documents (source_id, title, url, checksum)
    VALUES (%s, %s, %s, %s)
    ON CONFLICT (source_id) DO UPDATE SET checksum = EXCLUDED.checksum
    RETURNING id;
    """
    with conn.cursor() as cur:
        cur.execute(stmt, (source_id, title, url, checksum))
        doc_id = cur.fetchone()[0]
        conn.commit()
        return doc_id


def insert_chunks(conn: psycopg.Connection, document_id: int, chunks: List[Tuple[str, int]], embeddings) -> None:
    records = []
    for idx, ((content, token_count), emb) in enumerate(zip(chunks, embeddings)):
        records.append(
            (
                document_id,
                idx,
                content,
                token_count,
                emb.tolist(),
                content,
            )
        )

    stmt = """
    INSERT INTO chunks (document_id, chunk_index, content, token_count, embedding, tsv_content)
    VALUES (%s, %s, %s, %s, %s, to_tsvector('english', %s))
    ON CONFLICT DO NOTHING;
    """
    with conn.cursor() as cur:
        cur.executemany(stmt, records)
    conn.commit()


def ingest_from_ids(settings):
    logger.info("Downloading Gutenberg IDs: %s", settings.dataset_ids)
    jobs = []
    with ThreadPoolExecutor(max_workers=settings.max_workers) as executor:
        for gid in settings.dataset_ids:
            urls = [u for u in settings.dataset_urls if f"/{gid}/" in u or f"pg{gid}.txt" in u]
            jobs.append(executor.submit(download_text, gid, urls, settings.request_timeout))

        results = []
        for future in tqdm(as_completed(jobs), total=len(jobs), desc="Downloading"):
            results.append(future.result())

    docs = []
    for gid, url, text in results:
        docs.append({"source_id": gid, "url": url, "text": text, "title": f"Gutenberg #{gid}"})
    return docs


def iter_mirror_files(base_path: str, limit: int | None = None):
    count = 0
    for root, _, files in os.walk(base_path):
        for fname in files:
            if not fname.lower().endswith(".txt"):
                continue
            yield os.path.join(root, fname)
            count += 1
            if limit and count >= limit:
                return


def load_local_file(path: str):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read()
    basename = os.path.basename(path)
    match = re.search(r"(\d+)", basename)
    source_id = match.group(1) if match else basename
    title = f"Gutenberg local #{source_id}"
    url = f"file://{os.path.abspath(path)}"
    return {"source_id": source_id, "url": url, "text": text, "title": title}


def ingest_from_mirror(settings):
    if not settings.mirror_path or not os.path.isdir(settings.mirror_path):
        raise RuntimeError("Mirror mode enabled but GUTENBERG_MIRROR_PATH is not set or not a directory")

    files = list(iter_mirror_files(settings.mirror_path, settings.ingest_limit))
    logger.info("Found %d local text files for ingestion", len(files))

    docs = []
    with ThreadPoolExecutor(max_workers=settings.max_workers) as executor:
        futures = [executor.submit(load_local_file, path) for path in files]
        for future in tqdm(as_completed(futures), total=len(futures), desc="Loading"):
            docs.append(future.result())
    return docs


def ingest():
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")
    mode = "mirror" if settings.is_mirror_mode else "ids"
    logger.info("Starting ingestion (mode=%s)", mode)

    init_db()
    embedder = get_embedder()
    if embedder.dim != settings.embedding_dim:
        logger.warning("Embedding dim mismatch: config=%s, model=%s", settings.embedding_dim, embedder.dim)

    if settings.is_mirror_mode:
        results = ingest_from_mirror(settings)
    else:
        results = ingest_from_ids(settings)

    with get_conn() as conn:
        for item in tqdm(results, desc="Processing"):
            source_id = item["source_id"]
            url = item["url"]
            text = item["text"]
            title = item["title"]

            checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
            doc_id = upsert_document(conn, str(source_id), title, url, checksum)
            chunks = chunk_text(text, chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap)
            contents = [c[0] for c in chunks]
            embeddings = embedder.embed(contents, batch_size=32)
            insert_chunks(conn, doc_id, chunks, embeddings)

    create_vector_index()
    logger.info("Ingestion complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest Gutenberg documents into Postgres with pgvector.")
    args = parser.parse_args()
    ingest()
