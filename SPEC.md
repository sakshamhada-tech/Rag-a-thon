# DevMate — SPEC

## Architecture note

DevMate Phase 1 is a minimal but real end-to-end agent loop, CLI-only:

```
python -m devmate.cli --repo ./demo_repo --task "Find and fix the failing tests"
        │
        ▼
┌────────────────────────────────────────────────────────────┐
│ cli.py: load settings, create Sandbox (temp copy of repo), │
│ build tools bound to sandbox, build & invoke LangGraph     │
└────────────────────────────────────────────────────────────┘
        │
        ▼
   LangGraph StateGraph (agents/graph.py)
   baseline(test) → analyse → plan → fix → test → decide ─┐
        ▲                                      │         │
        └──────────── retry (failing output in context) ─┘
                                               ▼
                                            report → END
```

**Shared state** (`AgentState`, TypedDict): `task`, `repo_summary`, `plan`,
`attempts`, `max_attempts` (default 3), `baseline_test_result`,
`last_test_result`, `edits_made`, `diff`, `status`, `report`, `messages`.

**Nodes**
1. `baseline` — runs `run_tests` once BEFORE any edit via the tester code path;
   records `baseline_test_result` so the report can show before/after.
2. `analyse` — Developer agent, read-only tools (`list_tree`, `read_file`,
   `search_code`, `get_dependencies`); writes `repo_summary`.
3. `plan` — Developer agent; states suspected root cause + fix plan.
4. `fix` — Developer agent; edit tools (`write_file`, `apply_patch`) with a
   capped tool-calling loop (`MAX_TOOL_STEPS`, default 12). Increments
   `attempts`. Retry context = the failing output appended as a user message.
5. `test` — Tester node; runs the real suite via `run_tests`; result is parsed
   from pytest output (pass/fail counts, failing test names, trimmed traceback).
   Never based on LLM claims.
6. `decide` (conditional edge) — tests pass → `report`; fail and
   `attempts < max_attempts` → `fix`; else → `report`.
7. `report` — final summary: root cause (plan), edits made, unified diff
   (original repo → workspace), tests before/after, attempts, status.

**Sandbox** (`sandbox.py`): `tempfile.mkdtemp` copy of the target repo
(excluding `.git`, venvs, caches). `resolve()` rejects absolute paths, `..`
escapes, null bytes and symlink escapes (via `realpath` + containment check).
`run()` allows only `python -m pytest|py_compile|unittest`, no shell, with a
timeout (default 60s), captured + truncated I/O (8000 chars), and a regex
allowlist for arguments plus a defense-in-depth containment check. `diff()`
produces the unified diff via `difflib`.

**Tools** return size-limited, structured results; errors come back as
`{"error": ...}` payloads, never exceptions. `apply_patch` uses exact-match
replacement and fails unless `old_str` occurs exactly once.

**LLM wrapper** (`llm.py`): neutral internal message format; native tool/function
calling. `AnthropicClient` (messages API + `tool_use` blocks), `OpenAIClient`
(chat.completions + `tools`), Grok/xAI and Groq Cloud (both via `OpenAIClient`
with `XAI_BASE_URL` / `GROQ_BASE_URL`) selected by `LLM_PROVIDER`; model/keys
from `.env`. Response parsing tolerates dict- or object-style SDK payloads
(Groq returns tool-call objects that are not always SDK-typed). `MockClient` (`LLM_PROVIDER=mock`) is a deterministic offline
provider for pipeline smoke tests only — no LLM, no reasoning.
`python -m devmate.cli --list-models` lists model IDs visible to the configured
key (OpenAI-compatible providers and Anthropic) so you can fix `GROQ_MODEL`
when Groq retires/free-tier-blocks a model. Fatal LLM errors (bad key/model)
abort the run instead of retrying all attempts with no edits.

**Demo repo** (`demo_repo/`, "swiftcart"): shopping-cart / bulk-discount
calculator, stdlib-only, 14 tests. Exactly 3 tests fail on one planted logic
bug (money rounding), requiring the agent to read code — no hints in comments,
test names, or README. The bug is not weakened or touched by the harness; the
agent must fix the source.

**Logging**: every step, tool call and result is appended to
`logs/<timestamp>.json` (gitignored); CLI prints live `[ANALYSE] [PLAN]
[TOOL CALL ...] [TEST] [RETRY n] [REPORT]` lines and exits 0/1.

## Status

### Done (Phase 1)
- [x] Sandbox: temp-copy workspace, path-escape protection (incl. symlinks),
      allowlisted runner, timeout, truncation, unified diff
- [x] Tools: `list_tree`, `read_file`, `search_code`, `get_dependencies`,
      `write_file`, `apply_patch` (unique-anchor), `run_tests` (parsed output)
- [x] LangGraph loop: baseline → analyse → plan → fix → test → decide → report,
      with retry on failing tests and `max_attempts` budget
- [x] Provider-agnostic LLM wrapper (Anthropic + OpenAI + Grok/xAI + Groq Cloud) with native tool calling
- [x] CLI with live step printing, JSON run logs, exit code 0/1
- [x] Demo repo with one planted bug, 3 failing tests (verified at baseline)
- [x] Unit tests: sandbox path-escape/symlinks/allowlist, apply_patch
      uniqueness, decide() routing, pytest-output parsing (48 tests, all pass)
- [x] End-to-end CLI run verified (offline mock provider; see note below)

### Verified evidence
- Baseline: `3 failed, 11 passed` in `demo_repo` (before any edit).
- End-to-end (offline `mock` provider): baseline fail → patch `money.py`
  → `14 passed` → exit 0; original repo untouched (diff applied to temp copy).
- Unit suite: `48 passed`.

### Known limitations / not yet verified
- **No successful real LLM run yet** — the maintainer's free-tier Groq run
  failed fast on a 404 because `llama-3.3-70b-versatile` was retired for
  free/developer tiers on 2026-08-16. Default is now `openai/gpt-oss-120b`;
  run `--list-models` to pick a visible tool-calling model for your key. The
  offline mock provider proves the plumbing (sandbox, tools, graph routing,
  logs, exit codes), NOT that an LLM can solve novel bugs. First real run must
  use `LLM_PROVIDER=groq|xai|anthropic|openai` with a valid key in `.env`.
- Retry loop is exercised by construction (`decide` unit tests) but has not been
  observed to recover from a failed first attempt with a real model.
- `run_tests` parses counts with regexes against pytest's summary line; exotic
  pytest output (warnings-as-errors, `-p no:cacheprovider` noise) may need
  parser hardening.
- No cost/token accounting; `MAX_TOOL_STEPS` is the only per-node loop cap.
- Mock provider is synchronous/deterministic and demo-specific in its patch
  heuristic; it is not a general stand-in for an LLM.

### Next (Phase 2 candidates)
- Real-LLM evaluation on a small bug corpus; measure fix rate / attempts.
- Failure-aware retry: feed structured traceback slices, not just the tail.
- Reviewer agent + human-approval flow for edits (explicitly out of scope for P1).
- Web UI / API wrapper around the CLI loop; streaming step events.
- Token/cost tracking; per-phase model routing.
- GitHub issue → PR flow (clone, branch, PR creation) instead of temp copies.
- Harden `run_tests` parsing (junitxml output), add timeout/retry for LLM calls.
