# Retrieval Component

CPU-first document retrieval service for Project Gutenberg texts with lexical (Postgres full-text) and semantic (pgvector) search. Packaged with Docker for local and cloud use.

## Prerequisites
- Python 3.11+ (for local runs) or Docker + docker-compose.
- Postgres 16 with `vector` extension (compose uses `pgvector/pgvector:pg16` image).
- CPU is sufficient; embeddings use `sentence-transformers/all-MiniLM-L6-v2` by default.

## Configuration
Copy `.env.example` to `.env` and adjust as needed:
- `DATABASE_URL` e.g. `postgresql://rag:ragpass@localhost:5432/ragdb`
- `EMBEDDING_MODEL`, `EMBEDDING_DIM` (default 384), `CHUNK_SIZE`, `CHUNK_OVERLAP`
- `DATASET_IDS` comma-separated Gutenberg IDs (defaults: 1342,1661,98)
- `INGEST_MODE` `ids` (default) or `mirror`
- `GUTENBERG_MIRROR_PATH` path to a local Gutenberg mirror (used when `INGEST_MODE=mirror`)
- `INGEST_LIMIT` optional cap on files processed when in mirror mode
- `HOST_GUTENBERG_MIRROR_PATH` host path to mount into the container at `GUTENBERG_MIRROR_PATH`
- `MAX_WORKERS`, `LOG_LEVEL`, `REQUEST_TIMEOUT`

## Run with Docker (recommended)
```bash
cd retrieval
cp .env.example .env  # adjust if needed
docker compose up --build
```
- API available at `http://localhost:8000`
- Postgres exposed on `localhost:${POSTGRES_PORT:-5432}`

Ingest Gutenberg texts (runs inside the app container):
```bash
docker compose exec app python -m rag_retrieval.ingest
```

Download the full Gutenberg mirror (run outside the container; large: tens of GB). Use an active Gutenberg rsync mirror (ibiblio’s `pub` module is deprecated). Example:
```bash
rsync -avz --delete --progress \
  rsync://aleph.gutenberg.org/gutenberg/ \
  /tmp/gutenberg_mirror
```
If that mirror is unavailable, check https://www.gutenberg.org/policy/mirror_site.txt for current rsync endpoints.
Then set in `.env`: `INGEST_MODE=mirror`, `GUTENBERG_MIRROR_PATH=/tmp/gutenberg_mirror`, `HOST_GUTENBERG_MIRROR_PATH=/tmp/gutenberg_mirror`, optionally `INGEST_LIMIT` to cap during testing.


Full dataset via local mirror (after you rsync/download a Gutenberg mirror to disk):
```bash
# set in .env: INGEST_MODE=mirror and GUTENBERG_MIRROR_PATH=/path/to/mirror
docker compose exec app python -m rag_retrieval.ingest
```

Create a tiny local mirror for testing (no full download):
```bash
mkdir -p /tmp/gutenberg_mirror
echo "Sample text for testing." > /tmp/gutenberg_mirror/1000.txt
echo "Another small sample." > /tmp/gutenberg_mirror/2000.txt
# then set in .env: INGEST_MODE=mirror and GUTENBERG_MIRROR_PATH=/tmp/gutenberg_mirror
```

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
  -d '{"query": "Who is Elizabeth Bennet?", "mode": "hybrid", "k": 5}'
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
