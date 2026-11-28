"""Phase 5/6: hybrid RAG v2 — AST chunking, embeddings, query assist, memory."""
import json
import math
import shutil
import tempfile
from pathlib import Path

import pytest

from fixora.embeddings import get_embedder, probe_groq_embeddings, cosine, HashEmbedder
from fixora.retrieval import HybridRetriever, build_failure_queries, chunk_python
from fixora.query_assist import rewrite_queries, rerank
from fixora.agents.common import RunContext
from fixora.events import EventEmitter
from fixora.llm import LLMError, LLMResponse, MockClient
from fixora.config import load_settings
from fixora.sandbox import Sandbox
from fixora import memory as fixora_memory

REPO_ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------ embeddings
def test_hash_embedder_is_deterministic_and_normalised():
    emb = HashEmbedder()
    a = emb.embed(["stock_level inventory error"])[0]
    b = emb.embed(["stock_level inventory error"])[0]
    assert a == b
    assert len(a) == 384
    assert math.isclose(math.sqrt(sum(v * v for v in a)), 1.0, abs_tol=1e-6)


def test_get_embedder_never_raises(monkeypatch):
    # Pin the offline embedder: the auto/fastembed path fetches a model over the
    # network on first use, which is covered manually by scripts/verify_rag.py.
    monkeypatch.setenv("FIXORA_EMBEDDER", "hash")
    emb, backend = get_embedder()
    assert backend in ("fastembed", "hash")
    vecs = emb.embed(["hello world"])
    assert vecs and len(vecs[0]) == len(emb.embed(["hello"][0])[0])


def test_probe_groq_embeddings_no_key():
    class S:  # minimal settings doppelganger
        llm_provider = "mock"
        groq_api_key = None
        groq_base_url = "https://api.groq.com/openai/v1"
    result = probe_groq_embeddings(S())
    assert result["available"] is False


# -------------------------------------------------------------- chunking
def test_ast_chunks_include_functions_classes_and_tests():
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / "repo"
        shutil.copytree(REPO_ROOT / "demo_repo_import", dst)
        chunks = chunk_python(dst / "meridian" / "catalog.py", "meridian/catalog.py")
        kinds = {(c["kind"], c["symbol"]) for c in chunks}
        assert any(k == "function" for k, _ in kinds)
        assert any(sym == "available" for _, sym in kinds)
        assert any(k == "module" for k, _ in kinds)
        tests = chunk_python(dst / "tests" / "test_catalog.py", "tests/test_catalog.py")
        assert any(c["kind"] == "test" for c in tests)


def test_syntax_error_file_falls_back_to_windows():
    with tempfile.TemporaryDirectory() as tmp:
        # self-contained: an unterminated f-string is a syntax error on every
        # supported Python (3.11 through 3.14+); do not depend on the demo fixture.
        target = Path(tmp) / "broken.py"
        target.write_text(
            '"""Module docstring."""' + "\n\n\n"
            + 'def format_total(total):\n'
            + '    return f"${total:.2f}\n', encoding="utf-8")
        chunks = chunk_python(target, "broken.py")
        assert chunks, "unparseable file must still be indexed via line windows"
        assert all(c["kind"] == "window" for c in chunks)


def test_hybrid_retrieval_locates_buggy_function():
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / "repo"
        shutil.copytree(REPO_ROOT / "demo_repo_import", dst)
        retr = HybridRetriever(dst)
        hits = retr.retrieve("stock_level inventory catalog", k=4)
        assert hits
        assert hits[0]["path"] == "meridian/catalog.py"


def test_failure_driven_queries_from_state():
    state = {"task": "Fix the failing tests",
             "last_test_result": {
                 "failing_tests": ["tests/test_a.py::test_round_half_up",
                                   "tests/test_a.py::test_tier_boundary"],
                 "diagnosis": {"summary": "Assertion failure", "exceptions": ["AssertionError"],
                               "module": None, "guidance": ""}}}
    queries = build_failure_queries(state)
    assert queries[0] == "Fix the failing tests"
    assert any("round half up" in q for q in queries)
    assert any("AssertionError" in q for q in queries)


# ----------------------------------------------------------- query assist
class _BoomLLM:
    provider_label = "boom"

    def chat(self, messages, tools, reasoning_effort=None):
        raise LLMError("429 rate limit exceeded")


class _GarbageLLM:
    provider_label = "garbage"

    def chat(self, messages, tools, reasoning_effort=None):
        return LLMResponse(text="this is not json at all", tool_calls=[])


