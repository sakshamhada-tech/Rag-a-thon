# DevMate

**From Generative AI to Agentic AI — hackathon project, Phase 3.**
DevMate is an autonomous coding agent that takes a Python repo plus a task,
analyses the repo, finds the bug, patches it, runs the real test suite, gets a
second opinion from a Reviewer agent, and reports the result — from the CLI or
from a small FastAPI server with live events.

Small and working beats big and half-working: there is still no web UI, no
vector DB, no embeddings and no PR creation (Phase 3).

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
  DevMate retries the verdict with no tools exposed (still never auto-approves
  without a human). Otherwise it
  must emit strict JSON (`verdict`, `issues`, `risk`, `summary`). A rejection
  routes back to `fix` with reviewer issues in context and counts toward
  `MAX_ATTEMPTS`. A hard code rule flags diffs under `tests/` and forces
  rejection when assertions or test functions were removed/loosened.
- **LLM**: provider-agnostic wrapper with native tool calling.
  `LLM_PROVIDER=groq` (Groq Cloud, free tier), `xai` (Grok), `anthropic` or
  `openai`; keys/models from `.env`. `LLM_PROVIDERS` enables a fallback chain
  (see below).
- **Server**: FastAPI + SSE (see `devmate/api.py`), one run at a time, run
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
python -m devmate.cli --repo ./demo_repo --task "Find and fix the failing tests"
```

The run stops at `awaiting_approval` after tests pass and the Reviewer
approves. The original repo is untouched until:

```bash
python -m devmate.cli --repo ./demo_repo --task "Find and fix the failing tests" --yes
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
python -m devmate.cli --list-models
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

## Phase 3 web UI

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
LLM_PROVIDER=mock .venv/bin/python -m uvicorn devmate.api:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/`. FastAPI serves `frontend/dist` and the API from the
same origin. `/health`, `/config`, `/demos`, `/runs` and `/runs/{id}/events` back
the UI. Built-in demos appear in the left panel; GitHub runs still accept only
public repositories. Demo runs are copied to a temp directory and can only export
a patch, so approving a demo never mutates `demo_repo/` or `demo_repo_hard/`.

### Frontend development

```bash
LLM_PROVIDER=mock .venv/bin/python -m uvicorn devmate.api:app --host 127.0.0.1 --port 8000
cd frontend
npm run dev   # Vite on 127.0.0.1:5173, proxying /runs, /demos, /config, /health
```

Set `VITE_API_PROXY_TARGET` from a shell if the API is not on port 8000.

### UI environment and controls

- `DEVMATE_API_TOKEN` — optional API bearer token. The static UI shell is public;
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
- SSE replay/live stream uses event sequence ids and `Last-Event-ID`; the reducer
  dedupes by `seq`.
- Diff is rendered by a custom unified-diff parser; tests/review/log tabs show
  real run data only.
- Approval remains consequential: confirmation step, disabled while in flight,
  patch download for demo/GitHub runs, local apply only for approved local runs.

### Docker

```bash
docker build -t devmate .
docker run --rm -p 8000:8000 -e LLM_PROVIDER=mock devmate
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
  `DEVMATE_API_TOKEN` and send `Authorization: Bearer ...`; CORS defaults to
  localhost origins (`CORS_ORIGINS` to override), never wildcard credentials.

## FastAPI server

Open `http://127.0.0.1:8000/` in a browser (or use curl). The server is a JSON
API, not a finished frontend: `/` shows the available endpoints, `/health` returns
`{"ok": true}`, and `/docs` shows the interactive API docs. To start a run,
use `POST /runs` with a public GitHub URL.

```bash
source .venv/bin/activate
LLM_PROVIDER=groq python -m uvicorn devmate.api:app --host 127.0.0.1 --port 8000

# if binding 0.0.0.0, require a bearer token:
DEVMATE_API_TOKEN=choose-a-long-random-token   python -m uvicorn devmate.api:app --host 0.0.0.0 --port 8000
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

## Verify

```bash
( cd demo_repo && python -m pytest )      # baseline: 3 failed, 11 passed
( cd demo_repo_hard && python -m pytest ) # baseline: 5 failed, 9 passed
python -m pytest tests/                   # unit tests (97 passing)
cd frontend && npm run build              # TypeScript + Vite production build
```

Offline end-to-end smoke (no keys, no network, `LLM_PROVIDER=mock`):

```bash
LLM_PROVIDER=mock python -m devmate.cli --repo ./demo_repo --task "Fix the failing tests"
LLM_PROVIDER=mock python -m devmate.cli --repo ./demo_repo_hard --task "Fix the failing tests"
```

Both runs should reach `awaiting_approval`; adding `--yes` on a copy applies the
diff. The `mock` provider is deterministic and demo-specific — it is **not** an
LLM and exists only to smoke-test the pipeline. Real autonomous behaviour
requires `LLM_PROVIDER=groq|xai|anthropic|openai` with a valid key in `.env`.

## Repository layout

- `devmate/` — the agent (`config`, `llm`, `sandbox`, `tools/`, `agents/`,
  `events.py`, `runner.py`, `cli.py`, `api.py`, `github.py`)
- `demo_repo/` — swiftcart (Phase 1 fixture)
- `demo_repo_hard/` — ledgerline (Phase 2 fixture; two related bugs)
- `frontend/` — Phase 3 React/TypeScript UI
- `Dockerfile` / `.dockerignore` — single-image API + built UI
- `tests/` — unit tests for the sandbox, tools, graph routing, caching,
  approval gate, GitHub validation and provider fallback
- `logs/` — JSON run logs and approval patches (gitignored)
- `SPEC.md` — architecture note + phase status

## What is deliberately out of scope

Web UI, deployment, Docker, vector search, PR creation. See `SPEC.md`.
