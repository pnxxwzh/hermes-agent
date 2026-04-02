"""Embedding helpers for SparkGraph semantic recall."""

from __future__ import annotations

import hashlib
import math
import struct
from typing import Iterable

from openai import OpenAI

from agent.sparkgraph.config import SparkGraphEmbeddingConfig


def embedding_enabled(config: SparkGraphEmbeddingConfig | None) -> bool:
    if config is None:
        return False
    return bool(config.model and config.base_url and config.provider)


def embedding_content_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def create_embedding(text: str, config: SparkGraphEmbeddingConfig) -> list[float]:
    """Generate a single embedding vector using the configured runtime."""
    client = OpenAI(
        api_key=config.api_key or "EMPTY",
        base_url=config.base_url,
        timeout=config.timeout,
        max_retries=0,
    )
    response = client.embeddings.create(
        model=config.model,
        input=str(text or ""),
    )
    vector = response.data[0].embedding if response and response.data else None
    if not isinstance(vector, list) or not vector:
        raise ValueError("embedding response did not contain a vector")
    return [float(v) for v in vector]


def pack_embedding(values: Iterable[float]) -> bytes:
    vector = [float(v) for v in values]
    if not vector:
        raise ValueError("embedding vector must not be empty")
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack_embedding(blob: bytes, dims: int) -> list[float]:
    if dims <= 0:
        return []
    return list(struct.unpack(f"<{dims}f", blob))


def cosine_similarity(left: Iterable[float], right: Iterable[float]) -> float:
    left_vec = [float(v) for v in left]
    right_vec = [float(v) for v in right]
    if not left_vec or not right_vec or len(left_vec) != len(right_vec):
        return 0.0
    dot = sum(a * b for a, b in zip(left_vec, right_vec))
    left_norm = math.sqrt(sum(a * a for a in left_vec))
    right_norm = math.sqrt(sum(b * b for b in right_vec))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)
