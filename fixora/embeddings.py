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
import socket
import ssl

from pathlib import Path
import tempfile

from ._codetok import tokenize

_NET_PROBE_CACHE: dict[tuple, bool] = {}


def _fastembed_model_cached(model_name: str) -> bool:
    """Best-effort check for a usable on-disk fastembed model payload.

    Mirrors fastembed's default cache locations (FASTEMBED_HOME, ~/.cache/fastembed,
    <tmp>/fastembed_cache). A directory that exists but holds no payload files
    (e.g. a half-finished download) does NOT count as cached.
    """
    slug = model_name.split("/")[-1]
    bases = []
    home = os.environ.get("FASTEMBED_HOME")
    if home:
        bases.append(Path(home))
    bases.append(Path.home() / ".cache" / "fastembed")
    bases.append(Path(tempfile.gettempdir()) / "fastembed_cache")
    payload_ext = {".onnx", ".bin", ".model", ".json"}
    for base in bases:
        root = base / slug
        if not root.is_dir():
            continue
        for child in root.rglob("*"):
            if child.is_file() and child.suffix.lower() in payload_ext:
                return True
    return False


def _network_reachable(host: str = "huggingface.co", port: int = 443, timeout: float = 3.0) -> bool:
    """True only if a real TLS handshake to the host succeeds.

    Sandboxes/proxies may accept the TCP connect and then drop the TLS session;
    the fastembed library would then burn ~40 s retrying before giving up, so
    we probe the handshake itself. Result memoised per host:port.
    """
    key = (host, port)
    cached = _NET_PROBE_CACHE.get(key)
    if cached is not None:
        return cached
    ok = False
    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                ok = tls.version() is not None
    except (OSError, ssl.SSLError):
        ok = False
    _NET_PROBE_CACHE[key] = ok
    return ok


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
        # If the model is already cached, load strictly from disk so offline runs
        # never contend with the hub. When nothing is cached yet (first run), fall
        # through to the normal path — a one-time ~30 MB download.
        # Never call into fastembed's download path when the hub is unreachable:
        # the library retries with sleeps (~40 s) before giving up. Decide up
        # front from cache presence + a cheap network probe.
        if not _fastembed_model_cached(model_name) and not _network_reachable():
            raise RuntimeError(
                "fastembed model not cached and huggingface.co unreachable; "
                "using the hash embedder instead. Set FIXORA_EMBEDDER=hash to "
                "skip this probe, or pre-download the model to enable fastembed.")
        try:
            self._model = TextEmbedding(model_name, local_files_only=True)
        except Exception:
            self._model = TextEmbedding(model_name)      # one-time download, may raise
        self.dims = self._model.embedding_size if hasattr(self._model, "embedding_size") else 384

    def embed(self, texts: list[str]) -> list[list[float]]:
        vecs = [list(map(float, v)) for v in self._model.embed(list(texts))]
        return vecs


# Process-wide memo: the fastembed probe may download a ~30 MB model on first
# use, so repeated retriever constructions must not repeat it. Keyed by the
# requested backend so tests that flip FIXORA_EMBEDDER still see their value.
_embedder_cache: dict[str, tuple] = {}


def get_embedder():
    """Returns (embedder, backend_name). Never raises. Memoised per process."""
    pref = (os.environ.get("FIXORA_EMBEDDER") or "auto").strip().lower()
    cached = _embedder_cache.get(pref)
    if cached is not None:
        return cached
    if pref in ("auto", "fastembed"):
        try:
            result = (FastembedEmbedder(), "fastembed")
            _embedder_cache[pref] = result
            return result
        except Exception:
            pass  # fall through to hash; caller logs the backend actually used
    result = (HashEmbedder(), "hash")
    _embedder_cache[pref] = result
    return result


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
