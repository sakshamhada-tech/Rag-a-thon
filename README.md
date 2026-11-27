# Fixora

**From Generative AI to Agentic AI — hackathon project, Phase 4.**
Fixora is an autonomous coding agent that takes a Python repo plus a task,
analyses the repo, finds the bug, patches it, runs the real test suite, gets a
second opinion from a Reviewer agent, and reports the result — from the CLI or
from a small FastAPI server with live events.

Small and working beats big and half-working: there is still no vector DB, no
embeddings and no PR creation. Phase 4 adds a failure taxonomy, dependency
installation and two more demos on top of the Phase 2 agent loop and the
Phase 3 web UI.

## How it works

```
baseline test → analyse → plan → fix ⇄ test
                                   ↓ pass
                                 review → approve → report (awaiting approval)
                                   ↓ reject and attempts remain
                                  back to fix with reviewer issues in context
```

Passing tests are **not** enough: a passing run stops at `report` with status
`awaiting_approval`. Nothing is written to the original repo until a human
approves (CLI `--yes`, or `POST /runs/{id}/approve`). Rejecting ends the run as
`rejected`. For GitHub clones, approval writes a patch file instead of pushing
anywhere.

- **Sandbox**: the target repo is copied to a fresh temp workspace; every read,
  edit and test run happens only there. The original is never modified before
  approval. LLM-supplied paths are resolved and checked to stay inside the
  workspace (blocks `..`, absolute paths, symlink escapes). Commands are
  allowlisted (`python -m pytest|py_compile|unittest`), run without a shell,
  with a timeout and truncated output.
- **Tools**: `list_tree`, `read_file`, `search_code`, `get_dependencies`,
  `write_file`, `apply_patch` (exact match, must be unique), `run_tests`
  (real pytest output — never the LLM's claim). Identical tool calls (same
  tool + args) in one run are served from a cache and marked `[CACHED]`; any
  successful mutation invalidates the cache. Tool-argument errors are returned
  as `{"error": ...}` data so the model can correct itself.
- **Reviewer**: a separate LangGraph node runs after a passing test run with the
  task, plan, unified diff and test results. It has read-only tools; if your
  provider rejects that tool-enabled pass because the model emits pseudo-tools,
  Fixora retries the verdict with no tools exposed (still never auto-approves
  without a human). Otherwise it
  must emit strict JSON (`verdict`, `issues`, `risk`, `summary`). A rejection
  routes back to `fix` with reviewer issues in context and counts toward
  `MAX_ATTEMPTS`. A hard code rule flags diffs under `tests/` and forces
  rejection when assertions or test functions were removed/loosened.
- **LLM**: provider-agnostic wrapper with native tool calling.
  `LLM_PROVIDER=groq` (Groq Cloud, free tier), `xai` (Grok), `anthropic` or
  `openai`; keys/models from `.env`. `LLM_PROVIDERS` enables a fallback chain
  (see below).
- **Server**: FastAPI + SSE (see `fixora/api.py`), one run at a time, run
  timeout, in-memory state plus JSON log on disk (`logs/`, gitignored).

## Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit .env: set LLM_PROVIDER + your API key
```

## CLI

```bash
python -m fixora.cli --repo ./demo_repo --task "Find and fix the failing tests"
```

The run stops at `awaiting_approval` after tests pass and the Reviewer
approves. The original repo is untouched until:

```bash
python -m fixora.cli --repo ./demo_repo --task "Find and fix the failing tests" --yes
```

`--yes` applies the reviewed diff to a local repo, or writes a patch file for
GitHub runs. Rejecting from the API marks the run `rejected` and never writes.

Exit codes: `0` for `awaiting_approval`/`applied`, `1` for `failed`,
`2` for `error`/`timed_out`, `429` if the server is busy.

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
python -m fixora.cli --list-models
```

Free-tier notes: requests are rate-limited per minute; sustained 429s will
switch to the next provider in `LLM_PROVIDERS` (if set) or abort the run with an
error. `.env` is gitignored; never commit your key.

