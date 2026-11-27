"""Local, free embeddings for Fixora retrieval.

Two backends, selected by FIXORA_EMBEDDER (auto|fastembed|hash), auto by default:

- fastembed: ONNX runtime, default model BAAI/bge-small-en-v1.5 (384-dim), lazily
  loaded, cached. No PyTorch, no API key, no rate limit. Requires the model file —
  if fastembed is not installed or the model cannot be loaded (e.g. no network at
  build/runtime), we fall back to the local hash embedder instead of failing.
- hash: deterministic bag-of-ngrams (token unigrams + char trigrams) projected into
  384 buckets via SHA-256, tf-scaled and L2-normalised. Pure stdlib, zero files,
  zero network, zero cost, always available. Quality is below a neural model but
  fine as a hybrid-mode partner for BM25.

Neither backend calls Groq — Groq has no embeddings endpoint — and neither is
used from any external service.
"""
from __future__ import annotations

import hashlib
import math
import os
import re

from ._codetok import tokenize


class HashEmbedder:
    name = "hash"
    dims = 384

    def _features(self, text: str):
        tokens = tokenize(text)
        yield from tokens
        joined = f" {text.lower()} "
        for i in range(len(joined) - 3):
            ch = joined[i:i + 3]
            if any(c.isalnum() for c in ch):
                yield "§" + ch

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dims
            for feat in self._features(text):
                h = hashlib.sha256(feat.encode("utf-8")).digest()
                bucket = int.from_bytes(h[:2], "little") % self.dims
                sign = 1.0 if h[2] & 1 else -1.0
                vec[bucket] += sign
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


class FastembedEmbedder:
    name = "fastembed"

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        from fastembed import TextEmbedding              # raises ImportError if absent
        self._model = TextEmbedding(model_name)          # raises on model load failure
        self.dims = self._model.embedding_size if hasattr(self._model, "embedding_size") else 384

    def embed(self, texts: list[str]) -> list[list[float]]:
        vecs = [list(map(float, v)) for v in self._model.embed(list(texts))]
        return vecs


def get_embedder():
    """Returns (embedder, backend_name). Never raises."""
    pref = (os.environ.get("FIXORA_EMBEDDER") or "auto").strip().lower()
    if pref in ("auto", "fastembed"):
        try:
            return FastembedEmbedder(), "fastembed"
        except Exception:
            if pref == "fastembed":
                pass  # fall through to hash; caller logs the backend actually used
    return HashEmbedder(), "hash"


def probe_groq_embeddings(settings) -> dict:
    """One-off check: does the configured Groq key list any embedding models?

    Logs and reports only; Fixora never uses Groq for embeddings. Tolerates all
    failures (no key, no network, provider down) — returns {"available": False, ...}.
    """
    result = {"available": False, "models": [], "note": "not probed"}
    try:
        provider = (getattr(settings, "llm_provider", "") or "").strip().lower()
        if provider != "groq" or not getattr(settings, "groq_api_key", None):
            result["note"] = "groq not the configured provider or no key set"
            return result
        from openai import OpenAI
        client = OpenAI(api_key=settings.groq_api_key, base_url=settings.groq_base_url,
                        timeout=5.0, max_retries=0)
        models = [m.id for m in client.models.list().data]
        embeds = [m for m in models if "embed" in m.lower()]
        result.update(available=bool(embeds), models=embeds[:10],
                      note="groq has no embeddings; local embedder is used regardless")
    except Exception as e:
        result["note"] = f"probe failed: {type(e).__name__}"
    return result


def cosine(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))
