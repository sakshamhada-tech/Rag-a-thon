"""Hybrid RAG tool: BM25 + local vector retrieval, fused with RRF (offline)."""
from __future__ import annotations

import json

from ..retrieval import retriever_for


def _render(sandbox, query: str, k: int):
    retriever = retriever_for(sandbox)
    chunks = retriever.retrieve(query, k)
    hits = [{"path": c["path"], "symbol": c.get("symbol", ""), "kind": c.get("kind", ""),
             "start_line": c["start_line"], "end_line": c["end_line"],
             "is_test": c.get("is_test", False), "score": c["score"],
             "snippet": c["text"][:1200]} for c in chunks]
    return {"ok": True, "chunks": hits, "index": retriever.stats(),
            "backend": retriever.stats()["backend"],
            "note": "hybrid lexical+vector retrieval; pair with read_file for full files"}


def search_semantic(sandbox, query: str, k: int = 5) -> str:
    query = (query or "").strip()
    if not query:
        return json.dumps({"error": "search_semantic: 'query' must be a non-empty string"})
    try:
        k = max(1, min(int(k or 5), 10))
    except (TypeError, ValueError):
        k = 5
    try:
        return json.dumps(_render(sandbox, query, k))
    except Exception as e:
        return json.dumps({"error": f"search_semantic failed: {e}"})


def retrieve_code(sandbox, query: str, k: int = 5) -> str:
    """Deprecated alias of search_semantic (kept for compatibility)."""
    return search_semantic(sandbox, query, k)
