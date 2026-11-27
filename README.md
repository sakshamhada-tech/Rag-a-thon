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
  `LLM_PROVIDER=groq` (Groq Cloud, free tier), `xai` (Grok), `anthropic` or `openai`; keys/models from `.env`.
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

### Use Groq Cloud (free tier)

Groq (`groq.com`) is **not** the same as xAI Grok. Its API is OpenAI-compatible.

```bash
# .env
LLM_PROVIDER=groq
GROQ_API_KEY=gsk-your-free-tier-key-here
GROQ_MODEL=openai/gpt-oss-120b   # llama-3.3-70b-versatile was retired for free/dev tiers on 2026-08-16
GROQ_BASE_URL=https://api.groq.com/openai/v1
# Optional: lower this if the free tier rate-limits you
MAX_TOOL_STEPS=8
```

If you're unsure which models your key can access:

```bash
python -m devmate.cli --list-models
```

Then run:

```bash
python -m devmate.cli --repo ./demo_repo --task "Find and fix the failing tests"
```

Free-tier notes: requests are rate-limited per minute; the client uses extra SDK retries, but sustained 429s will abort the run with `[ERROR] groq API error: ...`. If you see `404 - The model ... does not exist`, run `python -m devmate.cli --list-models` and set `GROQ_MODEL` to a visible tool-calling model. `llama-3.3-70b-versatile` / `llama-3.1-8b-instant` were shut down for free/developer tiers on 2026-08-16 ([Groq deprecations](https://console.groq.com/docs/deprecations)); Groq recommends `openai/gpt-oss-120b` or `openai/gpt-oss-20b`. `.env` is gitignored; never commit your key.

### Use Grok (xAI)

```bash
# .env
LLM_PROVIDER=xai
XAI_API_KEY=xai-your-real-key-here
XAI_MODEL=grok-4-latest        # or the exact Grok model enabled for your account
XAI_BASE_URL=https://api.x.ai/v1
```

Then run:

```bash
python -m devmate.cli --repo ./demo_repo --task "Find and fix the failing tests"
```

`.env` is gitignored; never commit your Grok key.

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
`LLM_PROVIDER=groq`, `xai`, `anthropic` or `openai` with a valid key.

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
