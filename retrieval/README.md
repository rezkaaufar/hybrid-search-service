# Retrieval Component

CPU-first document retrieval service for Amazon product reviews with lexical (Postgres full-text) and semantic (pgvector) search. Packaged with Docker for local and cloud use.

## Prerequisites
- Python 3.11+ (for local runs) or Docker + docker-compose.
- Postgres 16 with `vector` extension (compose uses `pgvector/pgvector:pg16` image).
- CPU is sufficient; embeddings use `sentence-transformers/all-MiniLM-L6-v2` by default.

## Configuration
Copy `.env.example` to `.env` and adjust as needed:
- `DATABASE_URL` e.g. `postgresql://rag:ragpass@localhost:5432/ragdb`
- `EMBEDDING_MODEL`, `EMBEDDING_DIM` (default 384), `CHUNK_SIZE`, `CHUNK_OVERLAP`
- `DATASET_NAMES` comma-separated Amazon review categories (defaults: Baby,Pet_Supplies,Video_Games)
- `DATASET_BASE_URL` base URL for Amazon review files (default SNAP URL)
- `LOCAL_DATA_PATH` optional path to local review files (`.json/.jsonl/.json.gz`) to ingest instead of downloading
- `MAX_REVIEWS_PER_DATASET` optional cap per dataset (default 5000)
- `MAX_WORKERS`, `LOG_LEVEL`, `REQUEST_TIMEOUT`

## Run with Docker (recommended)
```bash
cd retrieval
cp .env.example .env  # adjust if needed
docker compose up --build
```
- API available at `http://localhost:8000`
- Postgres exposed on `localhost:${POSTGRES_PORT:-5432}`

Ingest Amazon reviews (runs inside the app container):
```bash
docker compose exec app python -m rag_retrieval.ingest
```

By default ingestion downloads selected Amazon review datasets from SNAP. To ingest your own local files, set `LOCAL_DATA_PATH` to a directory containing `.json/.jsonl/.json.gz` review files and rerun the ingest command.

Restart stack and reset volumes (recreates DB):
```bash
docker compose down -v
docker compose up --build
docker compose exec app python -m rag_retrieval.ingest
```

## Run locally (no Docker)
```bash
cd retrieval
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # point DATABASE_URL to your Postgres with pgvector installed
python -m rag_retrieval.ingest
uvicorn rag_retrieval.api:app --reload --port 8000
```

## Query the service
```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"query": "battery life for this camera", "mode": "hybrid", "k": 5}'
```
- `mode` options: `lexical`, `semantic`, `hybrid` (default). `k` limits results.

## Data model & indexes
- `documents`: source_id, title, url, checksum
- `chunks`: content, token_count, `tsvector` for full-text, `vector` for embeddings
- Indexes: GIN on `tsv_content`, IVFFLAT on `embedding` (created after ingest)

## Notes
- Embeddings and inference run on CPU by default; no CUDA required.
- Initial model download happens on first run; cache persists inside container or local env.
- Extend by adding more Gutenberg IDs to `DATASET_IDS` or overriding the embedding model.
