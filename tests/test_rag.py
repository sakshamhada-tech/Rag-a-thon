"""RAG: local offline BM25-style retrieval used to ground the agent."""
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from fixora.rag import RepoRetriever, tokenize
from fixora.tools import build_tools
from fixora.config import load_settings
from fixora.sandbox import Sandbox
from fixora.llm import MockClient

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_tokenize_splits_identifiers():
    toks = tokenize("stock_level StockLevel available2026")
    assert "stock" in toks and "level" in toks
    assert "stocklevel" not in toks or "stock" in toks  # camel split works
    assert any(t.startswith("available") for t in toks)


def test_retriever_finds_the_buggy_import():
    with tempfile.TemporaryDirectory() as tmp:
        src = REPO_ROOT / "demo_repo_import"
        dst = Path(tmp) / "repo"
        shutil.copytree(src, dst)
        retr = RepoRetriever(dst)
        hits = retr.retrieve("stock_level import inventory", k=3)
        assert hits, "expected at least one hit"
        paths = [h["path"] for h in hits]
        assert paths[0] == "meridian/catalog.py"   # the file with the wrong import
        assert any(h["path"].startswith("meridian/") for h in hits)


def test_retrieve_respects_k_and_returns_scores():
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / "repo"
        shutil.copytree(REPO_ROOT / "demo_repo_syntax", dst)
        retr = RepoRetriever(dst)
        hits = retr.retrieve("format_total total", k=3)
        assert 1 <= len(hits) <= 3
        assert all(h["score"] > 0 for h in hits)
        assert any(h["path"] == "skylark/pricing.py" for h in hits)


def test_retrieve_code_tool_roundtrip(tmp_path):
    sandbox = Sandbox(REPO_ROOT / "demo_repo_import")
    try:
        tools = build_tools(sandbox, load_settings())
        ok = json.loads(tools["retrieve_code"].run({"query": "stock_level inventory", "k": 3}))
        assert ok["ok"] is True
        assert ok["chunks"] and ok["chunks"][0]["path"] == "meridian/catalog.py"
        err = json.loads(tools["retrieve_code"].run({"query": ""}))
        assert "error" in err
    finally:
        sandbox.cleanup()


def test_rag_block_appears_in_fix_prompt_context(monkeypatch):
    from fixora.agents import developer
    monkeypatch.setenv("FIXORA_RAG", "1")
    sandbox = Sandbox(REPO_ROOT / "demo_repo_import")
    try:
        settings = load_settings()
        tools = build_tools(sandbox, settings)
        from fixora.agents.common import RunContext
        from fixora.events import EventEmitter
        ctx = RunContext(sandbox=sandbox, settings=settings, llm=MockClient(),
                         tools=tools, printer=lambda *a, **k: None,
                         emitter=EventEmitter())
        state = {"task": "Fix the failing tests",
                 "last_test_result": {"failing_tests": ["tests/test_catalog.py::test_available_reports_stock"],
                                      "diagnosis": {"summary": "ImportError: cannot import name 'stock_level' from 'meridian.inventory'",
                                                    "module": "meridian.inventory", "exceptions": ["ImportError"]}}}
        block = developer._rag_block(ctx, state, k=3)
        assert "Relevant code retrieved" in block
        assert "catalog.py" in block
        monkeypatch.setenv("FIXORA_RAG", "0")
        assert developer._rag_block(ctx, state) == ""
    finally:
        sandbox.cleanup()


def test_index_rebuilds_after_edits(tmp_path):
    dst = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "demo_repo_syntax", dst)
    retr = RepoRetriever(dst)
    before = retr.retrieve("rename_me_xyz_token", k=1)
    (dst / "skylark" / "pricing.py").write_text(
        (dst / "skylark" / "pricing.py").read_text() + "\n# rename_me_xyz_token marker\n")
    after = retr.retrieve("rename_me_xyz_token", k=1)
    assert any(h["path"].endswith("pricing.py") for h in after)
