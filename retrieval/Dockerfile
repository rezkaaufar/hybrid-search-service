FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TOKENIZERS_PARALLELISM=false \
    PYTHONPATH=/app/src

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential libpq-dev curl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY src /app/src

EXPOSE 8000

CMD ["uvicorn", "rag_retrieval.api:app", "--host", "0.0.0.0", "--port", "8000"]
