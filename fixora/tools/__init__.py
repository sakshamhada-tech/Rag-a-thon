"""Tool registry: JSON schemas for the LLM plus a safe executor.

Every tool returns a JSON string; errors are payloads ({"error": ...}),
never raised exceptions. Arguments are validated against the declared schema —
bad args come back as a clear {"error": ...} payload so the model can correct
itself instead of crashing the run.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from . import repo_tools, edit_tools, test_tools, dependency_tools, rag_tools

_PY_TYPES = {"string": str, "integer": int, "number": (int, float),
             "boolean": bool, "object": dict, "array": list}


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[[dict], str]

    def schema(self) -> dict:
        return {"name": self.name, "description": self.description,
                "parameters": self.parameters}

    def run(self, args) -> str:
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return json.dumps({"error": f"{self.name}: arguments must be a JSON object, "
                                        f"got {type(args).__name__}"})
        error = self.validate(args)
        if error:
            return json.dumps({"error": error})
        try:
            return self.fn(args)
        except Exception as e:  # belt and braces: tools should not raise
            return json.dumps({"error": f"{self.name} failed: {e}"})

    def validate(self, args: dict) -> str | None:
        """Type-check declared params; extras (aliases) are allowed and ignored."""
        props = self.parameters.get("properties", {})
        for key, spec in props.items():
            if key not in args:
                continue
            value = args[key]
            expected = _PY_TYPES.get(spec.get("type"))
            if expected is None:
                continue
            if spec["type"] == "integer" and isinstance(value, bool):
                return f"{self.name}: argument '{key}' must be an integer, got boolean"
            if not isinstance(value, expected):
                return (f"{self.name}: argument '{key}' must be of type {spec['type']}, "
                        f"got {type(value).__name__}")
        for key in self.parameters.get("required", []):
            if key not in args or args[key] is None:
                return f"{self.name}: missing required argument '{key}'"
        return None


def _alias(args: dict, *names, default=None):
    for name in names:
        if name in args and args[name] is not None:
            return args[name]
    return default


def build_tools(sandbox, settings) -> dict[str, Tool]:
    """Bind all Phase 1 tools to a sandbox instance."""
    tools = [
        Tool("list_tree",
             "List the file tree of the repo (or a subdirectory).",
             {"type": "object", "properties": {
                 "path": {"type": "string", "description": "Relative dir, default '.'"},
                 "max_depth": {"type": "integer", "description": "Tree depth, default 3, max 6"}},
              "required": []},
             lambda a: repo_tools.list_tree(sandbox, a.get("path") or ".", a.get("max_depth") or 3)),
        Tool("read_file",
             "Read a file with 1-based line numbers. Optional start_line/end_line.",
             {"type": "object", "properties": {
                 "path": {"type": "string"},
                 "start_line": {"type": "integer"},
                 "end_line": {"type": "integer"}},
              "required": ["path"]},
             # aliases tolerated for robustness: line_start/start, line_end/end
             lambda a: repo_tools.read_file(sandbox, a["path"],
                                            _alias(a, "start_line", "line_start", "start"),
                                            _alias(a, "end_line", "line_end", "end"))),
        Tool("search_code",
             "Regex search across repo text files. Returns 'path:line: snippet', capped.",
             {"type": "object", "properties": {
                 "pattern": {"type": "string", "description": "Python regex"},
                 "path": {"type": "string", "description": "Relative dir/file, default '.'"}},
              "required": ["pattern"]},
             lambda a: repo_tools.search_code(sandbox, a["pattern"], a.get("path") or ".")),
        Tool("get_dependencies",
             "Parse requirements.txt / pyproject.toml and collect top-level imports.",
             {"type": "object", "properties": {}, "required": []},
             lambda a: repo_tools.get_dependencies(sandbox)),
        Tool("write_file",
             "Write a new file or fully overwrite an existing one (relative path).",
             {"type": "object", "properties": {
                 "path": {"type": "string"},
                 "content": {"type": "string"}},
              "required": ["path", "content"]},
             lambda a: edit_tools.write_file(sandbox, a["path"], a["content"])),
        Tool("apply_patch",
             "Exact-match replace in a file. Fails unless old_str occurs exactly once.",
             {"type": "object", "properties": {
                 "path": {"type": "string"},
                 "old_str": {"type": "string"},
                 "new_str": {"type": "string"}},
              "required": ["path", "old_str", "new_str"]},
             lambda a: edit_tools.apply_patch(sandbox, a["path"], a["old_str"], a["new_str"])),
        Tool("run_tests",
             "Run pytest in the workspace. Returns pass/fail counts, failing test names, a failure "
             "diagnosis (syntax_error, dependency_missing, import_error, assertion_failure, ...) and trimmed output.",
             {"type": "object", "properties": {
                 "test_path": {"type": "string", "description": "Optional path, e.g. tests/test_cart.py"}},
              "required": []},
             lambda a: test_tools.run_tests(sandbox, settings, a.get("test_path") or None)),
        Tool("search_semantic",
             "Hybrid code retrieval (BM25 + local vector similarity, RRF-fused) over AST chunks "
             "(functions, classes, tests, module top-level). Give a natural-language query "
             "(symbols, behaviours, error text) and get the most relevant chunks with path, symbol, "
             "line range and score. Use to LOCATE code; search_code for exact strings; read_file for "
             "full context. Never re-read files already in context.",
             {"type": "object", "properties": {
                 "query": {"type": "string", "description": "What to look for"},
                 "k": {"type": "integer", "description": "Number of chunks (default 5, max 10)"}},
              "required": ["query"]},
             lambda a: rag_tools.search_semantic(sandbox, a["query"], a.get("k") or 5)),
        Tool("retrieve_code",
             "Semantic-ish lexical (BM25) retrieval over the repo. Give it a natural-language query "
             "(e.g. 'stock_level inventory import error') and it returns the most relevant code chunks "
             "with paths and line ranges. Cheaper than reading whole files blindly — use it first when "
             "exploring a large repo or when unsure where something lives.",
             {"type": "object", "properties": {
                 "query": {"type": "string", "description": "What to look for"},
                 "k": {"type": "integer", "description": "Number of chunks (default 5, max 10)"}},
              "required": ["query"]},
             lambda a: rag_tools.retrieve_code(sandbox, a["query"], a.get("k") or 5)),
        Tool("install_dependencies",
             "Install the repository's OWN declared dependencies (requirements.txt, or pyproject.toml "
             "[project] dependencies) into an isolated temp directory; pytest then sees them via PYTHONPATH. "
             "Takes no arguments — the model cannot choose packages. Use when the diagnosis is dependency_missing. "
             "Returns ok/error with the pip tail. Never touches the host interpreter or the repo.",
             {"type": "object", "properties": {}, "required": []},
             lambda a: dependency_tools.install_dependencies(sandbox, settings)),
    ]
    return {t.name: t for t in tools}
