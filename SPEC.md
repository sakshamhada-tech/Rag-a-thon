# DevMate — SPEC

## Architecture note

DevMate Phase 2 keeps the Phase 1 sandboxed loop and adds caching, a reviewer
agent, a human approval gate, a FastAPI/SSE server and strict GitHub input
handling:

```
python -m devmate.cli --repo ./demo_repo --task "Find and fix the failing tests"
        │
        ▼
┌───────────────────────────────────────────────┐
│ runner.py: load settings, clone/copy repo into │
│ temp workspace, build tools + RunContext,      │
│ build & invoke LangGraph, hold sandbox until   │
│ human approval                                │
└───────────────────────────────────────────────┘
        │
        ▼
   LangGraph StateGraph (agents/graph.py)
   baseline(test) → analyse → plan → fix → test ─┐
        ▲                                │ pass │
        │                                ▼       │
        │                              review    │
        │                                │       │
        └──────── retry (fix) ◄──────────┘       │
                  │ approve                      │
                  ▼                              ▼
                report (awaiting_approval / failed) → END
```

**Shared state** (`AgentState`, TypedDict): `task`, `repo_summary`, `plan`,
`attempts`, `max_attempts` (default 3), `baseline_test_result`,
`last_test_result`, `edits_made`, `diff`, `review`, `status`, `report`,
`messages`.

**Nodes**
1. `baseline` — runs `run_tests` once BEFORE any edit via the tester code path;
   records `baseline_test_result` so the report can show before/after.
2. `analyse` — Developer agent, read-only tools; writes `repo_summary`.
   `repo_summary` and already-read file contents are passed into plan/fix.
   Prompt rule: "Do not re-read files you already have."
3. `plan` — Developer agent; states suspected root cause + fix plan.
4. `fix` — Developer agent; edit tools with a capped tool-calling loop
   (`MAX_TOOL_STEPS`, default 12). Retry context = the failing output appended as
   a user message. Reviewer rejections are appended as user messages with the
   reviewer issues; an attempt re-enters here and counts toward `max_attempts`.
5. `test` — Tester node; runs the real suite via `run_tests`; parsed pytest
   counts/failing tests. Never based on LLM claims.
6. `review` — Reviewer agent (read-only tools); strict JSON verdict:
   `{"verdict":"approve|reject","issues":[...],"risk":"low|medium|high","summary":"..."}`.
   Checks root cause vs hack, weakened/deleted tests, unrelated changes,
   regressions. **Hard code rule**: a diff touching `tests/` is flagged; if
   assertions/test functions were removed or loosened, the reviewer is forced
   to reject regardless of LLM output. If a provider rejects the tool-enabled
   reviewer pass because the model emits pseudo-tools such as `json` or
   `commentary`, the node retries the verdict with no tools exposed and the
   diff in the prompt; the attempt is marked `fallback_used` in the review.
7. `report` — `awaiting_approval` when tests pass AND reviewer approves;
   otherwise `failed`. The report carries before/after test counts, attempts,
   edits, diff, review, timings (LLM duration, tool duration, cached hits,
   retries, total wall time).

**Human approval gate** (`runner.py`): the sandbox is kept alive after `report`.
Status is `awaiting_approval`; nothing touches the original repo. CLI `--yes`
or `POST /runs/{id}/approve`:
- local repo → changed/new files copied back, deleted files removed;
- GitHub clone → a patch file is written to `logs/<run_id>.patch` (no push, no PR).
`POST /runs/{id}/reject` marks the run `rejected` and cleans up without any
writes. `POST /runs/{id}/approve`/`reject` on a non-`awaiting_approval` run
returns 409.

**Server** (`api.py`): `POST /runs {repo, task}` (GitHub URLs only, validated,
public; local paths rejected with 400), `GET /runs/{id}/events` (SSE replay +
live stream of `step`, `tool_call`, `tool_result` (truncated), `test_result`,
`review_verdict`, `retry`, `awaiting_approval`, `done`, ...), `GET /runs/{id}`,
`POST /runs/{id}/approve`, `POST /runs/{id}/reject`, `GET /runs/{id}/patch`,
`GET /runs`. CORS enabled; one run at a time per server (429 otherwise);
`RUN_TIMEOUT` watchdog; state in memory + JSON log in `logs/`.

