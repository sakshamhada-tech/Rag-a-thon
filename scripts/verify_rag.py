#!/usr/bin/env python3
"""One-command end-to-end check of Fixora's RAG + embeddings — fully offline.

Usage:
    python scripts/verify_rag.py

What it verifies (in order, printing PASS/FAIL per step):

  1. Embedder backend & vector shape (dims, L2 norm, determinism)
  2. Hybrid index build + stats on a temp copy of the import demo
  3. Hybrid retrieval locates the file containing the bug
  4. Failure-driven queries derive from the failing-test state
  5. Cross-run memory: save on run completion + cosine recall
  6. End-to-end agent run (LLM_PROVIDER=mock) reaches awaiting_approval,
     emits retrieval events with failure-driven queries, and writes a lesson
  7. Kill switch: FIXORA_RAG=0 suppresses retrieval entirely

The end-to-end legs copy the bundled demo fixture to a temp dir and
materialise the intentionally-broken file, so results do not depend on the
state of your working tree. Nothing in this script touches the checked-in
fixtures, and the original repo is never modified.

Exit code: 0 when every step passes, 1 otherwise.
"""
from __future__ import annotations

import math
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FIXTURE_SRC = REPO_ROOT / "demo_repo_import"
# The intentionally-broken content of demo_repo_import at HEAD; materialised
# into the temp copy so this script works even if your fixtures were edited.
_BROKEN_CATALOG_PY = (
    '"""Catalogue price and availability helpers."""\n'
    "from .inventory import stock_level\n"
    "\n"
    "\n"
    "def price(unit_price, quantity):\n"
    "    return round(unit_price * quantity, 2)\n"
    "\n"
    "\n"
    "def available(item):\n"
    "    return stock_level(item)\n"
)

RESULTS: list[tuple[str, bool, str]] = []


