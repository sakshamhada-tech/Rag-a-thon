# Fixora

**From Generative AI to Agentic AI — hackathon project.**
Fixora is an autonomous coding agent that takes a Python repo plus a task,
analyses the repo, finds the bug, patches it, and reports the result — from
the CLI or from a small FastAPI server with live events. Three real LLM
agents do the work — a **Developer**, a **Tester** and a **Reviewer** —
orchestrated by a LangGraph supervisor; the same team shows up live in the
web UI as an agent roster.

Small and working beats big and half-working: PR creation is out of scope, and
retrieval runs in-memory with no database — but the RAG layer is genuinely
hybrid (AST-aware chunks, BM25 **and** local vector similarity, reciprocal rank
fusion), not just term-overlap search.

### Problem & solution

**Problem.** Small Python codebases with failing tests burn human time on
localisation (which file? which line?) before any fixing happens — and naïve
LLM agents hallucinate fixes into the wrong file, trust their own claim that
"tests pass", or quietly weaken the tests to make them green.

**Solution.** Fixora (1) copies the repo into a throwaway sandbox and never
touches the original until a human approves, (2) grounds every prompt in what
the codebase actually contains via local hybrid retrieval, (3) requires a
**separate** Reviewer agent to approve a diff plus a **human** to approve the
consequential action, and (4) measures success only by running the real test
suite before and after every fix attempt — never by the model's say-so.

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
- **Agent team**: the Developer, Tester and Reviewer are distinct LLM agents —
  separate prompts, separate tool permissions, separate failure behaviours —
  coordinated by one LangGraph supervisor in one process with a shared graph
  state (see **Multi-agent setup** below). Every emitted event carries an
  `agent` tag (`developer`/`tester`/`reviewer`); the UI shows the team live.
- **LLM**: provider-agnostic wrapper with native tool calling.
  `LLM_PROVIDER=groq` (Groq Cloud, free tier), `xai` (Grok), `anthropic` or
  `openai`; keys/models from `.env`. `LLM_PROVIDERS` enables a fallback chain
  (see below).
- **Server**: FastAPI + SSE (see `fixora/api.py`), one run at a time, run
  timeout, in-memory state plus JSON log on disk (`logs/`, gitignored).
  `GET /health`, `GET /config`, `GET /demos`, `GET /memory` (cross-run lesson
  store), and `GET /openapi.json`/`/docs` for the interactive API reference.

## Multi-agent setup

Fixora runs **three real LLM agents**, orchestrated by a LangGraph supervisor
(`fixora/agents/graph.py`). They are separate agents in the meaningful sense —
distinct system prompts, distinct phase permissions, distinct tool sets — not
one prompt role-playing three hats:

| Agent | Nodes | Prompt / role | Tools |
|-------|-------|---------------|-------|
| **Developer** | `analyse`, `plan`, `fix` | `PHASE: <phase>` — "You are Fixora's Developer agent…" (`fixora/agents/developer.py`) | read-only tools (`list_tree`, `read_file`, `search_code`, `search_semantic`, `get_dependencies`, `retrieve_code`) in analyse/plan; adds `write_file` / `apply_patch` only in fix |
| **Tester** | `baseline`, `test` | runs the real pytest suite in the sandbox before and after every attempt (`fixora/agents/tester.py`) | `run_tests` (deterministic — the model never decides whether tests pass) |
| **Reviewer** | `review` | read-only second opinion on the diff; strict JSON verdict `{verdict, issues, risk, summary}` (`fixora/agents/reviewer.py`) | read tools only; a hard code rule flags diffs that loosen tests |

Supervisor routing: `baseline → analyse → plan → fix ⇄ test → review →
report`, with two feedback loops — a failing `test` routes back to `fix` with
the failure diagnosis in context (bounded by `MAX_ATTEMPTS`), and a `reject`
verdict from the Reviewer routes back to `fix` with the reviewer's issues in
context (also bounded). A passing `review` ends at `report` with status
`awaiting_approval`; nothing is written to the original repo until a human
approves.

