# Reranker Service

A CPU-optimized cross-encoder reranking microservice that takes the output of the retrieval service (a query + list of documents) and returns them sorted by relevance score.

## Architecture

| Component | Description |
|-----------|-------------|
| `src/reranker_service/api.py` | FastAPI service with `/rerank` endpoint |
| `src/reranker_service/model.py` | CrossEncoder singleton wrapper |
| `src/reranker_service/config.py` | Environment-variable configuration via Pydantic |
| `loadtest/k6-reranker.js` | k6 load test script |

### Model

**`cross-encoder/ms-marco-MiniLM-L-6-v2`** (default)

- ~22 M parameters – fast on CPU
- Trained on MS MARCO passage ranking
- Scores (query, document) pairs; higher = more relevant
- Downloads once at image build time; runs fully offline at runtime

You can swap the model via `RERANKER_MODEL` – any HuggingFace cross-encoder compatible with `sentence-transformers.CrossEncoder` works.

---

## Quick Start

```bash
cd reranker
docker compose up --build
```

The service starts on **port 8080** by default.

### Health check

```bash
curl http://localhost:8080/health
# {"status":"ok"}
```

### Rerank documents

```bash
curl -X POST http://localhost:8080/rerank \
  -H "Content-Type: application/json" \
  -d '{
    "query": "battery life wireless headphones",
    "top_k": 3,
    "documents": [
      {
        "chunk_id": 1,
        "document_id": 10,
        "content": "Battery died after only 4 hours of use.",
        "score": 0.85,
        "source_title": "Review A"
      },
      {
        "chunk_id": 2,
        "document_id": 11,
        "content": "I get 30+ hours on a single charge. Best headphones I have owned.",
        "score": 0.80,
        "source_title": "Review B"
      },
      {
        "chunk_id": 3,
        "document_id": 12,
        "content": "Great sound quality but the ear cups get hot after an hour.",
        "score": 0.72,
        "source_title": "Review C"
      }
    ]
  }'
```

**Response:**

```json
{
  "query": "battery life wireless headphones",
  "results": [
    {
      "rank": 1,
      "chunk_id": 2,
      "document_id": 11,
      "content": "I get 30+ hours on a single charge. Best headphones I have owned.",
      "reranker_score": 8.34,
      "original_score": 0.80,
      "source_title": "Review B"
    },
    {
      "rank": 2,
      "chunk_id": 1,
      "document_id": 10,
      "content": "Battery died after only 4 hours of use.",
      "reranker_score": 5.12,
      "original_score": 0.85,
      "source_title": "Review A"
    },
    {
      "rank": 3,
      "chunk_id": 3,
      "document_id": 12,
      "content": "Great sound quality but the ear cups get hot after an hour.",
      "reranker_score": 1.87,
      "original_score": 0.72,
      "source_title": "Review C"
    }
  ],
  "reranked_count": 3,
  "returned_count": 3,
  "latency_ms": 42.5
}
```

---

## Configuration

Edit `.env` (copy from `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `RERANKER_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | HuggingFace model ID |
| `MODEL_LOCAL_PATH` | _(empty)_ | Path to pre-downloaded model (set automatically in Docker) |
| `PORT` | `8080` | Port the service listens on |
| `LOG_LEVEL` | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`) |
| `RERANK_CONCURRENCY` | `2` | Max simultaneous reranking calls (semaphore) |
| `MAX_DOCS_PER_REQUEST` | `100` | Hard cap on documents per request |

---

## Integration with the Retrieval Service

The `documents` array in `/rerank` matches the `results` array returned by the retrieval service's `/query` endpoint. A typical pipeline:

```
Client → POST /query (retrieval, port 8000)
               ↓
         ChunkResult list
               ↓
Client → POST /rerank (reranker, port 8080)
               ↓
         RankedDocument list (sorted by cross-encoder score)
```

---

## Load Testing

### Standalone k6

```bash
k6 run loadtest/k6-reranker.js
```

### Via Docker Compose (with Prometheus metrics)

```bash
# Start the reranker + Prometheus
docker compose --profile loadtest up --build

# In a separate terminal, run k6
docker compose --profile loadtest run --rm k6
```

### Tunables

| Env var | Default | Description |
|---------|---------|-------------|
| `RPS` | `20` | Target requests per second |
| `DURATION` | `2m` | Test duration |
| `TOP_K` | `5` | `top_k` param sent to `/rerank` |
| `VUS` | `50` | Pre-allocated virtual users |
| `SLEEP` | `0` | Seconds to sleep between iterations |

---

## Monitoring Stack (optional)

```bash
docker compose --profile monitoring up
```

| Service | URL |
|---------|-----|
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (admin / admin) |
| cAdvisor | http://localhost:8081 |
| node-exporter | http://localhost:9100 |

---

## Development (without Docker)

```bash
cd reranker
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export $(cat .env | xargs)
cd src
uvicorn reranker_service.api:app --host 0.0.0.0 --port 8080 --reload
```
