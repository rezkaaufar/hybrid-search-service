from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import List

import numpy as np
from sentence_transformers import CrossEncoder

logger = logging.getLogger(__name__)

# Resolve model path: prefer pre-downloaded local path to avoid HF calls at runtime.
_DEFAULT_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"


class Reranker:
    """Thin wrapper around a SentenceTransformers CrossEncoder model.

    The CrossEncoder accepts (query, document) pairs and returns relevance
    scores.  Higher scores mean more relevant.  The underlying model runs
    entirely on CPU.
    """

    def __init__(self, model_name_or_path: str) -> None:
        logger.info("Loading reranker model from %s …", model_name_or_path)
        # max_length=512 keeps memory predictable; most reviews fit comfortably.
        self._model = CrossEncoder(
            model_name_or_path,
            device="cpu",
            max_length=512,
        )
        logger.info("Reranker model loaded.")

    def rerank(self, query: str, documents: List[str]) -> np.ndarray:
        """Score every (query, document) pair.

        Args:
            query: The user query string.
            documents: Ordered list of document texts.

        Returns:
            1-D numpy array of float scores, one per document.
        """
        if not documents:
            return np.array([], dtype=np.float32)

        pairs = [(query, doc) for doc in documents]
        scores: np.ndarray = self._model.predict(pairs, show_progress_bar=False)
        return scores.astype(np.float32)


@lru_cache(maxsize=1)
def get_reranker() -> Reranker:
    """Singleton factory — loads once and reuses across requests."""
    from reranker_service.config import get_settings

    settings = get_settings()

    # Prefer a pre-baked local copy (set during Docker image build).
    model_path = settings.model_local_path or settings.reranker_model

    # If the local path was set but the directory doesn't exist, fall back to
    # the HF hub identifier so the container can still start in dev.
    if settings.model_local_path and not os.path.isdir(settings.model_local_path):
        logger.warning(
            "MODEL_LOCAL_PATH %s does not exist; falling back to %s",
            settings.model_local_path,
            settings.reranker_model,
        )
        model_path = settings.reranker_model

    return Reranker(model_path)
