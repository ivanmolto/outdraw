"""Text embeddings (briefing §9): the semantic run cache (T36) and the refine
parser's tag hints (T37). Embeddings never rank acts."""

from draw.embed.embedder import Embedder, HashingEmbedder, HttpEmbedder, embedder_from_env

__all__ = ["Embedder", "HashingEmbedder", "HttpEmbedder", "embedder_from_env"]
