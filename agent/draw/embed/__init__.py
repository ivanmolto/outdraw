"""Text embeddings (briefing §9): the semantic run cache (T36). Embeddings never rank acts."""

from draw.embed.embedder import Embedder, HashingEmbedder, HttpEmbedder, embedder_from_env

__all__ = ["Embedder", "HashingEmbedder", "HttpEmbedder", "embedder_from_env"]
