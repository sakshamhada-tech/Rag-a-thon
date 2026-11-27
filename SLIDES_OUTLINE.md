# Fixora — Slides Outline

Suggested 12-slide hackathon deck (10–12 minutes + 3-minute live demo).

1. **Title** — Fixora: an approval-gated autonomous bug-fixer for Python repos.
   One-liner: "It reads the repo, fixes the bug, proves it with tests, and
   waits for a human before touching your code."
2. **The problem** — LLMs patch where they *think* the bug is; they trust their
   own claims; they can silently loosen tests; junior devs lose hours to
   localisation before fixing.
3. **The idea** — agent loop + hard gates: sandbox copy → real tests →
   retrieval-grounded fix → separate Reviewer agent → **human approval** →
   diff/patch. Never the model's word for anything.
4. **Architecture diagram** — LangGraph loop (baseline→analyse→plan→fix→test→
   review→report) + approval gate. (Mermaid source: ARCHITECTURE.md)
5. **RAG is real, and free** — AST-aware chunks (function/class/method/test +
   module scope), hybrid BM25 **and** vector cosine merged by Reciprocal Rank
   Fusion, failure-driven queries, optional Groq query-rewrite/rerank with
   graceful degradation; `retrieve_code` tool in every phase; retrieval and its
   evidence rendered live in the UI.
6. **Memory, twice** — in-run state (graph) + cross-run lessons
   (`logs/memory.json`, embedder cosine recall, labelled "hints, not ground
   truth"), shown live: second run starts with the first run's lesson.
7. **Safety & trust** — sandboxed copy, path confinement, allowlisted
   commands, declared-deps-only install with size/time limits, secret
   redaction, `0600` logs, GitHub URL strict validation, one run at a time.
8. **The approval gate** — screenshot: `awaiting_approval` + diff + approve/
   reject; explain reject→nothing happens; GitHub runs export a patch.
9. **Tech stack** (one dense slide; everything by name) — Groq
   `openai/gpt-oss-120b` (generation; Groq has no embeddings endpoint),
   LangGraph, LangChain-free custom loop pieces, FastAPI + SSE, React 18 +
   Vite + TypeScript, rank_bm25, local hashed 384-dim embedder (optional
   BAAI/bge-small-en-v1.5 via fastembed), pytest, Docker. AI coding tools:
   Arena.ai Agent Mode alongside the owner.
10. **Results** — offline mock matrix: 4 fixtures → all `awaiting_approval`,
    two-bug retry demonstrated (`demo_repo_hard`); 149 tests green; live UI
    with retrieval/memory events. Honest caveats in the next slide.
11. **Limitations & what we would do next** — lexical default embeddings;
    retrieval ranking can surface module chunks over the exact buggy symbol;
    no PR creation; flat-file memory; Docker image written but not built in the
    maintainer sandbox; real-provider runs are possible but were not executed
    in the build environment (no keys); designed for small/medium Python repos.
12. **Team & ask** — Saksham Hada (owner), built with Arena.ai Agent Mode;
    repo link; one-line ask (stars, trial against your own repo, feedback on
    the retrieval/memory design).

**Live demo slot** — follow DEMO.md (3 minutes, offline mock, zero risk to
checked-in fixtures: use `cp -r` copies).
