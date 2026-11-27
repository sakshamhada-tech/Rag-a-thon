"""Read-only repository exploration tools: list_tree, read_file, search_code,
get_dependencies. All are sandboxed and size-limited; errors are returned as
{"error": ...} payloads, never raised.
"""
from __future__ import annotations

import ast
import json
import os
import re
import tomllib
from pathlib import Path

from ..config import READ_FILE_MAX_LINES, SEARCH_MAX_MATCHES, MAX_OUTPUT_CHARS

_TEXT_EXT = {".py", ".txt", ".md", ".toml", ".cfg", ".ini", ".yaml", ".yml",
             ".json", ".csv", ".sh", ".env", ".example"}
_SKIP = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules"}


def _err(msg: str) -> str:
    return json.dumps({"error": msg})


def list_tree(sandbox, path: str = ".", max_depth: int = 3, max_depth_cap: int = 6) -> str:
    try:
        root = sandbox.resolve(path or ".")
    except Exception as e:
        return _err(str(e))
    if not root.is_dir():
        return _err(f"not a directory: {path}")
    max_depth = min(max(int(max_depth or 3), 1), max_depth_cap)
    lines, count = [], 0
    base_depth = len(root.parts)
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP)
        depth = len(Path(dirpath).parts) - base_depth
        if depth >= max_depth:
            dirs[:] = []
        indent = "  " * depth
        name = Path(dirpath).name or "."
        lines.append(f"{indent}{name}/")
        count += 1
        for f in sorted(files):
            if f.endswith((".pyc", ".pyo")):
                continue
            lines.append(f"{indent}  {f}")
            count += 1
        if count > 200:
            lines.append("...[truncated: too many entries]")
            break
    return "\n".join(lines)


def read_file(sandbox, path: str, start_line: int | None = None, end_line: int | None = None) -> str:
    try:
        target = sandbox.resolve(path)
    except Exception as e:
        return _err(str(e))
    if not target.is_file():
        return _err(f"not a file: {path}")
    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        return _err(f"cannot read {path}: {e}")
    total = len(lines)
    start = max(int(start_line or 1), 1)
    end = min(int(end_line or total), total)
    if end - start + 1 > READ_FILE_MAX_LINES:
        end = start + READ_FILE_MAX_LINES - 1
    body = "\n".join(f"{i}: {lines[i - 1]}" for i in range(start, end + 1))
    header = f"===== {sandbox.rel(target)} (lines {start}-{end} of {total}) ====="
    out = f"{header}\n{body}"
    return out if len(out) <= MAX_OUTPUT_CHARS else out[:MAX_OUTPUT_CHARS] + "\n...[truncated]"


def search_code(sandbox, pattern: str, path: str = ".") -> str:
    try:
        root = sandbox.resolve(path or ".")
    except Exception as e:
        return _err(str(e))
    if not pattern:
        return _err("pattern is required")
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return _err(f"invalid regex {pattern!r}: {e}")
    files = [root] if root.is_file() else _walk_text_files(root)
    hits, files_with_hits = [], set()
    for fp in files:
        try:
            content = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(content.splitlines(), 1):
            if rx.search(line):
                hits.append(f"{sandbox.rel(fp)}:{i}: {line.strip()}")
                files_with_hits.add(fp)
                if len(hits) >= SEARCH_MAX_MATCHES:
                    hits.append(f"...[capped at {SEARCH_MAX_MATCHES} matches]")
                    return "\n".join(hits)
    return "\n".join(hits) if hits else "(no matches)"


def get_dependencies(sandbox) -> str:
    ws = Path(os.path.realpath(sandbox.workspace))
    deps: dict[str, list[str]] = {"requirements_txt": [], "pyproject": [], "top_level_imports": []}
    req = ws / "requirements.txt"
    if req.is_file():
        for raw in req.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if line and not line.startswith(("#", "-")):
                deps["requirements_txt"].append(line)
    pyproject = ws / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8", errors="replace"))
            proj = data.get("project", {})
            deps["pyproject"] = list(proj.get("dependencies", [])) + [
                f"{k}[{','.join(v)}]" if isinstance(v, list) else str(k)
                for k, v in (proj.get("optional-dependencies") or {}).items()]
        except Exception as e:
            deps["pyproject"] = [f"(unparseable pyproject.toml: {e})"]
    imports: set[str] = set()
    for fp in _walk_text_files(ws, exts={".py"}):
        try:
            tree = ast.parse(fp.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imports.add(node.module.split(".")[0])
    deps["top_level_imports"] = sorted(imports)
    return json.dumps(deps, indent=2)


def _walk_text_files(root: Path, exts: set[str] | None = None):
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP]
        for name in files:
            if name.endswith((".pyc", ".pyo")):
                continue
            fp = Path(dirpath) / name
            if exts is not None:
                if fp.suffix in exts:
                    yield fp
            elif fp.suffix.lower() in _TEXT_EXT or fp.name in ("requirements.txt", ".env.example"):
                try:
                    if fp.stat().st_size <= 1_000_000:
                        yield fp
                except OSError:
                    continue
