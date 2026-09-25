"""Embedding providers.

Everything else depends only on the `Embedder` protocol, so swapping the local
model for a hosted one (e.g. a code-specialised embedding API) is a change here
plus the `embedding_dim` setting.
"""

from collections.abc import Sequence
from functools import lru_cache
from typing import Protocol

import numpy as np

from app.config import get_settings


class Embedder(Protocol):
    dim: int

    def embed_documents(self, texts: Sequence[str]) -> list[np.ndarray]: ...

    def embed_query(self, text: str) -> np.ndarray: ...


class FastEmbedEmbedder:
    """Local ONNX embeddings via fastembed (downloads the model on first use)."""

    def __init__(self, model_name: str, dim: int):
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name=model_name)
        self.dim = dim

    def embed_documents(self, texts: Sequence[str]) -> list[np.ndarray]:
        return list(self._model.passage_embed(list(texts), batch_size=64))

    def embed_query(self, text: str) -> np.ndarray:
        return next(iter(self._model.query_embed([text])))


@lru_cache
def get_embedder() -> Embedder:
    settings = get_settings()
    return FastEmbedEmbedder(settings.embedding_model, settings.embedding_dim)