### LLM fallback chain

Set `LLM_PROVIDERS` in `.env` to try providers in order on
429/timeout/5xx/connection errors, with exponential backoff and a
`[LLM FALLBACK]` log line:

```bash
# bare names (reuse per-provider settings):
LLM_PROVIDERS=groq,openai

# explicit label|base_url|key|model entries also work:
LLM_PROVIDERS=groq|https://api.groq.com/openai/v1|gsk-key|openai/gpt-oss-120b,openai|https://api.openai.com/v1|sk-key|gpt-4o-mini
```

`reasoning_effort` is selected dynamically per operation: `low` for simple
tasks, `medium` for moderate ones, and `high` for complex work or after failed
retries (based on task wording, failing-test count, retry number and diff
size). Providers such as gpt-oss / o-/gpt-5 / grok-4 / qwen3 receive the hint;
others ignore it. `max_tokens` stays capped at 4096.

### Use Grok (xAI)

```bash
# .env
LLM_PROVIDER=xai
XAI_API_KEY=xai-your-real-key-here
XAI_MODEL=grok-4-latest        # or the exact Grok model enabled for your account
XAI_BASE_URL=https://api.x.ai/v1
```

`.env` is gitignored; never commit your Grok key.

Config safety: `LLM_PROVIDERS` entries are printed as labels only (for example
`groq1,groq2`); API keys are never printed at startup. Put all explicit entries
on one physical `.env` line with a non-empty model.

## Web UI

The repository now includes a React + TypeScript + Vite frontend in `frontend/`.
Tech: React 18, TypeScript, plain CSS with design tokens, self-hosted IBM Plex
fonts via Fontsource, lucide-react icons, native fetch + EventSource. No Tailwind,
no component library.

### Build and run (one process)

```bash
cd frontend
npm install
npm run build
cd ..
LLM_PROVIDER=mock .venv/bin/python -m uvicorn fixora.api:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/`. FastAPI serves `frontend/dist` and the API from the
same origin. `/health`, `/config`, `/demos`, `/runs` and `/runs/{id}/events` back
the UI. Built-in demos appear in the left panel; GitHub runs still accept only
public repositories. Demo runs are copied to a temp directory and can only export
a patch, so approving a demo never mutates the checked-in fixtures.

### Frontend development

```bash
LLM_PROVIDER=mock .venv/bin/python -m uvicorn fixora.api:app --host 127.0.0.1 --port 8000
cd frontend
npm run dev   # Vite on 127.0.0.1:5173, proxying /runs, /demos, /config, /health
```

Set `VITE_API_PROXY_TARGET` from a shell if the API is not on port 8000.

### UI environment and controls

- `FIXORA_API_TOKEN` — optional API bearer token. The static UI shell is public;
  API routes still require `Authorization: Bearer ...` when this is set. For local
  use, leave it unset or put the UI behind an authenticating proxy.
- `CORS_ORIGINS` — comma-separated allowed origins for Vite/dev servers. Defaults
  to localhost:3000 and localhost:5173.
- `PORT` — used by the Docker image (`uvicorn ... --port $PORT`).
- Theme initialisation happens in `frontend/index.html` before first paint; stored
  choice is read from `localStorage` with a `prefers-color-scheme` fallback.

### Key UI behaviour

- Fixed-height app shell (`100dvh`) with internal scroll regions for setup/history,
  timeline and inspector; no page-level growth when panels are full. Compact footer
  reports product phase and the approval gate.
- Desktop regions are resizable: hairline separators between setup/timeline and
  timeline/inspector support pointer drag (keyboard: arrow keys, `Shift` for larger
  steps). No gap between panels; widths persist in `localStorage`. Below 1180px the
  inspector becomes a slide-over and the right separator hides; below 760px the
  bottom tabs take over and separators hide.
- SSE replay/live stream uses event sequence ids and `Last-Event-ID`; the reducer
  dedupes by `seq`.
