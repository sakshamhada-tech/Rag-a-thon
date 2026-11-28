"""Memory injection into the analyse phase + retrieval events emitted."""
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from fixora.agents import developer
from fixora.agents.common import RunContext
from fixora.events import EventEmitter
from fixora.llm import MockClient
from fixora.config import load_settings
from fixora.tools import build_tools
from fixora import memory as fixora_memory
from fixora.sandbox import Sandbox

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_memory_hints_injected_and_memory_event_emitted(tmp_path, monkeypatch):
    monkeypatch.setenv("FIXORA_MEMORY", "1")
    monkeypatch.setenv("FIXORA_MEMORY_PATH", str(tmp_path / "memory.json"))
    fixora_memory.save_run("meridian", "Fix the failing tests",
                           {"status": "applied", "baseline_diagnosis": "Import error",
                            "tests_before": {"failing_tests": ["tests/test_catalog.py::test_available_reports_stock"]},
                            "tests_after": {"passed": 4, "failed": 0},
                            "edits_made": [{"path": "meridian/catalog.py", "tool": "apply_patch"}],
                            "plan": "Import stock_count instead of stock_level."})

    sandbox = Sandbox(REPO_ROOT / "demo_repo_import")
    events = []
    emitter = EventEmitter()
    base_emit = emitter.emit
    def _spy(*a, **k):
        ev = base_emit(*a, **k)
        events.append(ev)
        return ev
    emitter.emit = _spy
    try:
        tools = build_tools(sandbox, load_settings())
        ctx = RunContext(sandbox=sandbox, settings=load_settings(), llm=MockClient(),
                         tools=tools, printer=lambda *a, **k: None, emitter=emitter)
        state = {"task": "Fix the failing tests",
                 "last_test_result": {"failing_tests": ["tests/test_catalog.py::test_available_reports_stock"],
                                      "diagnosis": {"summary": "Import error", "exceptions": ["ImportError"]}}}
        block = developer._memory_block(ctx, state)
        assert "Prior experience" in block
        assert any(ev.get("type") == "memory" and ev.get("applied") is True for ev in events)
    finally:
        sandbox.cleanup()


def test_retrieval_event_emitted_on_rag_block(tmp_path, monkeypatch):
    monkeypatch.setenv("FIXORA_RAG", "1")
    sandbox = Sandbox(REPO_ROOT / "demo_repo_import")
    events = []
    emitter = EventEmitter()
    base_emit = emitter.emit
    def _spy(*a, **k):
        ev = base_emit(*a, **k)
        events.append(ev)
        return ev
    emitter.emit = _spy
    try:
        tools = build_tools(sandbox, load_settings())
        ctx = RunContext(sandbox=sandbox, settings=load_settings(), llm=MockClient(),
                         tools=tools, printer=lambda *a, **k: None, emitter=emitter)
        state = {"task": "Fix the failing tests",
                 "last_test_result": {"failing_tests": ["tests/test_catalog.py::test_available_reports_stock"],
                                      "diagnosis": {"summary": "Import error",
                                                    "exceptions": ["ImportError"]}}}
        block = developer._rag_block(ctx, state, k=3)
        assert "Relevant code retrieved" in block
        assert any(ev.get("type") == "retrieval" and ev.get("chunks") for ev in events)
    finally:
        sandbox.cleanup()