Honest boundaries: the team is **one process with one shared graph state** —
no network between agents, no queue, no inter-agent RPC, no second host. The
agents never talk to each other directly; they coordinate through the
LangGraph state and the event bus, and the Tester and Reviewer are
deliberately independent of the Developer (they re-run the suite and re-read
the diff instead of trusting the Developer's claims).

Every event the system emits is automatically tagged with the responsible
agent (`fixora/events.py`: `node=analyse|plan|fix`, `type=retrieval|memory|retry`
→ `developer`; `node=test|baseline`, `type=test_result` → `tester`;
`node=review`, `type=review_verdict` → `reviewer`). The tag flows into the CLI
log, the SSE stream and the `/runs/{id}` state dump, so the UI needs no
string-matching to show who did what. The web UI renders this as a live
**agent roster** at the top of the timeline
(`frontend/src/components/AgentRoster.tsx`): three cards (Developer / Tester /
Reviewer) where the agent behind the newest tagged event is *working* (pulsing
dot), agents that already acted are *done*, and the rest *idle*; a terminal
run marks everyone `done`. Each card shows the agent's latest activity line
(tool name, pytest counts, review verdict, retrieved chunk count or recalled
lessons).

## Tech stack (every model, API, framework, dataset and tool)

**LLMs / APIs**
- **Groq Cloud** (OpenAI-compatible REST) — default provider, free tier; model
  `openai/gpt-oss-120b` (`GROQ_MODEL`); Groq offers no embeddings endpoint, so
  generation only. `GET /openai/v1/models` can be probed with the app's
  `--list-models`.
- **Anthropic** (`claude-sonnet-4-5` default), **xAI Grok** (`grok-4-latest`),
  **OpenAI** (`gpt-4o-mini` default) — supported alternative providers; any of
  them or `mock` (offline, deterministic, smoke-testing only — not an LLM).
- **Groq llama-3.3-70b-versatile** — used earlier in development; retired for
  free/dev tiers on 2026-08-16; documented, not required.
- **fastembed 0.9.1 + BAAI/bge-small-en-v1.5** (384-dim, ONNX) — preferred
  embedding model; pinned in `requirements.txt` and used by default
  (`FIXORA_EMBEDDER=auto`); weights fetched from Hugging Face on first use
  (~30 MB, then cached and loaded strictly from disk). Verified with
  `python scripts/verify_rag.py`.
- **Local hashed bag-of-n-grams embedder** — zero-network fallback embedding
  model (`FIXORA_EMBEDDER=hash`, or `auto` when fastembed is unavailable); a
  deterministic 384-dim hashed n-gram encoder written from scratch for this
  project (no pretrained weights, fully offline, zero cost).
- **Gemini free embedding API** — considered and rejected (keeps the zero-cost,
  no-extra-keys guarantee). Not used.
- **FAISS / `faiss-cpu`** — evaluated and rejected (pure-Python vector scan is
  fast enough at this corpus size); the seam exists if corpora grow.
- **Google / any other embedding API** — not used.

**Frameworks & libraries**
- **LangGraph** (supervisor for the three-agent team: baseline→analyse→plan→fix→test→review→report;
  retry routing, TypedDict state; every emitted event carries its agent tag) · **FastAPI** + **Uvicorn** (API + SSE +
  static UI) · **Pydantic** · **rank_bm25** (BM25Okapi) · **numpy** (pulled in
  by rank_bm25) · **openai** and **anthropic** Python SDKs · **pytest**
  (suite runner inside the fix loop and for the repo's own tests) ·
  **tomllib** (stdlib) · **React 18** · **TypeScript** · **Vite** ·
  **lucide-react** (icons) · **Fontsource** (self-hosted IBM Plex) ·
  native **fetch**/**EventSource** (no TanStack Query/SWR). No Tailwind.
- **Docker** (multi-stage image: Node build stage → slim Python runtime,
  non-root user, single port, `PORT` env, `/health`).

**Datasets** — none. Fixora is evaluated on its own fixtures (`demo_repo*`)
and on public GitHub repositories; it trains nothing and ships no dataset.

**Tools** — git (shallow clone of public repos), pip, npm, Node 20+,
Python 3.11, curl. **AI coding tools used to build this project** — **Arena.ai
Agent Mode** (an agentic coding harness; contributions recorded as `Arena
Agent <arena-agent@users.noreply.github.com>` in git history, built on
multiple frontier models) alongside human direction by the repository owner.

## Team

- **Saksham Hada** ([@sakshamhada-tech](https://github.com/sakshamhada-tech)) — project owner, direction & review.
- **Arena.ai Agent** — AI pair-programmer used to build/maintain the codebase
  (see `git log`).

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

### Prompt size and free-tier token limits (why a 413 can happen)

Groq's on-demand tier serves `openai/gpt-oss-120b` with an **8,000 tokens-per-minute**
limit; a single request whose prompt exceeds it is rejected outright with
`Error code: 413 — Request too large`. Fixora keeps prompts well under that:

- every call in the fix loop is compacted per request — the system prompt, the
  opening instruction and a contiguous tail of the conversation are kept, the
  elided middle is replaced with a note (`FIXORA_CONTEXT_BUDGET_CHARS`, default
  ~18,000 chars ≈ 4.5k tokens),
- if the model still rejects the request as too large, one transparent retry
  runs with half the budget,
- tool results are truncated, RAG chunks are clipped to 600 chars and the
  already-read file replay is elided to `FIXORA_READ_BLOCK_CHARS` (default
  8,000 chars, oldest files first),
- a 413 is also treated as retryable for the `LLM_PROVIDERS` fallback chain, so
  a bigger-context provider can take over the run.

Why it can appear "out of nowhere": the 8k TPM limit was always there, but free/
on-demand allocations have been shrinking (llama-3.3-70b-versatile was retired
for free/dev tiers on 2026-08-16) and prompts grow with retrieval context —
RAG injection, memory hints and the already-read replay all count toward the
same 8k. If you see 413s persistently, prefer a smaller task, lower
`MAX_TOOL_STEPS`, or add a fallback provider rather than re-running blindly.
Provider error internals (org IDs, limits, request metadata) are **not** shown
in the dashboard — users see a friendly one-liner; the raw detail stays in the
on-disk run log.

### LLM fallback chain

Set `LLM_PROVIDERS` in `.env` to try providers in order on
429/timeout/5xx/connection errors. After one retry on the failing key, the
chain switches FORWARD to the next key in the list and stays there — keys are
walked in number order and never flapped back to an earlier key, so runs with
several free-tier keys don't bounce between them every call. The operator
console prints a `[LLM FALLBACK]` line; the dashboard only shows the friendly
message `Switching to next API key`. The workflow is checkpointed after every node
(LangGraph `MemorySaver`, thread = run id), so switching keys/providers
**does not restart the run** — completed phases are kept and only the failed
node re-executes. If even the fallback chain is exhausted (e.g. all keys
rate-limited), the run ends in `error` with the workspace kept alive and the
dashboard offers a **Resume run** action (API: `POST /runs/{run_id}/resume`)
that continues from the failed node instead of starting over; a *fresh* run is
only one click away and keeps semantics unchanged. Note that nodes are not
transaction-wrapped: a provider failure inside `fix` discards that node's
partial tool calls (edits made inside the sandbox before the crash are kept).

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
  steps). No gap between panels; widths persist in `localStorage`. Each section has
  a minimum width (248 / 260 / 320 px for setup / timeline / inspector, enforced
  in CSS as well as during drags and on browser window resize), all text and cards
  wrap instead of clipping as panels narrow, and primary action buttons cap at
  340 px so they do not stretch across a widened section. Below 1180px the
  inspector becomes a slide-over and the right separator hides; below 760px the
  bottom tabs take over and separators hide.
- SSE replay/live stream uses event sequence ids and `Last-Event-ID`; the reducer
  dedupes by `seq`.
- A live **agent roster** tops the timeline: Developer / Tester / Reviewer
  cards fed by the `agent` tag on every SSE event — newest tagged event =
  working (pulsing), already acted = done, everything else idle; terminal runs
  mark all three done. Per-row agent markers in the timeline prefer the same
  tag, falling back to node-name matching.
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
runtime with git, installs `requirements.txt` (including `fastembed==0.9.1`)
and pre-downloads BAAI/bge-small-en-v1.5 at build time (falls back to the hash
embedder if the download fails), then serves UI + API on `${PORT:-8000}` as a
non-root user.
It does not include a real `.env`; pass provider keys with `-e` only if you
understand the risk. **Note:** Docker was not available in the build sandbox
used by the maintainer agent, so this image is written but not built/verified
there — `docker build -t fixora .` is the command to run where Docker exists.

### Vercel (static frontend)

The repo is deployable on Vercel as a **static frontend** (`frontend/`) plus a
**separately hosted backend** — the full backend cannot run on Vercel's
serverless functions (it uses threads, a 900 s run timeout, and an in-memory
run registry + filesystem workspaces/logs).

What is in the repo:

- `vercel.json` — builds `frontend/` with `npm ci && npm run build`, serves
  `frontend/dist`, and rewrites every non-file route to `index.html` so the
  SPA works on refresh/deep-link.
- `.vercelignore` — keeps backend sources, tests and caches out of the
  deployment upload.
- `VITE_API_BASE` — build-time env var. Empty (the default) means
  same-origin API calls, which is correct for Docker / FastAPI-served UI.
  On Vercel, set `VITE_API_BASE` to the public URL of your hosted Fixora
  backend (Vercel project settings → Environment Variables, or
  `vercel env add VITE_API_BASE`). The frontend then calls that origin
  directly for REST and SSE. Leave it unset and the UI loads but cannot
  reach any API.
- The backend must allow the Vercel origin via `CORS_ORIGINS`
  (e.g. `CORS_ORIGINS=https://your-app.vercel.app`).

Steps:

```bash
# 1) Host the backend somewhere long-lived (Docker/Flight/Render/...):
#    see the Docker section; set FIXORA_EMBEDDER=hash if its network cannot
#    reach HuggingFace, and LLM_PROVIDER / keys as usual.
docker build -t fixora . && docker run --rm -p 8000:8000 -e LLM_PROVIDER=mock fixora

# 2) Deploy the frontend from the repo root:
npm i -g vercel   # or use the Vercel dashboard with the GitHub repo
vercel env add VITE_API_BASE production   # e.g. https://fixora-backend.example.com
vercel --prod
```

Honest limits: there is no serverless backend in this deployment; if you need
a fully self-contained Vercel deployment, the run engine (runner, sandbox,
agents, memory) would have to be re-architected onto a queue + worker +
external store — out of scope for this repository as-is.

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

# if the run died on a provider error: resume from the failed node (not from scratch)
curl -X POST http://127.0.0.1:8000/runs/run_xxx/resume \
  -H "Authorization: Bearer $FIXORA_API_TOKEN"

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
4. **Add more keys via `LLM_PROVIDERS`** — the chain walks forward only
   (key1 → key2 → key3) and never returns to an earlier key in the same process.
5. Endpoint stability (`base_url` latency, provider region) and output verbosity
   of the model.

There is no safe "skip tests" mode: pytest is the pass/fail signal and runs
always (baseline + after each fix). Parallelising tool execution is not
implemented; ordering matters for edits.

## RAG (retrieval-augmented generation)

Fixora grounds every fix prompt in *retrieval*, not just the file the agent
happens to read first. Retrieval is local, hybrid and free — no hosted vector
database, no API keys, no per-token cost.

**Pipeline (fixora/retrieval.py)**

1. **AST-aware chunking** — Python files are parsed with `ast` into
   function / async-function / method / class / test chunks (decorators,
   docstrings and line ranges preserved), plus one **module chunk** per file
   (imports + top-level statements) and **line-window** chunks (30 lines, 10
   overlap) as a fallback for unparseable files. Index is signature-cached by
   relpath+mtime+size; it rebuilds automatically on any repo change.
2. **Hybrid scoring** — each query is scored with BM25 (via the `rank_bm25`
   package; an internal Okapi BM25 is used as fallback) **and** with local
   vector cosine similarity, then merged with **Reciprocal Rank Fusion**
   (k = 60). The candidate pool is intentionally wider than the final k;
   optional LLM reranking (below) only ever reorders within it.
3. **Failure-driven queries** — at analyse/plan/fix time the base queries are
   built from the *actual failing test names, the diagnosis category and the
   exception* (e.g. `test rate applies at twentyfive items`,
   `AssertionError`), not from the task text alone.
4. **Query assist (Groq, optional)** — `FIXORA_QUERY_REWRITE=1` lets Groq
   propose up to 3 alternative search queries; `FIXORA_RERANK=1` lets Groq
   rerank the fused candidate pool. Both are **default ON but strictly
   optional**: any LLM error, timeout, quota exhaustion or invalid JSON falls
   back to local ranking, the run is never blocked, counts against Groq rate
   limits (calls are cached per run), and they add nothing when no provider is
   reachable. Set either to 0 to disable.
5. **Embeddings** — **`FIXORA_EMBEDDER=auto` is the default**: Fixora uses the
   **fastembed** backend — **BAAI/bge-small-en-v1.5** (384-dim) via ONNX
   (`fastembed==0.9.1`, pinned in `requirements.txt`) — when it is installed,
   and transparently falls back to a deterministic, fully offline **hashed
   bag-of-n-grams embedder** (384-dim, snake_case/camelCase/digit-aware
   tokeniser, L2-normalised) when it is not. The hash embedder captures
   lexical similarity (identifiers, error strings, test names) — most of what
   bug localisation needs — with zero downloads; fastembed adds real neural
   semantics (synonym/paraphrase matching). Its one-time ~30 MB model download
   from Hugging Face is cached on disk, and cached models load strictly from
   disk (`local_files_only`), so subsequent and offline runs never touch the
   network. The embedder probe is memoised process-wide so repeated
   retriever constructions do not repeat it. If installation or the download
   is blocked, retrieval still works — the backend in use is reported in
   `stats()` and in the `retrieval` UI event. The Dockerfile pre-downloads the
   model at build time (best effort, falls back to hash). Set
   `FIXORA_EMBEDDER=hash` to pin the offline fallback, or
   `FIXORA_EMBEDDER=fastembed` to require fastembed. Groq does **not** offer
   an embeddings endpoint (checked against `GET /openai/v1/models`); Gemini's
   free embedding API was considered and rejected to keep the zero-cost,
   no-keys guarantee.
6. **Vector store** — a pure-Python in-memory cosine scan over the chunk
   matrix. FAISS was evaluated and deliberately rejected: at the observed
   corpus sizes (<5k chunks for realistic Python repos in this tool's scope) a
   numpy/Python scan is sub-millisecond and removes a heavyweight native
   dependency. No FAISS binary is shipped; if corpora grow by ~10x this is
   the first place to swap in `faiss-cpu` (the scoring interface stays the
   same). Documented deviation, not an oversight.
7. **Context injection & tools** — analyse gets k=4 chunks, plan/fix k=5, plus
   a `retrieve_code(query, k)` tool is exposed in every phase for deeper
   lookups. Oversized repos index only code + tests (docs/markdown skipped)
   above a size threshold.
8. **Observability** — every retrieval is logged as a `retrieval` step/event
   carrying the queries, backend, chunk count, index build time and the top
   chunk previews; the UI renders these as an expandable retrieval panel.

Disable retrieval entirely with `FIXORA_RAG=0`.

Honest limits: neural semantic quality depends on the fastembed backend being
installed and downloaded; when Fixora falls back to the hash embedder it knows
vocabulary, not meaning. Ranking on
the bundled demos occasionally places module/window chunks above the exact
buggy function; retrieval narrows the search space, it does not replace the
agent's read-before-edit discipline. Cross-validating retrieved spans is left
to the reviewer + tests.

## Cross-run memory

Beyond in-run context (graph state), Fixora keeps a tiny **cross-run lesson
store** (`fixora/memory.py`):

- After each run reaches its outcome, a distilled lesson is appended to
  `logs/memory.json` (gitignored): symptom (diagnosis + failing tests),
  suspected root cause (first line of the plan), fix summary (edits/files) and
  outcome (status + after-test counts). API keys and common secret patterns
  are redacted before write; at most 50 lessons are kept (FIFO).
- At analyse time, the two most similar past lessons for the current
  symptom/task are retrieved with the same local embedder (cosine ≥ 0.15) and
  injected into the prompt as **clearly labelled hints** — never as ground
  truth, never bypassing the human approval gate.
- `FIXORA_MEMORY=0` disables both load and save; `FIXORA_MEMORY_PATH`
  overrides the location. `GET /memory` returns the store for inspection;
  the UI renders injected lessons as an amber "Memory" event with scores.
- Memory is best-effort by design: any failure in load/save/recall is logged
  and ignored, never raised into a run.
- Honest limits: memory is a flat JSON file (no DB), written from *outcomes
  reported by the pipeline* — a wrong lesson from a bad run is possible and is
  only de-prioritised by similarity ranking, not by truth checking.

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
  the rest of the process (the fallback chain advances monotonically); the
  dashboard only shows `Switching to next API key` — raw provider errors stay
  on the server console.
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
python -m pytest tests/                   # 150 tests, all passing (hermetic; FIXORA_EMBEDDER=hash is set for the session)
python scripts/verify_rag.py              # one-command RAG + embeddings end-to-end check (FIXORA_EMBEDDER=auto by default)
cd frontend && npm run build              # TypeScript + Vite production build
```

Cross-run memory smoke (mock, offline): the first run writes a lesson to
`logs/memory.json`; re-running a *different* copy of the same fixture logs a
`memory` event with the recalled lesson; `curl localhost:8000/memory` lists up
to the last 50 lessons.

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
  `events.py`, `runner.py`, `cli.py`, `api.py`, `github.py`,
  `retrieval.py`, `embeddings.py`, `query_assist.py`, `memory.py`,
  `diagnostics.py`, `reasoning.py`, `security.py`, `rag.py` (deprecated shim))
- `demo_repo/` — swiftcart (Phase 1 fixture)
- `demo_repo_hard/` — ledgerline (Phase 2 fixture; two related bugs)
- `demo_repo_syntax/` — skylark (Phase 4 fixture; syntax error blocks collection)
- `demo_repo_import/` — meridian (Phase 4 fixture; import error blocks collection)
- `frontend/` — React/TypeScript UI (built with Vite); live agent roster in
  `frontend/src/components/AgentRoster.tsx`
- `scripts/verify_rag.py` — one-command end-to-end RAG verification (embedder
  determinism, index stats, failure-driven queries, cross-run memory recall,
  approval gate, `FIXORA_RAG=0` kill switch)
- `Dockerfile` / `.dockerignore` — single-image API + built UI
- `tests/` — unit tests for the sandbox, tools, graph routing, caching,
  approval gate, GitHub validation, provider fallback, hybrid retrieval/AST
  chunking/query-assist fallbacks and cross-run memory (150 passing)
- `ARCHITECTURE.md` / `DEMO.md` / `SLIDES_OUTLINE.md` — architecture, demo
  script and presentation outline
- `logs/` — JSON run logs and approval patches (gitignored)
- `SPEC.md` — architecture note + phase status

## What is deliberately out of scope

PR creation / pushing branches, multi-tenant auth, billing/cost accounting,
non-Python targets, a hosted vector database, persistent (DB-backed) run
state, and multi-process agent federation (the Developer/Tester/Reviewer team
is one process sharing one LangGraph state). See `SPEC.md` for the full list
and the phase-by-phase record.
