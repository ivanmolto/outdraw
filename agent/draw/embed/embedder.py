"""Embedders: text to a unit vector of `EMBEDDING_DIM` floats (the `embeddings` column).

`HttpEmbedder` calls an EmbeddingGemma server speaking the text-embeddings-inference API
(`POST {EMBED_URL}/embed {"inputs": [...]}` → `[[float, ...]]`) and truncates its
Matryoshka output to 256 dimensions. Without `EMBED_URL`, `HashingEmbedder` stands in: word
and character-trigram features hashed into 256 signed buckets. It needs no model or network
and keeps reordered, re-cased or re-hyphenated text close; it does not know synonyms.
`SEMANTIC_CACHE=off` turns embeddings off altogether (`embedder_from_env` returns None).
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from typing import Final, Protocol

import httpx

from draw.db.models import EMBEDDING_DIM

__all__ = ["Embedder", "HashingEmbedder", "HttpEmbedder", "embedder_from_env", "unit"]

# EmbeddingGemma's prompt for symmetric similarity between two texts.
GEMMA_PROMPT: Final = "task: sentence similarity | query: "


class Embedder(Protocol):
    async def embed(self, text: str) -> list[float]:
        """A unit vector of `EMBEDDING_DIM` floats."""
        ...


def unit(vector: list[float]) -> list[float]:
    """`vector` scaled to length 1 (all zeros stays all zeros)."""
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else vector


class HashingEmbedder:
    """Signed feature hashing of words and character trigrams; deterministic across runs
    (blake2b, not Python's salted `hash`)."""

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim

    def _features(self, text: str) -> list[str]:
        words = re.findall(r"\w+", text.lower())
        grams = [f"#{w[i : i + 3]}" for w in (f" {w} " for w in words) for i in range(len(w) - 2)]
        return words + grams

    async def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        for feature in self._features(text):
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dim
            vector[bucket] += 1.0 if digest[4] & 1 else -1.0
        return unit(vector)


class HttpEmbedder:
    """An EmbeddingGemma text-embeddings-inference server; output truncated to `dim`."""

    def __init__(self, url: str, *, timeout: float = 5.0, dim: int = EMBEDDING_DIM) -> None:
        self.url = url.rstrip("/") + "/embed"
        self.timeout = timeout
        self.dim = dim

    async def embed(self, text: str) -> list[float]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(
                self.url, json={"inputs": [GEMMA_PROMPT + text], "truncate": True}
            )
            res.raise_for_status()
        rows = res.json()
        vector = [float(x) for x in rows[0][: self.dim]]
        if len(vector) != self.dim:
            raise ValueError(f"embedding has {len(vector)} dimensions, expected {self.dim}")
        return unit(vector)


def embedder_from_env() -> Embedder | None:
    """`SEMANTIC_CACHE=off` → None; `EMBED_URL` → `HttpEmbedder`; else `HashingEmbedder`."""
    if os.environ.get("SEMANTIC_CACHE", "on").strip().lower() == "off":
        return None
    url = os.environ.get("EMBED_URL", "").strip()
    return HttpEmbedder(url) if url else HashingEmbedder()