def step(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def fresh_fixture(tmp: Path) -> Path:
    dst = tmp / "repo"
    shutil.copytree(FIXTURE_SRC, dst,
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    (dst / "meridian" / "catalog.py").write_text(_BROKEN_CATALOG_PY, encoding="utf-8")
    return dst


def main() -> int:
    os.environ.setdefault("LLM_PROVIDER", "mock")
    os.environ["LLM_PROVIDERS"] = ""
    # Deterministic, inspectable memory for this script only.
    mem_dir = tempfile.mkdtemp(prefix="fixora-verify-memory-")
    os.environ["FIXORA_MEMORY_PATH"] = str(Path(mem_dir) / "memory.json")
    os.environ["FIXORA_MEMORY"] = "1"

    print("Fixora RAG + embeddings verification")
    print(f"repo: {REPO_ROOT}\n")

    with tempfile.TemporaryDirectory(prefix="fixora-verify-") as tmpd:
        tmp = Path(tmpd)
        demo = fresh_fixture(tmp)

        # ---------------- 1. embedder backend & vector shape
        from fixora.embeddings import get_embedder
        emb, backend = get_embedder()
        vecs = emb.embed(["stock_level inventory error"])
        v = vecs[0]
        norm = math.sqrt(sum(x * x for x in v)) if v else 0.0
        deterministic = emb.embed(["stock_level inventory error"])[0] == v
        ok = (len(v) == emb.dims and abs(norm - 1.0) < 1e-4 and deterministic)
        step("1. embedder backend + vector shape",
             ok, f"backend={backend} dims={len(v)} norm={norm:.4f} deterministic={deterministic}")

        # ---------------- 2. index build + stats
        from fixora.retrieval import HybridRetriever
        retr = HybridRetriever(demo)
        hits = retr.retrieve("ImportError stock_level inventory catalog", k=4)
        stats = retr.stats()
        ok = (stats.get("chunks", 0) > 0 and stats.get("files", 0) > 0
              and stats.get("build_ms", 0) > 0 and stats.get("backend") in ("hash", "fastembed"))
        step("2. hybrid index build + stats",
             ok,
             f"chunks={stats.get('chunks')} files={stats.get('files')} "
             f"backend={stats.get('backend')} build_ms={stats.get('build_ms')} "
             f"file_bytes={stats.get('file_bytes')}")

        # ---------------- 3. retrieval locates the buggy file
        paths = [h["path"] for h in hits]
        detail = " | ".join(f"{h['path']}:{h['start_line']}-{h['end_line']} {h['score']:.3f}"
                            for h in hits[:3])
        ok = any("catalog.py" in p for p in paths)
        step("3. retrieval finds meridian/catalog.py (contains the bug)",
             ok, detail)

        # ---------------- 4. failure-driven queries
        from fixora.retrieval import build_failure_queries
        state = {"task": "Fix the failing tests",
                 "last_test_result": {
                     "failing_tests": ["tests/test_catalog.py::test_available_reports_stock"],
                     "diagnosis": {"summary": "Import error",
                                   "exceptions": ["ImportError"], "module": "meridian.inventory"}}}
        queries = build_failure_queries(state)
        ok = len(queries) >= 2 and any("available reports stock" in q for q in queries) \
            and any("ImportError" in q for q in queries)
        step("4. failure-driven queries derived from failing tests + diagnosis",
             ok, f"queries={queries}")

        # ---------------- 5. cross-run memory: save + recall
        from fixora import memory
        saved = memory.save_run("verify_demo", "Fix the failing tests",
                                {"status": "applied", "baseline_diagnosis": "Import error",
                                 "tests_before": {"failing_tests": ["tests/test_catalog.py::test_available_reports_stock"]},
                                 "tests_after": {"passed": 4, "failed": 0},
                                 "edits_made": [{"path": "meridian/catalog.py", "tool": "apply_patch"}],
                                 "plan": "Import stock_count instead of stock_level."})
        hints = memory.relevant_hints(state)
        lessons = memory.load_lessons()
        ok = saved is not None and len(lessons) == 1 and bool(hints)
        step("5. cross-run memory write + embedder cosine recall",
             ok, f"lessons_saved={len(lessons)} hints_recalled={len(hints)}")

        # ---------------- 6. end-to-end offline agent run (fresh workspace)
        from fixora.runner import RunManager
        run_repo = fresh_fixture(tmp / "e2e")
        record = RunManager().run_blocking(str(run_repo), "Fix the failing tests",
                                           max_attempts=3, printer=lambda *a, **k: None)
        events = record.emitter.history if record.emitter else []
        retrieval = [e for e in events if e.get("type") == "retrieval"]
        ok = record.status == "awaiting_approval" \
            and len(retrieval) >= 1 \
            and bool(retrieval[0].get("queries")) \
            and retrieval[0].get("chunks_indexed", 0) > 0 \
            and retrieval[0].get("backend") in ("hash", "fastembed")
        step("6. end-to-end mock run: approval gate + retrieval events",
             ok,
             f"status={record.status} retrieval_events={len(retrieval)} "
             f"diagnosis={record.report.get('baseline_diagnosis')!r}")
        if retrieval:
            q = retrieval[0]["queries"]
            print(f"      first retrieval queries: {q}")
            print(f"      backend={retrieval[0]['backend']} "
                  f"chunks_indexed={retrieval[0]['chunks_indexed']} "
                  f"top_hit={retrieval[0]['chunks'][0]['path'] if retrieval[0].get('chunks') else 'n/a'}")

        # ---------------- 7. kill switch FIXORA_RAG=0
        os.environ["FIXORA_RAG"] = "0"
        try:
            kill_repo = fresh_fixture(tmp / "kill")
            record_off = RunManager().run_blocking(str(kill_repo), "Fix the failing tests",
                                                   max_attempts=3, printer=lambda *a, **k: None)
            events_off = record_off.emitter.history if record_off.emitter else []
            retrieval_off = [e for e in events_off if e.get("type") == "retrieval"]
            ok = len(retrieval_off) == 0
            ok = ok and record_off.status == "awaiting_approval"
            step("7. FIXORA_RAG=0 disables retrieval (no events; gate still reached)",
                 ok, f"status={record_off.status} retrieval_events={len(retrieval_off)}")
        finally:
            os.environ.pop("FIXORA_RAG", None)

    print()
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    if passed == total:
        print(f"ALL {total} CHECKS PASSED — RAG and embeddings are working locally.")
        print("Optional: curl -s localhost:8000/memory while the server runs, or watch")
        print("the Retrieval rows in the web UI timeline (LLM_PROVIDER=mock).")
        return 0
    print(f"{passed}/{total} checks passed — see FAIL lines above.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
