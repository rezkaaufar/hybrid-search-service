import argparse
import gzip
import hashlib
import json
import logging
import os
from io import TextIOWrapper
from typing import Generator, List, Tuple

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
    ON CONFLICT (source_id) DO UPDATE
      SET title = EXCLUDED.title,
          url = EXCLUDED.url,
          checksum = EXCLUDED.checksum
    RETURNING id;
    """
    with conn.cursor() as cur:
        cur.execute(stmt, (source_id, title, url, checksum))
        doc_id = cur.fetchone()[0]
        conn.commit()
        return doc_id


def replace_chunks(conn, document_id: int) -> None:
    stmt = "DELETE FROM chunks WHERE document_id = %s;"
    with conn.cursor() as cur:
        cur.execute(stmt, (document_id,))
    conn.commit()


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


def stream_remote_dataset(
    name: str, url: str, max_reviews: int | None, timeout: int
) -> Generator[dict, None, None]:
    logger.info("Downloading dataset %s from %s", name, url)
    count = 0
    with requests.get(url, stream=True, timeout=timeout) as resp:
        resp.raise_for_status()
        resp.raw.decode_content = True
        with gzip.GzipFile(fileobj=resp.raw) as gz:
            with TextIOWrapper(gz, encoding="utf-8", errors="ignore") as f:
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
                    source_id = f"{name}:{asin}:{reviewer}:{count}"
                    title = f"{name} review {asin}"
                    yield {"source_id": source_id, "url": url, "text": content, "title": title}
                    count += 1
                    if max_reviews and count >= max_reviews:
                        break
    logger.info("Parsed %d reviews from %s", count, name)


def stream_local_dataset(path: str, name: str, max_reviews: int | None) -> Generator[dict, None, None]:
    logger.info("Loading local dataset %s from %s", name, path)
    count = 0
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
            source_id = f"{name}:{asin}:{reviewer}:{count}"
            title = f"{name} review {asin}"
            yield {"source_id": source_id, "url": f"file://{os.path.abspath(path)}", "text": content, "title": title}
            count += 1
            if max_reviews and count >= max_reviews:
                break
    logger.info("Parsed %d reviews from local %s", count, name)


def ingest_from_remote(settings) -> Generator[dict, None, None]:
    for name, url in zip(settings.dataset_names, settings.dataset_urls):
        try:
            yield from stream_remote_dataset(name, url, settings.max_reviews_per_dataset, settings.request_timeout)
        except requests.RequestException as e:
            logger.warning("Skipping dataset %s (%s): %s", name, url, e)


def ingest_from_local(settings) -> Generator[dict, None, None]:
    if not settings.local_data_path or not os.path.isdir(settings.local_data_path):
        raise RuntimeError("LOCAL_DATA_PATH is not set or not a directory for local ingest")
    files = []
    for root, _, fnames in os.walk(settings.local_data_path):
        for fname in fnames:
            if fname.endswith(".json") or fname.endswith(".jsonl") or fname.endswith(".json.gz"):
                files.append(os.path.join(root, fname))
    if not files:
        raise RuntimeError("No .json/.jsonl/.json.gz files found under LOCAL_DATA_PATH")
    for path in sorted(files):
        name = os.path.splitext(os.path.basename(path))[0]
        yield from stream_local_dataset(path, name, settings.max_reviews_per_dataset)


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
        results_iter = ingest_from_local(settings)
    else:
        results_iter = ingest_from_remote(settings)

    with get_sync_conn() as conn:
        for item in tqdm(results_iter, desc="Processing", unit="review"):
            source_id = item["source_id"]
            url = item["url"]
            text = item["text"]
            title = item["title"]

            checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
            doc_id = upsert_document(conn, str(source_id), title, url, checksum)
            replace_chunks(conn, doc_id)
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
