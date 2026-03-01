import os
from sentence_transformers import CrossEncoder

model_name = os.getenv("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
local_path = "/app/models/reranker"

# HF_TOKEN is passed as a Docker ARG and picked up automatically by huggingface_hub
print(f"Downloading {model_name} …")
model = CrossEncoder(model_name)
model.save(local_path)
print(f"Model saved to {local_path}")
