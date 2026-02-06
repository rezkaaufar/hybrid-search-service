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

## Load test with k6
Prereqs: Docker (or k6 binary). Start the app first (e.g., `docker compose up`).

Run with Docker + web dashboard (listens on http://localhost:5665 while the test runs):
```bash
cd retrieval
docker run --rm -it \
  --network host \
  -p 5665:5665 \
  -v "$PWD/loadtest:/scripts" \
  -e BASE_URL=http://localhost:8000 \
  grafana/k6:latest \
  run --out web-dashboard /scripts/k6-query.js
```
On Docker Desktop (macOS/Windows) where `--network host` is limited, swap `--network host` with `--add-host host.docker.internal:host-gateway` and set `-e BASE_URL=http://host.docker.internal:8000`.

## Full monitoring (Prometheus + Grafana + exporters)
Starts Prometheus (with remote-write receiver), Grafana, cAdvisor, node-exporter, and Postgres exporter.

```bash
cd retrieval
docker compose --profile monitoring up -d prometheus grafana cadvisor node-exporter postgres-exporter
```

Then run k6 pushing its metrics to Prometheus (works with the remote-write receiver):
```bash
docker run --rm -it \
  --add-host host.docker.internal:host-gateway \
  -v "$PWD/loadtest:/scripts" \
  -e BASE_URL=http://host.docker.internal:8000 \
  grafana/k6:latest \
  run --out experimental-prometheus-rw=http://host.docker.internal:9090/api/v1/write /scripts/k6-query.js
```
If you’re on Linux and `--network host` is available, you can replace the `--add-host` line with `--network host` and set `BASE_URL=http://localhost:8000`.

Compose-native way (avoids host/localhost issues):
```bash
cd retrieval
# start monitoring and app (if not already)
docker compose --profile monitoring up -d prometheus grafana cadvisor node-exporter postgres-exporter app
# run k6 inside the compose network (prometheus is included in the loadtest profile)
docker compose --profile loadtest run --rm k6
```
Notes for Grafana dashboard (ID 19665):
- Ensure time range covers your k6 run (live data only).
- Set the `job` variable to `k6` (the job label used by the k6 remote-write output).
- The compose k6 service exports p90/p95/p99 stats; use the standard k6 Prometheus dashboard (ID 19665). Latency series live under `k6_http_req_duration{stat="p(95)"}` etc.

Dashboards:
- Grafana: http://localhost:3000 (admin / admin)
- Prometheus: http://localhost:9090
- cAdvisor UI (per-container CPU/mem): http://localhost:8080

Metrics coverage:
- k6 metrics (latency, RPS, errors) via remote write
- CPU/memory per container via cAdvisor
- Host CPU/memory via node-exporter
- Postgres connections/locks via postgres-exporter

Common tunables (env vars): `RPS` (default 20), `DURATION` (default 2m), `MODE` (lexical/semantic/hybrid), `K` (top-k), `QUERIES` (comma-separated list), `VUS`/`MAX_VUS`, `SLEEP` (per-iteration pause). Example: `-e RPS=50 -e DURATION=5m -e MODE=hybrid`.
