"""Shared leaf utilities for code retrieval (no intra-package imports)."""
from __future__ import annotations

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
