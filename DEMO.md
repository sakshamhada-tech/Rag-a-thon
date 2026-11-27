# Fixora — Demo Guide

Offline, deterministic, no keys required. Everything below runs with
`LLM_PROVIDER=mock`, which is **not an LLM** — it exists to prove the plumbing
(analysis → retrieval → fix loop → tests → review → approval gate) end-to-end.
For real autonomous behaviour, set `LLM_PROVIDER=groq` with `GROQ_API_KEY`.

## 3-minute demo script

1. **Show the fixture** (30 s):
   `sed -n '1,20p' demo_repo_hard/ledgerline/money.py` — banker's rounding,
   no comments. Point out `demo_repo_hard` has two related bugs.
2. **Run the CLI** (60 s):
   ```bash
   source .venv/bin/activate
   cp -r demo_repo_hard /tmp/ledgerline-demo
   LLM_PROVIDER=mock python -m fixora.cli --repo /tmp/ledgerline-demo --task "Fix the failing tests"
   ```
   Watch for: `[DIAGNOSIS] assertion_failure`, `[RAG]`/`retrieval` lines with
   queries, attempt counter, first retry (tier bug), reviewer JSON, final
   `[AWAITING APPROVAL]`.
3. **Prove the gate** (30 s): `ls /tmp/ledgerline-demo` — the original file is
   unchanged; then `git diff` in the fixture — clean. Explain: passing tests +
   reviewer approval still does NOT write anything.
4. **Approve** (20 s): re-run with `--yes`; show `[APPROVED] applied to ...`
   and `pytest` green (14 passed).
5. **Web UI** (60 s): `npm --prefix frontend run build && LLM_PROVIDER=mock .venv/bin/uvicorn fixora.api:app --host 127.0.0.1 --port 8000`,
   open the UI, run `demo_repo_hard`, expand the **retrieval** and **memory**
   rows in the timeline, show the approval panel.
6. **Show memory** (20 s): after run #1, point the same task at a fresh copy of
   the fixture and show the injected lesson (`curl localhost:8000/memory`).

## Expected baseline behaviour (all verified in this sandbox; public repos via mock)

| Fixture | Baseline | Mock outcome | Attempts (avg) |
|---|---|---|---|
| `demo_repo` (swiftcart) | 3 failed / 11 passed | `awaiting_approval` | 1 |
| `demo_repo_hard` (ledgerline) | 5 failed / 9 passed | `awaiting_approval` | 2 (retry for tier bug) |
| `demo_repo_syntax` (skylark) | collection blocked (SyntaxError) | `awaiting_approval` | 1 |
| `demo_repo_import` (meridian) | collection blocked (ImportError) | `awaiting_approval` | 2 |
| `github.com/more-itertools/more-itertools` | 772 passed (public clone) | `awaiting_approval`, no edits (baseline green) | 1 |
| `github.com/tqdm/tqdm` | collection blocked by pytest plugin gap (stock env) | new diagnosis: `dependency_missing` naming `pytest-asyncio`; no edits; gate held | 3 |

Fixture copies are mandatory: without `--yes` nothing is modified anywhere;
with `--yes`, use a copy (`cp -r`) — the checked-in demos are read-only props.

## Verification commands

```bash
python -m pytest tests/          # 150 tests
npm --prefix frontend run build  # type-check + production bundle
LLM_PROVIDER=mock python -m fixora.cli --repo ./demo_repo --task "Fix the failing tests"
curl -s localhost:8000/health    # {"ok": true}
curl -s localhost:8000/memory    # {"count": N, "lessons": [...]}
```

## What NOT to claim in the demo

- Do not say the mock "solved" anything autonomously — it demonstrates the
  pipeline, not model capability. (Real Groq runs exist historically; see
  SPEC.md "Verified evidence".)
- Do not claim neural retrieval quality with the default embedder — say
  "hybrid BM25 + local vectors, with an optional fastembed backend".
- Do not claim Docker was built in this environment — it is unavailable here;
  `docker build -t fixora .` is the deliverable for an environment with Docker.
