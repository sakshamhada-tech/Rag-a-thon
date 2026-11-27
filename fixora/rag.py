"""Local retrieval-augmented generation (RAG) for Fixora.

Everything here is 100% offline and dependency-free. The retriever is a BM25-style
lexical index over text chunks of the target repository — build it, score a query,
inject the best chunks into the agent's prompts (or hand them to the model via the
`retrieve_code` tool). There are no embeddings, no network calls, no model files,
no API keys and no added runtime dependencies: RAG here costs exactly nothing and
cannot be rate-limited or unreachable.

Honesty note: this is *lexical* retrieval (term overlap weighted by IDF), not
neural semantic embeddings. It ranks the right file highly whenever the query
shares vocabulary with it — which covers the overwhelmingly common cases in code
(identifiers, imports, test names, error messages). It does not understand
semantics beyond shared terms. The upgrade path is to add an embedding backend
behind the same interface later; nothing else would change.
"""
from __future__ import annotations

import math
import os
import re
from pathlib import Path

from .security import is_sensitive_path

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", ".venv", "venv",
              "build", "dist", ".mypy_cache", ".ruff_cache", "target"}
_TEXT_SUFFIXES = {".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java",
                  ".rb", ".c", ".cc", ".cpp", ".h", ".hpp", ".md", ".txt", ".rst",
                  ".toml", ".cfg", ".ini", ".yaml", ".yml", ".json", ".sh", ".sql"}
_MAX_FILE_BYTES = 200_000
_CHUNK_LINES = 30
_CHUNK_OVERLAP = 10
_MAX_CHUNKS = 5000

_K1 = 1.5
_B = 0.75

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SNAKE_RE = re.compile(r"_+")
_DIGIT_RE = re.compile(r"(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])")


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumerics, also splitting snake_case and camelCase parts."""
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(text):
        for part in _CAMEL_RE.split(raw):
            for digit_part in _DIGIT_RE.split(part):
                for piece in _SNAKE_RE.split(digit_part.lower()):
                    if len(piece) > 1 and not piece.isdigit():
                        tokens.append(piece)
    return tokens


def _iter_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            path = Path(dirpath) / name
            if path.suffix.lower() not in _TEXT_SUFFIXES:
                continue
            if is_sensitive_path(path):
                continue
            try:
                if path.stat().st_size > _MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield path


def _signature(root: Path):
    """(relpath, mtime_ns, size) tuple for every indexable file — cheap cache key."""
    sig = []
    for path in _iter_files(root):
        try:
            st = path.stat()
        except OSError:
            continue
        sig.append((str(path.relative_to(root)), st.st_mtime_ns, st.st_size))
    return tuple(sorted(sig))


class Chunk:
    __slots__ = ("path", "start_line", "end_line", "text", "tf", "length")

    def __init__(self, path: str, start_line: int, end_line: int, text: str):
        self.path = path
        self.start_line = start_line
        self.end_line = end_line
        self.text = text
        self.tf: dict[str, int] = {}
        for tok in tokenize(text):
            self.tf[tok] = self.tf.get(tok, 0) + 1
        self.length = max(1, sum(self.tf.values()))


class RepoRetriever:
    """BM25 retriever over line-window chunks of a repository."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._signature = None
        self._chunks: list[Chunk] = []
        self._df: dict[str, int] = {}
        self._avgdl = 1.0
        self._file_count = 0

    # ------------------------------------------------------------- indexing
    def _build(self):
        chunks: list[Chunk] = []
        file_count = 0
        for path in _iter_files(self.root):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            file_count += 1
            rel = str(path.relative_to(self.root))
            lines = text.splitlines()
            if not lines:
                continue
            step = max(1, _CHUNK_LINES - _CHUNK_OVERLAP)
            for start in range(0, len(lines), step):
                window = lines[start:start + _CHUNK_LINES]
                chunks.append(Chunk(rel, start + 1, start + len(window), "\n".join(window)))
                if len(chunks) >= _MAX_CHUNKS:
                    break
            if len(chunks) >= _MAX_CHUNKS:
                break

        df: dict[str, int] = {}
        for chunk in chunks:
            for term in chunk.tf:
                df[term] = df.get(term, 0) + 1

        self._chunks = chunks
        self._df = df
        self._file_count = file_count
        self._avgdl = (sum(c.length for c in chunks) / len(chunks)) if chunks else 1.0

    def ensure_current(self):
        """(Re)build the index if any indexed file changed since it was built."""
        sig = _signature(self.root)
        if sig != self._signature:
            self._build()
            self._signature = sig

    # ------------------------------------------------------------- retrieval
    def _score(self, chunk: Chunk, query_terms: list[str]) -> float:
        n = len(self._chunks) or 1
        score = 0.0
        for term in query_terms:
            tf = chunk.tf.get(term)
            if not tf:
                continue
            df = self._df.get(term, 0)
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            denom = tf + _K1 * (1 - _B + _B * chunk.length / self._avgdl)
            score += idf * (tf * (_K1 + 1)) / denom
        return score

    def retrieve(self, query: str, k: int = 6) -> list[dict]:
        self.ensure_current()
        terms = tokenize(query or "")
        if not terms:
            return []
        scored = ((self._score(c, terms), c) for c in self._chunks)
        top = sorted(((s, c) for s, c in scored if s > 0),
                     key=lambda pair: pair[0], reverse=True)[:max(1, min(int(k or 6), 10))]
        return [{"path": c.path, "start_line": c.start_line, "end_line": c.end_line,
                 "score": round(s, 4), "text": c.text} for s, c in top]

    def stats(self) -> dict:
        self.ensure_current()
        return {"chunks": len(self._chunks), "files": self._file_count}


def retriever_for(sandbox) -> RepoRetriever:
    """One cached retriever per sandbox workspace; rebuilt automatically on edits."""
    cached = getattr(sandbox, "_rag_retriever", None)
    if cached is None or cached.root != Path(sandbox.workspace):
        cached = RepoRetriever(Path(sandbox.workspace))
        sandbox._rag_retriever = cached
    return cached
