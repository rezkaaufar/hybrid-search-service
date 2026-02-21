FROM python:3.11-slim

ARG HF_TOKEN=""
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TOKENIZERS_PARALLELISM=false \
    PYTHONPATH=/app/src \
    HUGGINGFACE_HUB_TOKEN=$HF_TOKEN

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential libpq-dev curl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Download and persist the embedding model locally (uses HF token if provided)
RUN python -c "from sentence_transformers import SentenceTransformer; m=SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2', device='cpu'); m.save('/app/models/all-MiniLM-L6-v2')"

# Runtime: force offline to avoid HF calls
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    MODEL_LOCAL_PATH=/app/models/all-MiniLM-L6-v2

COPY src /app/src

EXPOSE 8080

CMD ["sh", "-c", "uvicorn rag_retrieval.api:app --host 0.0.0.0 --port ${PORT:-8080}"]
