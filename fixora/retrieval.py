"""Hybrid retrieval for Fixora: AST chunks + BM25 + local vector similarity + RRF.

Pipeline per query batch:
  1. Chunk the repo with Python `ast` — one chunk per function/method/class
     (docstring + body, size-capped), one module chunk per file (imports +
     top-level code), one chunk per test function. Files that do not parse
     (e.g. the syntax-error demo) fall back to line windows, so the index
     always builds. Non-Python text files are line-windowed too.
  2. Rank candidates with BM25 (rank_bm25 if installed, else an internal
     implementation) and with vector cosine from the local embedder.
  3. Merge with Reciprocal Rank Fusion (rrf_k=60) — keyword evidence matters
     for identifiers, vector evidence covers lexical gaps; neither dominates.
  4. Optional LLM stages (query rewrite / rerank) live in query_assist.py and
     only consume `top_candidates()` from here.

Index is in-memory, rebuilt per run (signature: relpath+mtime+size) and never
persisted. No FAISS/Chroma dependency: at this corpus size (<5k chunks, 384-dim)
pure-Python dot products are single-digit milliseconds, and a native dep we
cannot verify here would be theatre, not engineering.
"""
from __future__ import annotations

import ast
import math
import os
from pathlib import Path

from .embeddings import get_embedder, cosine
from ._codetok import _iter_files, tokenize

_WINDOW = 30
_OVERLAP = 10
_CHUNK_SOFT_CAP = 4000     # chars per symbol chunk before middle-truncation
_MODULE_SOFT_CAP = 1800    # chars per module chunk
_MAX_CHUNKS = 5000
_MAX_FILE_BYTES = 400_000
_BIG_REPO_BYTES = 3_000_000
_RRF_K = 60


def _qname(node: ast.AST, parents: dict) -> str:
    parts = [getattr(node, "name", "") or ""]
    cur = node
    while cur in parents:
        cur = parents[cur]
        name = getattr(cur, "name", None)
        if not name:
            break
        parts.append(name)
    return ".".join(reversed([p for p in parts if p]))


def _cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    head, tail = text[: half - 20], text[-(half - 20):]
    return f"{head}\n…[chunk truncated]…\n{tail}"


def _is_test_path(rel: str) -> bool:
    low = rel.lower()
    return ("/tests/" in "/" + low + "/" or low.startswith("tests/")
            or low.startswith("test_") or "/test_" in "/" + low or low.endswith("_test.py"))


def chunk_python(path: Path, rel: str):
    chunks = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return chunks
    lines = text.splitlines()
    try:
        tree = ast.parse(text)
    except Exception:
        return [_window_chunk(text, rel, i) for i in range(0, max(1, math.ceil(len(lines) / max(1, _WINDOW - _OVERLAP))))]

    # map parents for qualified names
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    symbol_lines = 0
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            is_test = _is_test_path(rel) or (getattr(node, "name", "") or "").startswith("test_")
            kind = "class" if isinstance(node, ast.ClassDef) else ("test" if is_test else "function")
            seg = ast.get_source_segment(text, node) or "\n".join(lines[node.lineno - 1:node.end_lineno])
            qname = _qname(node, parents)
            chunks.append({
                "path": rel, "symbol": qname, "kind": kind,
                "start_line": node.lineno, "end_line": node.end_lineno or node.lineno,
                "is_test": is_test, "text": _cap(seg, _CHUNK_SOFT_CAP),
            })
            symbol_lines += (node.end_lineno or node.lineno) - node.lineno + 1
        for sub in ast.walk(node):
            if sub is node:
                continue
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                is_test = _is_test_path(rel) or sub.name.startswith("test_")
                kind = "test" if is_test else "method"
                seg = ast.get_source_segment(text, sub) or "\n".join(lines[sub.lineno - 1:sub.end_lineno])
                qname = _qname(sub, parents)
                chunks.append({
                    "path": rel, "symbol": qname, "kind": kind,
                    "start_line": sub.lineno, "end_line": sub.end_lineno or sub.lineno,
                    "is_test": is_test, "text": _cap(seg, _CHUNK_SOFT_CAP),
                })
                symbol_lines += (sub.end_lineno or sub.lineno) - sub.lineno + 1

    # module chunk: imports + top-level non-symbol lines
    symbol_ranges = [(c["start_line"], c["end_line"]) for c in chunks]
    module_lines = []
    for i, line in enumerate(lines, start=1):
        if any(a <= i <= b for a, b in symbol_ranges):
            continue
        stripped = line.strip()
        if stripped:
            module_lines.append((i, line))
    if module_lines:
        first, last = module_lines[0][0], module_lines[-1][0]
        body = "\n".join(l for _, l in module_lines)
        chunks.append({
            "path": rel, "symbol": "<module>", "kind": "module",
            "start_line": first, "end_line": last,
            "is_test": _is_test_path(rel),
            "text": _cap(body, _MODULE_SOFT_CAP),
        })
    return chunks