class _OrderedLLM:
    provider_label = "ordered"

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools, reasoning_effort=None):
        self.calls += 1
        return LLMResponse(text='{"ranked_ids": ["c2", "c0", "c1"]}', tool_calls=[])


def _ctx(llm, printer=lambda *a, **k: None):
    sandbox = Sandbox(REPO_ROOT / "demo_repo_import")
    tools = {}
    settings = load_settings()
    return sandbox, RunContext(sandbox=sandbox, settings=settings, llm=llm,
                               tools=tools, printer=printer, emitter=EventEmitter())


def test_query_rewrite_falls_back_to_empty_on_llm_error():
    sandbox, ctx = _ctx(_BoomLLM())
    try:
        queries = rewrite_queries(ctx, {"task": "fix", "last_test_result": {
            "failing_tests": ["tests/t.py::test_x"], "diagnosis": {"summary": "x"}}})
        assert queries == []
    finally:
        sandbox.cleanup()


def test_rerank_falls_back_on_invalid_json():
    sandbox, ctx = _ctx(_GarbageLLM())
    try:
        candidates = [{"score": 1.0 - i * 0.1, "path": f"f{i}.py", "start_line": 1,
                       "end_line": 10, "text": f"chunk {i}", "symbol": f"s{i}"} for i in range(6)]
        ordered, applied = rerank(ctx, {"task": "fix"}, candidates, k=3)
        assert applied is False
        assert [c["path"] for c in ordered] == ["f0.py", "f1.py", "f2.py"]
    finally:
        sandbox.cleanup()


def test_rerank_reorders_with_valid_json():
    sandbox, ctx = _ctx(_OrderedLLM())
    try:
        candidates = [{"score": s, "path": f"f{i}.py", "start_line": 1, "end_line": 10,
                       "text": f"chunk {i}", "symbol": f"s{i}"}
                      for i, s in enumerate([0.9, 0.8, 0.7])]
        # scores are not dominant enough (0.9/0.8 < 3x) → rerank applies
        ordered, applied = rerank(ctx, {"task": "fix"}, candidates + [
            {"score": 0.6, "path": "f3.py", "start_line": 1, "end_line": 5,
             "text": "chunk 3", "symbol": "s3"}], k=3)
        assert applied is True
        assert [c["path"] for c in ordered] == ["f2.py", "f0.py", "f1.py"]
    finally:
        sandbox.cleanup()


def test_rerank_skips_when_top_hit_is_dominant():
    sandbox, ctx = _ctx(_OrderedLLM())
    try:
        candidates = [{"score": 3.0, "path": "winner.py", "start_line": 1, "end_line": 10,
                       "text": "winner", "symbol": "w"},
                      {"score": 0.5, "path": "loser.py", "start_line": 1, "end_line": 10,
                       "text": "loser", "symbol": "l"},
                      {"score": 0.4, "path": "loser2.py", "start_line": 1, "end_line": 10,
                       "text": "loser2", "symbol": "l2"}]
        ordered, applied = rerank(ctx, {"task": "fix"}, candidates, k=2)
        assert applied is False
        assert ordered[0]["path"] == "winner.py"
        assert ctx.llm.calls == 0
    finally:
        sandbox.cleanup()


# ---------------------------------------------------------------- memory
def test_memory_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("FIXORA_MEMORY", "1")
    monkeypatch.setenv("FIXORA_MEMORY_PATH", str(tmp_path / "memory.json"))
    report = {"status": "applied", "baseline_diagnosis": "Assertion failure",
              "tests_before": {"failing_tests": ["tests/t.py::test_round"]},
              "tests_after": {"passed": 14, "failed": 0},
              "edits_made": [{"path": "ledgerline/money.py", "tool": "apply_patch"}],
              "plan": "Round half up instead of half-even."}
    lesson = fixora_memory.save_run("demo_hard", "Fix the failing tests", report)
    assert lesson and lesson["repo"] == "demo_hard"
    hints = fixora_memory.relevant_hints(
        {"task": "Fix the failing tests",
         "last_test_result": {"failing_tests": ["tests/t.py::test_round"],
                              "diagnosis": {"summary": "Assertion failure"}}})
    assert hints and hints[0]["fix_summary"]
    block = fixora_memory.hints_block(hints)
    assert "Prior experience" in block and "NOT ground truth" in block


def test_memory_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("FIXORA_MEMORY", "0")
    monkeypatch.setenv("FIXORA_MEMORY_PATH", str(tmp_path / "memory.json"))
    assert fixora_memory.save_run("r", "t", {}) is None
    assert fixora_memory.relevant_hints({"task": "t"}) == []