- Diff is rendered by a custom unified-diff parser; tests/review/log tabs show
  real run data only.
- Approval remains consequential: confirmation step, disabled while in flight,
  patch download for demo/GitHub runs, local apply only for approved local runs.

### Docker

```bash
docker build -t fixora .
docker run --rm -p 8000:8000 -e LLM_PROVIDER=mock fixora
```

The image builds `frontend/dist` in a Node stage, copies it into a slim Python
runtime with git, and serves UI + API on `${PORT:-8000}`. It does not include a
real `.env`; pass provider keys with `-e` only if you understand the risk.

## Security

- `.env`, `.env.*` (except `.env.example`), private keys and common credential
  files are gitignored and are **not copied into the agent sandbox**; tool calls
  to read/write them are rejected.
- Startup logs show provider labels only (`groq1,groq2`), never API keys.
  Known token patterns and exact values loaded from the environment are redacted
  from printed output, events and command output.
- Run logs/patches are written with owner-only permissions (`0600`; log dir
  `0700`). Review them before sharing — they may contain source code.
- Never paste real keys into chat, issues, commits or screenshots. Once exposed,
  rotate/revoke them.
- The API should bind to `127.0.0.1` locally. If you expose it, set
  `FIXORA_API_TOKEN` and send `Authorization: Bearer ...`; CORS defaults to
  localhost origins (`CORS_ORIGINS` to override), never wildcard credentials.

## FastAPI server

Open `http://127.0.0.1:8000/` in a browser (or use curl). The server is a JSON
API, not a finished frontend: `/` shows the available endpoints, `/health` returns
`{"ok": true}`, and `/docs` shows the interactive API docs. To start a run,
use `POST /runs` with a public GitHub URL.

```bash
source .venv/bin/activate
LLM_PROVIDER=groq python -m uvicorn fixora.api:app --host 127.0.0.1 --port 8000

# if binding 0.0.0.0, require a bearer token:
FIXORA_API_TOKEN=choose-a-long-random-token   python -m uvicorn fixora.api:app --host 0.0.0.0 --port 8000
```

The API accepts **public GitHub URLs only** (`https://github.com/<owner>/<repo>`),
validated with a strict regex. Local paths, SSH URLs, `file://` URLs and other
hosts are rejected with `400`. Public repos are shallow-cloned (`--depth 1`) into
a sandbox with a clone timeout and a size cap (`CLONE_TIMEOUT`, `MAX_REPO_MB`).

```bash
# start a run
run_id=$(curl -s -X POST http://127.0.0.1:8000/runs \
  -H 'content-type: application/json' \
  -d '{"repo":"https://github.com/OWNER/REPO","task":"Fix the failing tests"}' | jq -r .run_id)

# stream events (SSE): step, agent/tool_call, tool_result, test_result,
# review_verdict, retry, awaiting_approval, done
curl -N http://127.0.0.1:8000/runs/$run_id/events

# current state: diff, tests before/after, review, timings
curl -s http://127.0.0.1:8000/runs/$run_id | jq

# consequential action — only after the run reaches awaiting_approval
curl -X POST http://127.0.0.1:8000/runs/$run_id/approve   # writes logs/<id>.patch for GitHub runs
curl -X POST http://127.0.0.1:8000/runs/$run_id/reject    # marks the run rejected

# download the patch after approving a GitHub run
curl -OJ http://127.0.0.1:8000/runs/$run_id/patch
```

One run at a time per server (`429` while another is active/awaiting), plus an
overall `RUN_TIMEOUT` watchdog. CORS is restricted to localhost origins by default. Run state is in memory with a
JSON log in `logs/`.

## Demo repos

- `demo_repo/` ("swiftcart") — one planted rounding bug; 3 failing tests at baseline.
- `demo_repo_hard/` ("ledgerline") — two related bugs across two modules:
  banker's rounding in `ledgerline/money.py` **and** an off-by-one bulk-discount
  tier in `ledgerline/discounts.py`. At baseline: 9 passed, 5 failed. Fixing only
  the obvious rounding bug leaves tier tests failing; the retry loop must fix
  the second bug. 14 tests total once green.