def _window_chunk(text: str, rel: str, index: int):
    lines = text.splitlines()
    step = max(1, _WINDOW - _OVERLAP)
    start = index * step
    window = lines[start:start + _WINDOW]
    is_test = _is_test_path(rel)
    return {
        "path": rel, "symbol": f"<window {index}>", "kind": "window",
        "start_line": start + 1, "end_line": start + len(window),
        "is_test": is_test, "text": "\n".join(window),
    }


class HybridRetriever:
    """AST-chunked repo index: BM25 + vector, merged with Reciprocal Rank Fusion."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._index_signature = None
        self._chunks: list[dict] = []
        self._tf: list[dict[str, int]] = []
        self._dl: list[int] = []
        self._df: dict[str, int] = {}
        self._vectors: list[list[float]] = []
        self.embedder = None
        self.backend = "not-built"
        self._last_build_ms = 0.0
        self._file_count = 0
        self._file_bytes = 0
        self._bm25 = None

    # ------------------------------------------------------------------ build
    def _iter_source_files(self):
        for path in _iter_files(self.root):
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > _MAX_FILE_BYTES:
                continue
            yield path, size

    def _compute_signature(self):
        sig = []
        for path in _iter_files(self.root):
            try:
                st = path.stat()
            except OSError:
                continue
            sig.append((str(path.relative_to(self.root)), st.st_mtime_ns, st.st_size))
        return tuple(sorted(sig))

    def _build(self):
        import time
        t0 = time.perf_counter()
        chunks: list[dict] = []
        file_count = 0
        big_repo = False
        files = list(self._iter_source_files())
        if sum(size for _, size in files) > _BIG_REPO_BYTES:
            big_repo = True
        skip_paths = set()
        if big_repo:
            # index only code + tests, not docs, when the repo is large
            for path, _ in files:
                if path.suffix.lower() not in {".py", ".pyi"}:
                    skip_paths.add(path)

        self._file_bytes = 0
        for path, _size in files:
            if path in skip_paths:
                continue
            rel = str(path.relative_to(self.root))
            self._file_bytes += _size
            if path.suffix.lower() in {".py", ".pyi"}:
                chunks.extend(chunk_python(path, rel))
            else:
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                n = math.ceil(len(text.splitlines()) / max(1, _WINDOW - _OVERLAP)) or 1
                chunks.extend(_window_chunk(text, rel, i) for i in range(n))
            file_count += 1
            if len(chunks) >= _MAX_CHUNKS:
                break
        chunks = chunks[:_MAX_CHUNKS]

        for c in chunks:
            c.setdefault("symbol", "")
            c.setdefault("kind", "window")
            c.setdefault("is_test", _is_test_path(c["path"]))

        self._chunks = chunks
        self._file_count = file_count
        self._tf = []
        self._df = {}
        for chunk in chunks:
            tf: dict[str, int] = {}
            for tok in tokenize(chunk["text"]):
                tf[tok] = tf.get(tok, 0) + 1
            self._tf.append(tf)
            for term in tf:
                self._df[term] = self._df.get(term, 0) + 1
        self._dl = [max(1, sum(t.values())) for t in self._tf]
        # BM25 object if rank_bm25 is available; else internal path will be used
        try:
            from rank_bm25 import BM25Okapi
            self._bm25 = BM25Okapi([tokenize(c["text"]) for c in chunks])
        except Exception:
            self._bm25 = None
        # vectors
        self.embedder = None
        try:
            embedder, backend = get_embedder()
            vectors = embedder.embed([c["text"] for c in chunks]) if chunks else []
            self.embedder = embedder
            self._vectors = vectors
            self.backend = backend
        except Exception:
            self._vectors = []
            self.backend = "bm25-only"
        self._last_build_ms = round((time.perf_counter() - t0) * 1000, 1)

    def ensure_current(self):
        sig = self._compute_signature()
        if sig != self._index_signature:
            self._file_bytes = 0
            self._build()
            self._index_signature = sig

    # --------------------------------------------------------------- scoring
    def _bm25_scores(self, terms: list[str]):
        if self._bm25 is not None:
            return list(self._bm25.get_scores(terms))
        n = len(self._chunks) or 1
        avgdl = (sum(self._dl) / n) if n else 1.0
        k1, b = 1.5, 0.75
        out = [0.0] * n
        for i, tf in enumerate(self._tf):
            score = 0.0
            for term in terms:
                f = tf.get(term)
                if not f:
                    continue
                df = self._df.get(term, 0)
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                denom = f + k1 * (1 - b + b * self._dl[i] / avgdl)
                score += idf * (f * (k1 + 1)) / denom
            out[i] = score
        return out

    def _vector_scores(self, query: str):
        if not self.embedder or not self._vectors:
            return [0.0] * len(self._chunks)
        qv = self.embedder.embed([query])[0]
        return [cosine(qv, v) for v in self._vectors]

    @staticmethod
    def _rank(scores):
        return sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)

    def retrieve(self, query: str, k: int = 5, extra_queries: list[str] | None = None):
        """Hybrid retrieval. Returns list of chunk dicts with "score" (RRF-fused)."""
        self.ensure_current()
        k = max(1, min(int(k or 5), 10))
        queries = [q for q in ([query] + (extra_queries or [])) if (q or "").strip()]
        if not queries or not self._chunks:
            return []

        fused: dict[int, float] = {}
        for q in queries:
            terms = tokenize(q)
            bm25_rank = self._rank(self._bm25_scores(terms))
            vec_scores = self._vector_scores(q)
            vec_rank = self._rank(vec_scores)
            for ranking in (bm25_rank, vec_rank):
                for rank, idx in enumerate(ranking[:50]):
                    fused[idx] = fused.get(idx, 0.0) + 1.0 / (_RRF_K + rank + 1)
        total = max(fused.values(), default=0.0)
        top = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:k]
        out = []
        for idx, score in top:
            score = score / total if total > 0 else 0.0
            c = self._chunks[idx]
            out.append({
                "path": c["path"], "symbol": c["symbol"], "kind": c["kind"],
                "start_line": c["start_line"], "end_line": c["end_line"],
                "is_test": c["is_test"], "score": round(float(score), 4),
                "text": c["text"],
            })
        return out

    def top_candidates(self, query: str, extra_queries: list[str] | None = None, k: int = 15):
        hits = self.retrieve(query, k=min(k, len(self._chunks)), extra_queries=extra_queries)
        return hits

    # convenience used by callers that need raw bm25 ordering for skip logic
    def stats(self):
        self.ensure_current()
        return {"chunks": len(self._chunks), "files": self._file_count,
                "backend": self.backend, "build_ms": self._last_build_ms,
                "file_bytes": self._file_bytes}


def retriever_for(sandbox) -> HybridRetriever:
    cached = getattr(sandbox, "_hybrid_retriever", None)
    if cached is None or cached.root != Path(sandbox.workspace):
        cached = HybridRetriever(Path(sandbox.workspace))
        sandbox._hybrid_retriever = cached
    return cached


def build_failure_queries(state: dict) -> list[str]:
    """Deterministic failure-driven query batch from what the run already knows."""
    parts: list[str] = [str(state.get("task") or "")]
    r = (state or {}).get("last_test_result") or {}
    diag = r.get("diagnosis") or {}
    failing = list(r.get("failing_tests") or [])
    names = []
    for t in failing[:6]:
        # tests/foo.py::TestClass::test_x -> test_x
        name = str(t).split("::")[-1]
        names.append(name.replace("_", " "))
    if diag:
        summary = str(diag.get("summary", ""))
        excs = " ".join(str(e) for e in diag.get("exceptions", []))
        parts.append(f"{summary} {excs} {diag.get('module') or ''}".strip())
    if names:
        parts.append(" ".join(names))
    return [p for p in parts if p.strip()][:4]
