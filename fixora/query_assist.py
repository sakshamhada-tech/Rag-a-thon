"""Optional Groq-assisted retrieval stages (all behind config flags, default ON).

Stages:
  QUERY_REWRITE — one small LLM call turns the failing tests, assertion text and
                  traceback into up to 3 focused search queries.
  RERANK        — ~15 local candidates come in; one small LLM call orders the top
                  k. Skipped when the local top hit is dominant; always falls back
                  to the local RRF order on any failure (bad JSON, timeout, error).

Both calls use low reasoning effort, tight max_tokens, the per-run memo cache,
and never raise — a retrieval quality booster can never break a run. Latency and
build-vs-skip decisions are logged to the run log via ctx.log_step("rag", ...).
"""
from __future__ import annotations

import hashlib
import json
import os
import re

from .llm import LLMError

_MAX_TOKENS = 120
_SNIPPET_CAP = 300
_ID_RE = re.compile(r"\bc(\d+)\b")


def _flag(name: str, default: str = "1") -> bool:
    return (os.environ.get(name, default) or default).strip().lower() not in ("0", "false", "off", "no")


def _memo(ctx):
    memo = getattr(ctx, "_qa_memo", None)
    if memo is None:
        memo = {}
        ctx._qa_memo = memo
    return memo


def _memo_key(payload: str) -> str:
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def rewrite_queries(ctx, state: dict) -> list[str]:
    """Up to 3 focused query strings; [] on any failure."""
    if not _flag("FIXORA_QUERY_REWRITE", "1"):
        return []
    r = (state or {}).get("last_test_result") or {}
    failing = ", ".join(str(t) for t in (r.get("failing_tests") or [])[:4])
    diag = r.get("diagnosis") or {}
    diag_line = f"{diag.get('summary', '')} {'; '.join(diag.get('exceptions') or [])}".strip()
    task = str((state or {}).get("task") or "")
    if not (failing or diag_line):
        return []
    prompt = (
        "You are refining search queries for source-code retrieval.\n"
        f"Task: {task}\nFailing tests: {failing}\nFailure: {diag_line}\n"
        "Output at most 3 short search queries for locating the buggy code — "
        "symbols, function names, behaviours — one per line, no bullets, no prose.")
    key = "rewrite:" + _memo_key(prompt)
    memo = _memo(ctx)
    if key in memo:
        return memo[key]
    try:
        resp, ms = ctx.timed_llm(lambda: ctx.llm.chat(
            [{"role": "system", "content": "You emit terse search queries only."},
             {"role": "user", "content": prompt}],
            [], reasoning_effort="low"))
    except LLMError as e:
        ctx.log_step("rag", "query_rewrite unavailable; using local queries", error=str(e)[:200])
        memo[key] = []
        return []
    except Exception as e:
        ctx.log_step("rag", "query_rewrite unavailable; using local queries",
                     error=f"{type(e).__name__}")
        memo[key] = []
        return []
    queries = []
    for line in resp.text.splitlines():
        line = line.strip().lstrip("-*•0123456789. ").strip()
        if line and len(line) <= 120 and len(line.split()) <= 16:
            queries.append(line)
        if len(queries) >= 3:
            break
    ctx.log_step("rag", f"query_rewrite produced {len(queries)} queries",
                 latency_ms=round(ms, 1), queries=queries)
    memo[key] = queries
    return queries


def rerank(ctx, state: dict, candidates: list[dict], k: int = 5) -> tuple[list[dict], bool]:
    """Return (ordered_candidates, rerank_applied). Never raises."""
    if not _flag("FIXORA_RERANK", "1"):
        return candidates[:k], False
    if len(candidates) <= k:
        return candidates[:k], False
    scores = [c.get("score", 0.0) for c in candidates]
    if len(scores) >= 2 and scores[1] > 0 and scores[0] / scores[1] >= 3.0:
        ctx.log_step("rag", "rerank skipped: dominant local top hit",
                     top=scores[0], second=scores[1])
        return candidates[:k], False
    # keep the call small: top 15, snippets capped
    take = candidates[:15]
    listing = []
    for i, c in enumerate(take):
        snippet = str(c.get("text", ""))[:_SNIPPET_CAP].replace("\n", "\\n")
        listing.append(f"c{i}|{c.get('path')}:{c.get('start_line')}|{c.get('symbol', '')}|{snippet}")
    prompt = (
        "Select the code chunks most relevant to fixing the failing tests.\n"
        f"Task: {state.get('task', '')}\n"
        "Chunks (id|path:line|symbol|snippet):\n" + "\n".join(listing) + "\n"
        f"Return STRICT JSON: {{\"ranked_ids\": [\"c2\", \"c0\", ...]}} with the best "
        f"{k} ids first. No prose.")
    key = "rerank:" + _memo_key(prompt)
    memo = _memo(ctx)
    if key in memo:
        ids = memo[key]
    else:
        try:
            resp, ms = ctx.timed_llm(lambda: ctx.llm.chat(
                [{"role": "system", "content": "You rank code chunks and answer with JSON only."},
                 {"role": "user", "content": prompt}],
                [], reasoning_effort="low"))
            raw = resp.text.strip()
            start, end = raw.find("{"), raw.rfind("}")
            data = json.loads(raw[start:end + 1]) if start != -1 else {}
            ids = data.get("ranked_ids") or []
            ids = [m.group(0) for m in _ID_RE.finditer(" ".join(str(i) for i in ids))]
            memo[key] = ids
            ctx.log_step("rag", f"rerank applied over {len(take)} candidates",
                         latency_ms=round(ms, 1), picked=ids[:k])
        except Exception as e:
            ctx.log_step("rag", "rerank unavailable; falling back to local ranking",
                         error=f"{type(e).__name__}")
            memo[key] = []
            return candidates[:k], False
    if not ids:
        return candidates[:k], False
    index = {f"c{i}": c for i, c in enumerate(take)}
    ordered = [index[i] for i in ids if i in index]
    seen = {id(c) for c in ordered}
    ordered += [c for c in candidates if id(c) not in seen]
    return ordered[:k], True
