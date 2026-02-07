import argparse
import gzip
import hashlib
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from typing import List, Tuple

import requests
from tqdm import tqdm

from rag_retrieval.chunker import chunk_text
from rag_retrieval.config import get_settings
from rag_retrieval.db import create_vector_index_sync, get_sync_conn, init_db_sync
from rag_retrieval.embedding import get_embedder

logger = logging.getLogger(__name__)


def upsert_document(conn, source_id: str, title: str, url: str, checksum: str) -> int:
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


def insert_chunks(conn, document_id: int, chunks: List[Tuple[str, int]], embeddings) -> None:
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


def parse_review_record(record: dict) -> str:
    parts = []
    summary = record.get("summary") or ""
    text = record.get("reviewText") or ""
    if summary:
        parts.append(summary.strip())
    if text:
        parts.append(text.strip())
    combined = ". ".join(parts).strip()
    return combined


def download_and_parse_dataset(name: str, url: str, max_reviews: int | None, timeout: int):
    logger.info("Downloading dataset %s from %s", name, url)
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    reviews = []
    with gzip.open(BytesIO(resp.content), "rt", encoding="utf-8", errors="ignore") as f:
        for line in f:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            content = parse_review_record(record)
            if not content:
                continue
            asin = record.get("asin", "unknown")
            reviewer = record.get("reviewerID", "unknown")
            source_id = f"{name}:{asin}:{reviewer}:{len(reviews)}"
            title = f"{name} review {asin}"
            reviews.append({"source_id": source_id, "url": url, "text": content, "title": title})
            if max_reviews and len(reviews) >= max_reviews:
                break
    logger.info("Parsed %d reviews from %s", len(reviews), name)
    return reviews


def load_local_dataset(path: str, name: str, max_reviews: int | None):
    reviews = []
    logger.info("Loading local dataset %s from %s", name, path)
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="ignore") as f:
        for line in f:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            content = parse_review_record(record)
            if not content:
                continue
            asin = record.get("asin", "unknown")
            reviewer = record.get("reviewerID", "unknown")
            source_id = f"{name}:{asin}:{reviewer}:{len(reviews)}"
            title = f"{name} review {asin}"
            reviews.append({"source_id": source_id, "url": f"file://{os.path.abspath(path)}", "text": content, "title": title})
            if max_reviews and len(reviews) >= max_reviews:
                break
    logger.info("Parsed %d reviews from local %s", len(reviews), name)
    return reviews


def ingest_from_remote(settings):
    jobs = []
    with ThreadPoolExecutor(max_workers=settings.max_workers) as executor:
        for name, url in zip(settings.dataset_names, settings.dataset_urls):
            jobs.append(
                executor.submit(
                    download_and_parse_dataset,
                    name,
                    url,
                    settings.max_reviews_per_dataset,
                    settings.request_timeout,
                )
            )
        results = []
        for future in tqdm(as_completed(jobs), total=len(jobs), desc="Downloading"):
            results.extend(future.result())
    return results


def ingest_from_local(settings):
    if not settings.local_data_path or not os.path.isdir(settings.local_data_path):
        raise RuntimeError("LOCAL_DATA_PATH is not set or not a directory for local ingest")
    files = []
    for root, _, fnames in os.walk(settings.local_data_path):
        for fname in fnames:
            if fname.endswith(".json") or fname.endswith(".jsonl") or fname.endswith(".json.gz"):
                files.append(os.path.join(root, fname))
    if not files:
        raise RuntimeError("No .json/.jsonl/.json.gz files found under LOCAL_DATA_PATH")
    reviews = []
    with ThreadPoolExecutor(max_workers=settings.max_workers) as executor:
        futures = []
        for path in files:
            name = os.path.splitext(os.path.basename(path))[0]
            futures.append(executor.submit(load_local_dataset, path, name, settings.max_reviews_per_dataset))
        for future in tqdm(as_completed(futures), total=len(futures), desc="Loading local"):
            reviews.extend(future.result())
    return reviews


def ingest():
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")
    mode = "local" if settings.local_data_path else "remote"
    logger.info("Starting ingestion (mode=%s)", mode)

    init_db_sync()
    embedder = get_embedder()
    if embedder.dim != settings.embedding_dim:
        logger.warning("Embedding dim mismatch: config=%s, model=%s", settings.embedding_dim, embedder.dim)

    if settings.local_data_path:
        results = ingest_from_local(settings)
    else:
        results = ingest_from_remote(settings)

    with get_sync_conn() as conn:
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

    create_vector_index_sync()
    logger.info("Ingestion complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest Amazon reviews documents into Postgres with pgvector.")
    args = parser.parse_args()
    ingest()
