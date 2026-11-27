"""RAG tool: retrieve the most relevant repo chunks for a query (BM25, offline)."""
from __future__ import annotations

import json

from ..rag import retriever_for


def retrieve_code(sandbox, query: str, k: int = 5) -> str:
    query = (query or "").strip()
    if not query:
        return json.dumps({"error": "retrieve_code: 'query' must be a non-empty string"})
    try:
        k = max(1, min(int(k or 5), 10))
    except (TypeError, ValueError):
        k = 5
    retriever = retriever_for(sandbox)
    chunks = retriever.retrieve(query, k)
    hits = [{"path": c["path"], "start_line": c["start_line"],
             "end_line": c["end_line"], "score": c["score"],
             "snippet": c["text"][:1200]} for c in chunks]
    return json.dumps({"ok": True, "chunks": hits,
                       "index": retriever.stats(),
                       "note": "lexical retrieval; pair with read_file for full files"})