- `demo_repo_syntax/` ("skylark") — a syntax error (unterminated f-string) blocks
  import and test collection; the suite cannot run at all at baseline. Fixing the
  syntax restores the suite.
- `demo_repo_import/` ("meridian") — an import of the wrong name from an internal
  module (`stock_level` vs `stock_count`) blocks collection; the fix needs two
  edits (import line, then the call site), exercising the retry loop.

## Speed / latency notes

Local timings for the offline mock on this fixture tree: full `demo_repo_import`
run (Analyse → Plan → Fix → re-test → Review) ≈ 3 s, of which ≈ 2 pytest
subprocesses are ≈ 0.7 s each. On a real provider, wall-clock is dominated by the
8–12 LLM round-trips, each carrying reasoning tokens. Levers, in order of impact:

1. **Model choice** (`GROQ_MODEL`) — a strong but faster model beats a bigger one;
   e.g. `openai/gpt-oss-20b` vs `openai/gpt-oss-120b`. Verify it parses tool calls.
2. **`FIXORA_REASONING_EFFORT=low`** — bypasses the complexity heuristic for every
   call (the heuristic already returns `low` for simple tasks; this forces it even
   when the first test run fails with errors).
3. **`--max-attempts 1`** — bounded, but gives up on the first failing iteration.
4. **`FIXORA_PROVIDER_COOLDOWN`** — already skips dead keys instead of paying
   2 failed attempts + a 1 s sleep per call.
5. Endpoint stability (`base_url` latency, provider region) and output verbosity
   of the model.

There is no safe "skip tests" mode: pytest is the pass/fail signal and runs
always (baseline + after each fix). Parallelising tool execution is not
implemented; ordering matters for edits.

## RAG (retrieval-augmented generation)

Fixora now grounds every fix prompt in *retrieval*, not just the file the agent
happens to read first. This is **lexical RAG, local and free**:

- A BM25-style inverted index is built over line-window chunks of the target
  repo (skips `.git`, venvs, caches and sensitive paths), stdlib-only, no
  embeddings, no network, no API keys, no extra dependencies, no per-token cost.
  It cannot rate-limit or go down; it is rebuilt automatically whenever the repo
  changes.
- At analyse, plan and fix time, a block of the most relevant chunks (path,
  line ranges, BM25 scores) is injected into the prompt. The same retriever is
  exposed to the agent as the `retrieve_code(query, k)` tool for deeper lookups.
- The number of retrieved chunks is capped (analyse: 4, plan/fix: 5) and every
  retrieval is logged as a `rag` step in the run timeline/logs.
- Disable with `FIXORA_RAG=0`.

Honest limits: this is term-overlap retrieval, not neural semantic search. It is
deterministic, free and genuinely retrieval-augmented (generation is fed by what
the index returns), but it does not capture meaning beyond shared vocabulary
(identifiers, test names, error messages — which is most of what bug fixing uses).
Upgrading to neural embeddings later means swapping `fixora/rag.py`'s scoring
function; prompts, tools and events stay the same.

## Failure taxonomy and dependency errors

Every test run is classified from real pytest output before the agent is asked
to fix anything (`fixora/diagnostics.py`). The category, label, summary, files,
module and suggested guidance are injected into the fix prompt, logged to the
run report (`baseline_diagnosis` / `diagnosis`), emitted as a `diagnosis` event
for the timeline, and shown on the Inspector's Log tab via raw JSON.

Categories: `syntax_error`, `dependency_missing`, `import_error`,
`collection_error`, `no_tests`, `assertion_failure`, `runtime_error`,
`timeout`, `unknown`.

Dependency problems are first-class:

- `dependency_missing` (ModuleNotFoundError) points the fix agent at the
  `install_dependencies` tool, which installs **only the repo's own declared
  dependencies** — `requirements.txt` at the root, or `[project] dependencies`
  from `pyproject.toml` (name/extras/version constraints only; URLs, markers
  and direct references are rejected). The LLM cannot name packages.
- Packages are installed into `<sandbox-root>/fixora-deps` — a temp directory
  next to the copied repo, **never** the host interpreter's environment and
  never inside the repo copy, so they cannot leak into the diff. pytest then
  imports them via `PYTHONPATH`. The temp dir is deleted with the run.
- Disable with `FIXORA_ALLOW_DEPENDENCY_INSTALL=0` (see `.env.example`).
- If the package is not declared anywhere, the agent is told to either fix the
  import (typo/internal module) or add it to `requirements.txt` itself, rather
  than to hallucinate a package name.

Other hardening in the same vein:

- Providers that fail with a retryable error (429/5xx/timeout) are skipped for
  `FIXORA_PROVIDER_COOLDOWN` seconds (default 300; `0` restores always-retry)
  instead of being re-tried on every tool call.
- Sandbox subprocesses (pytest, pip) run with a minimal allowlisted environment
  — host API keys and `FIXORA_API_TOKEN` are not visible to repo code.
- `PYTHONDONTWRITEBYTECODE=1` plus explicit `__pycache__` invalidation after
  every `write_file`/`apply_patch`: an in-place edit that keeps file size
  identical within the same (uint32-truncated) mtime second can no longer test
  against stale bytecode. A regression test (`tests/test_bytecode_invalidation.py`)
  pins this.

## Verify

```bash
( cd demo_repo && python -m pytest )        # baseline: 3 failed, 11 passed
( cd demo_repo_hard && python -m pytest )  # baseline: 5 failed, 9 passed
( cd demo_repo_syntax && python -m pytest ) # baseline: collection blocked (SyntaxError)
( cd demo_repo_import && python -m pytest ) # baseline: collection blocked (ImportError)
python -m pytest tests/                   # unit tests (134 passing)
cd frontend && npm run build              # TypeScript + Vite production build
```

Offline end-to-end smoke (no keys, no network, `LLM_PROVIDER=mock`):

```bash
LLM_PROVIDER=mock python -m fixora.cli --repo ./demo_repo --task "Fix the failing tests"
LLM_PROVIDER=mock python -m fixora.cli --repo ./demo_repo_hard --task "Fix the failing tests"
LLM_PROVIDER=mock python -m fixora.cli --repo ./demo_repo_syntax --task "Fix the failing tests"
LLM_PROVIDER=mock python -m fixora.cli --repo ./demo_repo_import --task "Fix the failing tests"
```

Both runs should reach `awaiting_approval`; adding `--yes` on a copy applies the
diff. The `mock` provider is deterministic and demo-specific — it is **not** an
LLM and exists only to smoke-test the pipeline. Real autonomous behaviour
requires `LLM_PROVIDER=groq|xai|anthropic|openai` with a valid key in `.env`.

## Repository layout

- `fixora/` — the agent (`config`, `llm`, `sandbox`, `tools/`, `agents/`,
  `events.py`, `runner.py`, `cli.py`, `api.py`, `github.py`)
- `demo_repo/` — swiftcart (Phase 1 fixture)
- `demo_repo_hard/` — ledgerline (Phase 2 fixture; two related bugs)
- `demo_repo_syntax/` — skylark (Phase 4 fixture; syntax error blocks collection)
- `demo_repo_import/` — meridian (Phase 4 fixture; import error blocks collection)
- `frontend/` — React/TypeScript UI (built with Vite)
- `Dockerfile` / `.dockerignore` — single-image API + built UI
- `tests/` — unit tests for the sandbox, tools, graph routing, caching,
  approval gate, GitHub validation and provider fallback
- `logs/` — JSON run logs and approval patches (gitignored)
- `SPEC.md` — architecture note + phase status

## What is deliberately out of scope

Web UI, deployment, Docker, vector search, PR creation. See `SPEC.md`.