**GitHub input** (`github.py`): only `https://github.com/<owner>/<repo>`
(optional `.git`/trailing slash) is accepted; other hosts, SSH, `file://` and
local paths are rejected. Repos are `git clone --depth 1` with a timeout
(`CLONE_TIMEOUT`, default 120s) and a size cap (`MAX_REPO_MB`, default 50 MB);
`.git` is discarded after the size check.

**Tool-call caching** (`agents/common.py`): identical tool calls (same tool +
normalized args) in one run hit an in-memory cache and return the previous
result marked `[CACHED]`. Any successful `write_file`/`apply_patch` bumps a
generation counter, invalidating the cache (and dropping that path from the
"already read" replay). This is per-run, not persisted across runs.

**Tool-argument validation** (`tools/__init__.py`): declared types are checked
before dispatch (`integer` excludes `bool`); missing required args and type
mismatches are returned as `{"error": ...}` payloads, never raised. `read_file`
accepts `start_line`/`line_start`/`start` and `end_line`/`line_end`/`end`.

**Timings**: `RunContext.timed_llm` records per-call LLM duration;
`execute_tool` records per-call tool duration; `fix_node` emits `retry` events
and counts them; `ctx.timings()` aggregates everything into the report and the
JSON run log. `[CACHED ...]` steps carry `cached: true` and zero duration.

**LLM wrapper** (`llm.py`): same facade as Phase 1 plus:
- `LLM_PROVIDERS` fallback chain (`name` entries reuse per-provider env, or
  `label|base_url|key|model`); switches on 429/timeout/5xx/connection errors
  with exponential backoff and a `[LLM FALLBACK]` log line. Non-retryable 4xx
  (e.g. bad schema) is raised immediately — it is never masked by fallback.
- `reasoning_effort` sent as `low` (analyse/fix) and `medium` (plan/review)
  when the model supports it; `max_tokens` capped at 4096.
- `MockClient` extended: plans, reviews (auto-approve JSON) and can drive
  `demo_repo_hard` through the two-bug retry sequence offline.

**Demo repos**
- `demo_repo/` ("swiftcart") — Phase 1 fixture: one rounding bug, 3 failing tests.
- `demo_repo_hard/` ("ledgerline") — Phase 2 fixture: banker's rounding in
  `money.py` **and** `quantity > 25` vs `>= 25` in `discounts.py`. Baseline:
  9 passed, 5 failed. One fix leaves 3 tests failing; the retry loop must fix
  the tier bug too. No comments hint at either bug.

**Logging**: every step, tool call, result and event is appended to
`logs/<timestamp>-<run_id>.json` (gitignored); CLI prints live
`[ANALYSE] [PLAN] [TOOL CALL ...] [CACHED ...] [TEST] [RETRY n] [REVIEW]
[AWAITING APPROVAL] [DONE]` lines.

## Status

### Done (Phase 2)
- [x] Tool-call dedupe/cache with `[CACHED]` marker + mutation invalidation
- [x] `repo_summary` + already-read file contents passed into plan/fix; "Do not
      re-read files you already have" rule
- [x] `read_file` aliases (`start_line`/`line_start`/`start`, `end_line`/`line_end`/`end`)
- [x] Tool-argument validation returning data, not exceptions
- [x] Per-step timings (LLM call, tool, retries) in run log and report
- [x] `reasoning_effort` low for analyse/fix, medium for plan/review;
      `max_tokens` capped at 4096
- [x] `LLM_PROVIDERS` fallback chain with exponential backoff + `[LLM FALLBACK]` log
- [x] Reviewer agent node (read-only tools, strict JSON), hard code rule for
      `tests/` diffs forcing rejection on removed/loosened assertions
- [x] Reviewer rejection routes back to fix with issues; counts toward `max_attempts`
- [x] Human approval gate: `awaiting_approval`; original repo untouched until
      approve; approve applies diff (local) or writes a patch file (GitHub);
      reject marks `rejected`; nothing consequential without approval
- [x] FastAPI server (`devmate/api.py`) with `/runs` POST, `/runs/{id}/events`
      SSE (replay + live), `GET /runs/{id}`, `POST /approve`, `POST /reject`,
      `GET /patch`, `GET /runs`; CORS; one run at a time (429); `RUN_TIMEOUT`
