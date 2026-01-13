from typing import List

import numpy as np
from sentence_transformers import SentenceTransformer

from rag_retrieval.config import get_settings


class Embedder:
    def __init__(self):
        settings = get_settings()
        self.model = SentenceTransformer(settings.embedding_model, device="cpu")
        self.dim = self.model.get_sentence_embedding_dimension()

    def embed(self, texts: List[str], batch_size: int = 32) -> np.ndarray:
        embeddings = self.model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
            device="cpu",
        )
        return embeddings


_embedder = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder
