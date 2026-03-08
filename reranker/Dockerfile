FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the cross-encoder model so the container starts offline.
# Pass --build-arg HF_TOKEN=<token> only if your network requires auth.
ARG RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
ARG HF_TOKEN=""
ENV MODEL_LOCAL_PATH=/app/models/reranker

COPY download_model.py /tmp/download_model.py
RUN python /tmp/download_model.py && rm /tmp/download_model.py

# Prevent runtime HF hub calls
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

# Copy application source
COPY src/ src/
ENV PYTHONPATH=/app/src

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8080}/health || exit 1

CMD ["sh", "-c", "uvicorn reranker_service.api:app --host 0.0.0.0 --port ${PORT:-8080}"]
