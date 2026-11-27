# DevMate

**From Generative AI to Agentic AI — hackathon project, Phase 1.**
DevMate is an autonomous coding agent that takes a Python repo plus a task,
analyses the repo, finds the bug, patches it, runs the tests, retries with the
failing output in context, and reports the result — from the command line.

Small and working beats big and half-working: this phase is CLI-only, no web UI,
no vector DB, no embeddings.

## How it works

```
baseline test → analyse → plan → fix ⇄ test → report
                          (retry with failing output, up to MAX_ATTEMPTS)
```

- **Sandbox**: the target repo is copied to a fresh temp workspace; every read,
  edit and test run happens only there. The original is never modified.
  LLM-supplied paths are resolved and checked to stay inside the workspace
  (blocks `..`, absolute paths, symlink escapes). Commands are allowlisted
  (`python -m pytest|py_compile|unittest`), run without a shell, with a
  timeout and truncated output.
- **Tools**: `list_tree`, `read_file`, `search_code`, `get_dependencies`,
  `write_file`, `apply_patch` (exact match, must be unique), `run_tests`
  (real pytest output — never the LLM's claim).
- **LLM**: provider-agnostic wrapper with native tool calling.
  `LLM_PROVIDER=anthropic` or `openai`, keys/models from `.env`.
- The full run (steps, tool calls, results) is logged to `logs/<timestamp>.json`.

## Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit .env: set LLM_PROVIDER + your API key
```

## Run

```bash
python -m devmate.cli --repo ./demo_repo --task "Find and fix the failing tests"
```

Exit code is `0` if the tests pass at the end, else `1`.

### Example output (abridged, offline smoke run)

```
[START] provider=mock repo=./demo_repo workspace=/tmp/devmate-workspace-... max_attempts=3
[TEST] baseline run (before any edits)
[TEST] status=failed passed=11 failed=3 errors=0 failing=tests/test_discounts.py::test_discount_at_ten_items_boundary, ...
[TOOL CALL list_tree(path=., max_depth=3)]
[ANALYSE] ...
[PLAN] Suspected root cause: ...
[TOOL CALL apply_patch(path=swiftcart/money.py, ...)]
[TEST] status=passed passed=14 failed=0 errors=0
[REPORT] { "status": "success", "attempts": 1, ... }
[DONE] exit=0
```

Note: the run above used `LLM_PROVIDER=mock`, a deterministic offline
provider that scripts read → locate → patch with **no LLM involved**. It exists
only to smoke-test the pipeline (sandbox, tools, graph routing, logs, exit
codes) on machines without API access. Real autonomous behaviour requires
`LLM_PROVIDER=anthropic` or `openai` with a valid key.

## Verify

```bash
cd demo_repo && python -m pytest   # baseline: 3 failed, 11 passed (planted bug)
cd .. && python -m pytest tests/   # unit tests: sandbox escapes, apply_patch, decide(), parsers
```

## Repository layout

- `devmate/` — the agent (`config`, `llm`, `sandbox`, `tools/`, `agents/`, `cli.py`)
- `demo_repo/` — swiftcart, a demo shopping-cart/discount project with a planted bug
- `tests/` — unit tests for the sandbox, tools and graph routing
- `logs/` — JSON run logs (gitignored)
- `SPEC.md` — architecture note + phase status

## What is deliberately out of scope (Phase 1)

Web UI, FastAPI server, reviewer agent, GitHub cloning, vector search, Docker,
deployment, human-approval flow. See `SPEC.md`.
