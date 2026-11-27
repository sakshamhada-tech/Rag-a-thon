"""Deprecated compatibility shim (Phase 5 API surface).

The implementations moved to fixora.retrieval (AST chunks, hybrid BM25+vector
ranking, RRF fusion) and fixora._codetok (tokeniser, file walking). Kept so
older imports keep working; `tokenize`, `RepoRetriever` and `retriever_for`
behave as before (RepoRetriever is the hybrid retriever now).
"""
from __future__ import annotations

from ._codetok import tokenize  # noqa: F401
from .retrieval import HybridRetriever, retriever_for, build_failure_queries  # noqa: F401

RepoRetriever = HybridRetriever