- [x] Strict public GitHub URL validation + shallow clone with timeout/size cap
- [x] `demo_repo_hard/` fixture (two related bugs across two modules)
- [x] Unit tests: aliases, argument validation, caching, mutation invalidation,
      approval gate (no write before approve; approve writes), GitHub URL
      validator, provider fallback, patch export, reviewer pseudo-tool fallback —
      79 tests, all pass
- [x] Offline end-to-end CLI (`LLM_PROVIDER=mock`) verified on `demo_repo` and
      `demo_repo_hard` (reaches `awaiting_approval`; two-bug retry observed on
      `demo_repo_hard`, logs in `logs/`)
- [x] CLI `--yes` on a scratch copy applied the reviewed diff and left
      `demo_repo/swiftcart/money.py` untouched

### Verified evidence
- Baseline: `demo_repo` 3 failed/11 passed; `demo_repo_hard` 5 failed/9 passed.
- Offline mock end-to-end: `demo_repo` 1 attempt -> 14 passed ->
  `awaiting_approval`; `demo_repo_hard` 2 attempts (1 retry) -> 14 passed ->
  `awaiting_approval`; original fixtures untouched without `--yes`.
- Unit suite: `79 passed`.
- Maintainer local Groq run on `demo_repo_hard` (2026-11-28, prompt supplied by
  maintainer): both bugs were fixed and tests reached `14 passed`, but the
  reviewer failed because Groq rejected pseudo-tool calls (`json`,
  `commentary`). This exposed a reviewer robustness bug, fixed by the
  verdict-only fallback above; that fallback still requires human approval.
- FastAPI process verified locally: `POST /runs` with a local path returns 400;
  public GitHub URL returns 202 and streams SSE events replaying the run;
  local approval covered by unit tests.

### Known limitations / honestly not verified
- **No Phase 2 real-LLM run in this sandbox** — there is no `.env`/API key here
  and provider reachability (e.g. `api.groq.com`) was not exercised. The
  maintainer ran Phase 2 locally on 2026-11-28 against Groq for
  `demo_repo_hard`; the model fixed both bugs and reached `14 passed`, but the
  reviewer aborted on Groq-side tool-call validation for pseudo-tools. The
  reviewer fallback added afterwards is unit-tested only; a fresh real-provider
  run is required to claim the whole path is green.
- GitHub end-to-end in the sandbox cloned the public `Rag-a-thon` repo and
  streamed events successfully; the root pytest suite collected the
  sub-project `demo_repo/tests`, which fails to import (`swiftcart` lives
  under `demo_repo/`), so that run failed rather than reaching
  `awaiting_approval`. The API is designed for standalone Python repos with
  pytest at the root; approval for GitHub runs (patch export) is covered by a
  unit test, not by a live public-repo fix.
- Fallback chain is unit-tested with fake clients (429 -> switch; 400 ->
  raise), not against real provider outages.
- `reasoning_effort` is best-effort: providers that do not recognize the
  parameter may reject it; the wrapper only sends it when the model looks like
  a reasoning-capable family (gpt-oss, o-/gpt-5, grok-4, qwen3, deepseek-r).
- One run at a time is process-global; a second concurrent `POST /runs` gets
  429. State is in-memory + JSON log (no DB, no restart recovery).
- Run timeout marks the run `timed_out` in the API, but the graph is not
  forcibly killed mid-tool-call; the watchdog frees the slot and the graph's
  own tool/LLM timeouts bound the tail.
- `mock` provider remains deterministic and demo-shaped; it is a plumbing
  check, not evidence of model capability.
- `run_tests` parses pytest's summary line with regexes; exotic output may
  still parse imperfectly.

### Out of scope (Phase 2)
Frontend UI, deployment, Docker, vector search, PR creation, pushing branches,
multi-tenant auth, billing/cost accounting. See "Next" in README/SPEC history.

### Phase 3 candidates
- Real GitHub issue -> branch -> PR flow (clone, branch, commit, PR) behind
  explicit approval.
- Real-LLM evaluation on the demo corpus; measure fix rate and reviewer
  agreement; feed reviewer disagreements back for prompt tuning.
- Junitxml parsing for `run_tests`; token/cost tracking; per-phase model routing.
- Persist run state (SQLite) and allow resume of `awaiting_approval` after restart.
