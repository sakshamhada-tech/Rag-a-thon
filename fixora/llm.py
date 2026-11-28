"""Provider-agnostic LLM client with native tool/function calling and fallback.

Supports Anthropic, OpenAI, xAI/Grok and Groq Cloud (both via OpenAI-compatible
APIs) via LLM_PROVIDER. Also provides a deterministic offline provider `mock`
used ONLY to smoke-test the agent pipeline without network access — it does
no real reasoning and must not be used in production.

Fallback chain: set LLM_PROVIDERS in .env, e.g.
  LLM_PROVIDERS=groq,openai                     # bare names, uses per-provider env
  LLM_PROVIDERS=groq|https://api.groq.com/openai/v1|gsk_key|openai/gpt-oss-120b,openai|https://api.openai.com/v1|sk_key|gpt-4o-mini
On 429/timeout/5xx/connection errors the client backs off once, then switches
FORWARD to the next provider in LLM_PROVIDERS and stays there: the chain is
monotonic for the lifetime of the process and never returns to an earlier key
(so a single run cannot flap key1 → key2 → key1 → key2 ...). The operator
console prints a [LLM FALLBACK] line; the dashboard only ever sees the
friendly message "Switching to next API key".

Internal message format (neutral dicts, OpenAI-flavoured):
  {"role": "system"|"user", "content": str}
  {"role": "assistant", "content": str, "tool_calls": [ToolCall, ...]}
  {"role": "tool", "tool_call_id": str, "name": str, "content": str}
Tool schemas are passed as {"name", "description", "parameters": JSONSchema}.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from .config import MAX_OUTPUT_CHARS


class LLMError(Exception):
    """Raised for missing keys, API failures, or malformed responses."""


class ToolStepCapError(LLMError):
    """A phase consumed its tool-step budget (MAX_TOOL_STEPS) without
    converging. Raised instead of silently truncating the phase so the run
    stops in the resumable 'error' state with partial work preserved — the
    user can Resume (fresh step budget, checkpoint kept) instead of starting
    from scratch.
    """


_FRIENDLY_RULES = [
    (re.compile(r"(413|request too large|too many tokens|reduce your message|"
                r"context length|context window|maximum context|token limit)", re.I),
     "The model rejected the request for exceeding its size/rate limit (TPM). "
     "Fixora compacted the context and retried; if the run stopped here, the "
     "provider's token limit is too small — pick a larger model or add a fallback "
     "provider via LLM_PROVIDERS."),
    (re.compile(r"(429|rate limit|too many requests|cooldown|quota)", re.I),
     "The provider's rate limit was reached. Wait a minute and retry, or configure "
     "an additional provider via LLM_PROVIDERS (fallback chain)."),
    (re.compile(r"(401|403|unauthorized|invalid api key|authentication|permission denied)", re.I),
     "The provider rejected the API key. Check the key in your .env configuration."),
    (re.compile(r"(404|model.*(not found|decommissioned|retired|deprecated)|does not exist)", re.I),
     "The configured model was not found or was retired. Check GROQ_MODEL (see GET /openai/v1/models)."),
    (re.compile(r"(timeout|timed out|connection|unreachable|econnreset|socket hang up)", re.I),
     "The provider could not be reached (network timeout or connection error). "
     "Check your connection and the provider's status page."),
    (re.compile(r"(500|502|503|504|overloaded|temporarily unavailable|server error)", re.I),
     "The provider is temporarily unavailable. Try again shortly, or configure a fallback provider."),
    (re.compile(r"(insufficient_quota|billing|payment required|402)", re.I),
     "The provider reports a billing/quota problem. Check your plan and billing settings."),
    (re.compile(r"tool[- ]step budget", re.I),
     "The agent ran out of tool steps before finishing. Partial work is kept — "
     "press Resume to continue with a fresh step budget instead of starting over."),
]


def friendly_llm_error(error: Exception | str) -> str:
    """Map a provider/transport failure to a dashboard-safe one-liner.

    The technical detail is never dropped: nodes log the raw exception into the
    run's step log before it reaches here. This string is what end users and
    the browser UI see — no org IDs, request metadata or provider internals.
    """
    text = str(error)
    for pattern, message in _FRIENDLY_RULES:
        if pattern.search(text):
            return message
    return ("The model could not complete this request. The run log has the "
            "technical details.")


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    # Token usage reported by the provider ({"prompt_tokens","completion_tokens","total_tokens"}); None for mock.
    usage: dict | None = None


def _truncate(s: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    return s if len(s) <= limit else s[:limit] + f"\n...[truncated {len(s) - limit} chars]"


def _get(obj, key, default=None):
    """Tolerate dict-style and object-style payloads (Groq/OpenAI-compatible SDKs)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _parse_tool_arguments(raw, tool_name: str = "") -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if not isinstance(raw, str):
        return {"_raw_arguments": str(raw)}
    text = raw.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Let the tool return a structured error instead of crashing the run.
        return {"_raw_arguments": raw, "_json_error": f"invalid JSON arguments for {tool_name or 'tool'}"}
    return parsed if isinstance(parsed, dict) else {"_raw_arguments": parsed}


def _parse_anthropic_response(resp) -> LLMResponse:
    text, calls = "", []
    for block in _get(resp, "content", []) or []:
        btype = _get(block, "type")
        if btype == "text":
            text += _get(block, "text", "") or ""
        elif btype == "tool_use":
            name = str(_get(block, "name", ""))
            calls.append(ToolCall(id=str(_get(block, "id", "")),
                                  name=name,
                                  arguments=_parse_tool_arguments(_get(block, "input", {}), name)))
    usage = _get(resp, "usage") or {}
    usage = dict(usage) if isinstance(usage, dict) else {k: _get(usage, k) for k in ("input_tokens", "output_tokens")}
    return LLMResponse(text=text, tool_calls=calls, usage=_usage_dict(usage))


def _parse_openai_response(resp) -> LLMResponse:
    choices = _get(resp, "choices", []) or []
    if not choices:
        raise LLMError("empty choices in response")
    msg = _get(choices[0], "message", {}) or {}
    text = _get(msg, "content", "") or ""
    calls = []
    for i, tc in enumerate(_get(msg, "tool_calls", None) or []):
        fn = _get(tc, "function", {}) or {}
        name = str(_get(fn, "name", ""))
        calls.append(ToolCall(id=str(_get(tc, "id", f"call_{i}")),
                              name=name,
                              arguments=_parse_tool_arguments(_get(fn, "arguments", {}), name)))
    usage = _get(resp, "usage") or {}
    usage = dict(usage) if isinstance(usage, dict) else {}
    return LLMResponse(text=text, tool_calls=calls, usage=_usage_dict(usage))


def _usage_dict(usage: dict) -> dict | None:
    if not usage:
        return None
    # normalise both provider shapes to OpenAI-style keys
    out = {}
    inp = usage.get("prompt_tokens", usage.get("input_tokens"))
    outp = usage.get("completion_tokens", usage.get("output_tokens"))
    tot = usage.get("total_tokens", (inp or 0) + (outp or 0) if (inp is not None or outp is not None) else None)
    if inp is not None:
        out["prompt_tokens"] = int(inp)
    if outp is not None:
        out["completion_tokens"] = int(outp)
    if tot is not None:
        out["total_tokens"] = int(tot)
    return out or None


def _supports_reasoning(model: str, base_url: str | None) -> bool:
    markers = ("gpt-oss", "o1", "o3", "o4", "gpt-5", "grok-4", "qwen3", "deepseek-r")
    return any(m in (model or "").lower() for m in markers)


def _supports_temperature(model: str) -> bool:
    """Strict reasoning models (o-series, gpt-5) reject any temperature other
    than the default — omit it there. Everywhere else pin temperature=0 so tool
    calls stay schema-faithful instead of sampling at temperature=1."""
    m = (model or "").lower()
    return not any(x in m for x in ("o1", "o3", "o4", "gpt-5"))


# ---------------------------------------------------------------- Anthropic

class AnthropicClient:
    supports_reasoning = False   # Claude uses extended thinking, not reasoning_effort

    def __init__(self, api_key: str | None, model: str):
        if not api_key:
            raise LLMError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set (see .env.example)")
        from anthropic import Anthropic
        self.client = Anthropic(api_key=api_key)
        self.model = model
        self.provider_label = "anthropic"

    def chat(self, messages: list[dict], tools: list[dict], reasoning_effort: str | None = None,
             max_tokens: int | None = None) -> LLMResponse:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system") or None
        convo: list[dict] = []
        for m in messages:
            role = m["role"]
            if role == "system":
                continue
            if role == "user":
                convo.append({"role": "user", "content": m["content"]})
            elif role == "assistant":
                blocks = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for i, tc in enumerate(m.get("tool_calls", [])):
                    tc_id = str(_get(tc, "id", f"call_{i}"))
                    tc_name = str(_get(tc, "name", ""))
                    blocks.append({"type": "tool_use", "id": tc_id, "name": tc_name,
                                   "input": _parse_tool_arguments(_get(tc, "arguments", {}), tc_name)})
                convo.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            elif role == "tool":
                convo.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": m["tool_call_id"],
                     "content": m["content"]}]})
        anthropic_tools = [{"name": t["name"], "description": t["description"],
                            "input_schema": t["parameters"]} for t in tools]
        kwargs = dict(model=self.model, max_tokens=max_tokens or 4096, messages=convo,
                      temperature=0)
        if system:
            kwargs["system"] = system
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools
        try:
            resp = self.client.messages.create(**kwargs)
        except Exception as e:  # SDK raises several subclasses; normalise
            raise LLMError(f"Anthropic API error: {e}") from e
        return _parse_anthropic_response(resp)


# ------------------------------------------------------------------ OpenAI

class OpenAIClient:
    def __init__(self, api_key: str | None, model: str, *, base_url: str | None = None,
                 provider_label: str = "openai", key_hint: str = "OPENAI_API_KEY",
                 max_retries: int = 2):
        if not api_key:
            raise LLMError(f"LLM_PROVIDER={provider_label} but {key_hint} is not set (see .env.example)")
        from openai import OpenAI
        self.client = (OpenAI(api_key=api_key, base_url=base_url, max_retries=max_retries)
                       if base_url else OpenAI(api_key=api_key, max_retries=max_retries))
        self.model = model
        self.provider_label = provider_label
        self.supports_reasoning = _supports_reasoning(model, base_url)

    def chat(self, messages: list[dict], tools: list[dict], reasoning_effort: str | None = None,
             max_tokens: int | None = None) -> LLMResponse:
        oai: list[dict] = []
        for m in messages:
            role = m["role"]
            if role in ("system", "user"):
                oai.append({"role": role, "content": m["content"]})
            elif role == "assistant":
                msg: dict = {"role": "assistant", "content": m.get("content") or None}
                if m.get("tool_calls"):
                    serialized = []
                    for i, tc in enumerate(m["tool_calls"]):
                        tc_name = str(_get(tc, "name", ""))
                        serialized.append({
                            "id": str(_get(tc, "id", f"call_{i}")), "type": "function",
                            "function": {"name": tc_name,
                                         "arguments": json.dumps(_parse_tool_arguments(_get(tc, "arguments", {}), tc_name))}})
                    msg["tool_calls"] = serialized
                oai.append(msg)
            elif role == "tool":
                oai.append({"role": "tool", "tool_call_id": m["tool_call_id"],
                            "content": m["content"]})
        oai_tools = [{"type": "function",
                      "function": {"name": t["name"], "description": t["description"],
                                   "parameters": t["parameters"]}} for t in tools]
        kwargs: dict = dict(model=self.model, messages=oai, max_tokens=max_tokens or 4096)
        if oai_tools:
            kwargs["tools"] = oai_tools
            # Sequential calls only: parallel calls multiply the odds of one
            # malformed element taking down the whole request with a 400.
            kwargs["parallel_tool_calls"] = False
        if _supports_temperature(self.model):
            kwargs["temperature"] = 0
        if reasoning_effort and self.supports_reasoning:
            kwargs["reasoning_effort"] = reasoning_effort
        try:
            resp = self.client.chat.completions.create(**kwargs)
        except Exception as e:
            raise LLMError(f"{self.provider_label} API error: {e}") from e
        return _parse_openai_response(resp)


# -------------------------------------------------------------------- Mock

def _first_source_hit(text: str, basename: str):
    """First 'path/to/<basename>:NN:' match that is NOT a test file."""
    matches = list(re.finditer(rf"([\w./-]*?{re.escape(basename)}):\d+:", text))
    for candidate_m in matches:
        path = candidate_m.group(1)
        if re.search(r"(^|/)(tests?|specs?)/", path):
            continue
        if "test_" in path.rsplit("/", 1)[-1]:
            continue
        return candidate_m
    return matches[0] if matches else None

class MockClient:
    """Deterministic offline provider for pipeline smoke tests ONLY."""

    supports_reasoning = False

    def __init__(self):
        self._n = 0
        self.provider_label = "mock"

    @staticmethod
    def _phase(messages) -> str:
        for m in messages:
            if m["role"] == "system":
                mm = re.search(r"PHASE:\s*(\w+)", m["content"])
                if mm:
                    return mm.group(1).lower()
        return "fix"

    def chat(self, messages: list[dict], tools: list[dict], reasoning_effort: str | None = None,
             max_tokens: int | None = None) -> LLMResponse:
        self._n += 1
        phase = self._phase(messages)
        tool_results = [m for m in messages if m["role"] == "tool"]
        tool_names = [m.get("name") for m in tool_results]

        def call(name, args):
            return LLMResponse(text="", tool_calls=[
                ToolCall(id=f"mock-{self._n}-{name}", name=name, arguments=args)])

        if phase == "analyse":
            if "list_tree" not in tool_names:
                return call("list_tree", {"path": ".", "max_depth": 3})
            if "search_code" not in tool_names:
                return call("search_code", {"pattern": "def ", "path": "."})
            tree = next(m["content"] for m in tool_results if m.get("name") == "list_tree")
            return LLMResponse(text=(
                "Repo summary (offline mock):\n- Layout:\n" + _truncate(tree, 1500) +
                "\n- Entry points: package modules under the source root; tests in tests/."
                "\n- Dependencies: see requirements / top-level imports (get_dependencies)."
                "\n- Tests live in the tests/ directory and run with pytest."))

        if phase == "plan":
            if "search_code" not in tool_names:
                return call("search_code", {"pattern": "ROUND|quantize|round|quantity", "path": "."})
            hits = next(m["content"] for m in tool_results if m.get("name") == "search_code")
            if "(no matches)" in hits:
                return LLMResponse(text=(
                    "Suspected root cause is unclear from the symbol search.\n"
                    "Fix plan: read the module the failing traceback points at, correct the "
                    "blocking defect, then re-run the tests.\nEvidence:\n(no matches)"))
            return LLMResponse(text=(
                "Suspected root cause: discount/money rounding helper truncates instead of using "
                "round-half-up, so odd-cent amounts come out one cent low.\n"
                "Fix plan: open the rounding helper, switch the quantizer to ROUND_HALF_UP, "
                "re-run the tests.\nEvidence:\n" + _truncate(hits, 800)))

        if phase == "fix":
            all_text = "\n".join(str(m.get("content", "")) for m in messages)
            # File-selection heuristics must read *tool outputs* (thefreshest search/read
            # evidence), not the whole prompt — otherwise instruction blocks like the RAG
            # "Relevant code retrieved" section, which cite many .py paths, hijack the
            # earliest-regex-match and steer the mock at the wrong file.
            tool_text = "\n".join(str(m.get("content", "")) for m in messages
                                   if m.get("role") == "tool")
            evidence = tool_text if tool_text.strip() else all_text
            if "search_code" not in tool_names:
                return call("search_code", {
                    "pattern": r"ROUND_DOWN|ROUND_HALF_EVEN|ROUND_HALF_UP|quantity > 25|import |f\"\$|stock_",
                    "path": ".",
                })
            if "read_file" not in tool_names:
                m = (_first_source_hit(evidence, "pricing.py")
                     or _first_source_hit(evidence, "catalog.py")
                     or (re.search(r"([\w./-]+\.py):[^\n]*quantity > 25", evidence)
                         if "test_rate_applies_at_twentyfive_items" in all_text else None))
                m = (m or re.search(r"([\w./-]+\.py):[^\n]*(ROUND_HALF_EVEN|ROUND_DOWN)", evidence)
                     or re.search(r"([\w./-]+\.py):[^\n]*quantity", evidence)
                     or re.search(r"([\w./-]+\.py)", evidence)
                     or re.search(r"([\w./-]+\.py)", all_text))
                target = m.group(1) if m else "swiftcart/money.py"
                return call("read_file", {"path": target})
            if "apply_patch" not in tool_names and "write_file" not in tool_names:
                read = next((m["content"] for m in tool_results if m.get("name") == "read_file"), "")
                path_m = re.search(r"===== ([\w./-]+\.py)", read) or re.search(r"([\w./-]+\.py)", read)
                target = path_m.group(1) if path_m else "swiftcart/money.py"
                if 'return f"${total:.2f}' in read and 'return f"${total:.2f}"' not in read:
                    return call("apply_patch", {"path": target,
                                                "old_str": 'return f"${total:.2f}',
                                                "new_str": 'return f"${total:.2f}"'})
                if "from .inventory import stock_level" in read:
                    return call("apply_patch", {"path": target,
                                                "old_str": "from .inventory import stock_level",
                                                "new_str": "from .inventory import stock_count"})
                if "stock_count" in read and re.search(r"\bstock_level\(", read):
                    return call("apply_patch", {"path": target,
                                                "old_str": "stock_level(",
                                                "new_str": "stock_count("})
                for bad in ("decimal.ROUND_HALF_EVEN", "decimal.ROUND_DOWN"):
                    if bad in read:
                        return call("apply_patch", {"path": target,
                                                    "old_str": bad,
                                                    "new_str": "decimal.ROUND_HALF_UP"})
                if "quantity > 25" in read:
                    return call("apply_patch", {"path": target,
                                                "old_str": "quantity > 25",
                                                "new_str": "quantity >= 25"})
                m = re.search(r"([\w.]+)\.ROUND_(DOWN|HALF_EVEN)", read)
                if m:  # generic fallback: swap only the fully-qualified constant
                    return call("apply_patch", {"path": target,
                                                "old_str": f"{m.group(1)}.ROUND_{m.group(2)}",
                                                "new_str": f"{m.group(1)}.ROUND_HALF_UP"})
                return LLMResponse(text="No known-fix pattern found in the file (offline mock).")
            return LLMResponse(text="Patch applied; re-running tests.")

        if phase == "review":
            return LLMResponse(text=('{"verdict": "approve", "issues": [], '
                                     '"risk": "low", "summary": "Offline mock reviewer: change is minimal and targeted."}'))

        return LLMResponse(text="Offline mock: nothing to do for this phase.")


# ------------------------------------------------------------- fallback chain

def _is_retryable(error: Exception) -> bool:
    text = str(error).lower()
    markers = ("429", "rate limit", "timeout", "timed out", "500", "502", "503", "504",
               "overloaded", "connection error", "connection reset", "unreachable",
               "temporarily unavailable", "econnreset", "socket hang up",
               # a hard size rejection can succeed on a bigger-context provider
               "413", "request too large", "too many tokens", "context length",
               "reduce your message")
    return any(m in text for m in markers)


class FallbackLLMClient:
    """Try clients in the configured order; switch FORWARD on retryable errors.

    Monotonic for the process lifetime: once client N has been exhausted by a
    retryable error, later calls never go back to N-1, N-2, ... The pointer
    advances once and sticks at the healthy client, so a run walks the keys in
    number order (key1 → key2 → key3 …) and never flaps back to an earlier key.
    Callers in tests/tools can subclass; the class is stateful by design.
    """

    provider_label = "fallback"

    def __init__(self, clients: list):
        self.clients = clients
        self._cursor = 0  # index into clients; only ever increases

    def chat(self, messages: list[dict], tools: list[dict], reasoning_effort: str | None = None,
             max_tokens: int | None = None) -> LLMResponse:
        n = len(self.clients)
        last_error: LLMError | None = None
        for idx in range(self._cursor, n):
            client = self.clients[idx]
            label = getattr(client, "provider_label", f"provider-{idx}")
            for attempt in range(2):
                try:
                    try:
                        resp = client.chat(messages, tools, reasoning_effort=reasoning_effort,
                                           max_tokens=max_tokens)
                    except TypeError as exc:
                        if "max_tokens" not in str(exc):
                            raise
                        resp = client.chat(messages, tools, reasoning_effort=reasoning_effort)
                    if resp.usage:
                        _log_usage(label, resp.usage)
                    return resp
                except LLMError as e:
                    last_error = e
                    if not _is_retryable(e):
                        raise
                    if attempt == 0:
                        time.sleep(2 ** attempt)
                        continue
                    self._cursor = idx + 1
                    if idx < n - 1:
                        nxt = getattr(self.clients[idx + 1], "provider_label", f"provider-{idx + 1}")
                        _log_fallback(f"[LLM FALLBACK] {label} failed ({_brief(e)}); "
                                      f"switching forward to {nxt}")
                    break
        if last_error is not None:
            raise LLMError(
                f"All {n} configured API keys have reached their limits (or are "
                f"temporarily unavailable). Add another key via LLM_PROVIDERS, or "
                f"wait and retry. Last error: {_brief(last_error, 120)}")
        raise LLMError("No LLM providers configured")


def _log_usage(label: str, usage: dict):
    print(f"[LLM USAGE] provider={label} "
          f"prompt={usage.get('prompt_tokens')} completion={usage.get('completion_tokens')} "
          f"total={usage.get('total_tokens')}")


def _log_fallback(detail: str, friendly: str = "Switching to next API key"):
    # Operator console gets the technical detail; UI/journal get the friendly line.
    print(detail)
    cb = getattr(FallbackLLMClient, "on_fallback", None)
    if callable(cb):
        cb(friendly)


def _brief(e: Exception, limit: int = 160) -> str:
    s = str(e).replace("\n", " ")
    return s if len(s) <= limit else s[:limit] + "…"


# -------------------------------------------------------------------- factory

def _build_one(provider: str, settings):
    provider = provider.strip().lower()
    if provider == "anthropic":
        return AnthropicClient(settings.anthropic_api_key, settings.anthropic_model)
    if provider == "openai":
        return OpenAIClient(settings.openai_api_key, settings.openai_model)
    if provider in ("xai", "grok", "grok-api"):
        return OpenAIClient(settings.xai_api_key, settings.xai_model,
                            base_url=settings.xai_base_url,
                            provider_label="xai", key_hint="XAI_API_KEY")
    if provider == "groq":
        return OpenAIClient(settings.groq_api_key, settings.groq_model,
                            base_url=settings.groq_base_url,
                            provider_label="groq", key_hint="GROQ_API_KEY",
                            max_retries=4)
    if provider == "mock":
        return MockClient()
    raise LLMError(f"Unknown LLM provider {provider!r} in LLM_PROVIDERS "
                   "(expected anthropic, openai, xai, groq or mock, or explicit 'name|base_url|key|model')")


def _parse_chain(raw: str, settings) -> list:
    """Parse LLM_PROVIDERS: comma-separated bare names or 'label|base_url|key|model' entries."""
    clients = []
    for index, entry in enumerate(raw.split(","), start=1):
        entry = entry.strip()
        if not entry:
            continue
        parts = [p.strip() for p in entry.split("|")]
        if len(parts) == 1:
            clients.append(_build_one(parts[0], settings))
        elif len(parts) == 4:
            label, base_url, key, model = parts
            if not label or not base_url or not key or not model:
                raise LLMError(
                    f"Malformed LLM_PROVIDERS entry {index}: expected non-empty "
                    "label|base_url|key|model (one line; no line breaks)")
            clients.append(OpenAIClient(key, model, base_url=base_url,
                                        provider_label=label,
                                        key_hint=f"key for {label}",
                                        max_retries=2))
        else:
            raise LLMError(f"Malformed LLM_PROVIDERS entry {index}: expected "
                           "'name' or 'label|base_url|key|model'")
    return clients


def get_llm(settings):
    """Factory: build the (possibly chained) client from the environment."""
    raw = (getattr(settings, "llm_providers", "") or "").strip()
    if raw:
        clients = _parse_chain(raw, settings)
        if not clients:
            raise LLMError("LLM_PROVIDERS is set but contains no usable entries")
        return clients[0] if len(clients) == 1 else FallbackLLMClient(clients)
    return _build_one(settings.llm_provider, settings)


def list_available_models(settings) -> list[str]:
    """List model IDs visible to the configured provider key (no inference)."""
    provider = settings.llm_provider
    if provider == "mock":
        return ["mock (offline)"]
    try:
        if provider == "anthropic":
            if not settings.anthropic_api_key:
                raise LLMError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set")
            from anthropic import Anthropic
            resp = Anthropic(api_key=settings.anthropic_api_key).models.list()
            return sorted(str(_get(m, "id", "")) for m in (_get(resp, "data", []) or []))
        if provider in ("openai", "xai", "grok", "grok-api", "groq"):
            if provider == "openai":
                key, base_url = settings.openai_api_key, None
            elif provider in ("xai", "grok", "grok-api"):
                key, base_url = settings.xai_api_key, settings.xai_base_url
            else:
                key, base_url = settings.groq_api_key, settings.groq_base_url
            if not key:
                key_env = {"openai": "OPENAI_API_KEY", "groq": "GROQ_API_KEY"}.get(provider, "XAI_API_KEY")
                raise LLMError(f"LLM_PROVIDER={provider} but {key_env} is not set")
            from openai import OpenAI
            resp = OpenAI(api_key=key, base_url=base_url, max_retries=0).models.list()
            return sorted(str(_get(m, "id", "")) for m in (_get(resp, "data", []) or []))
        raise LLMError(f"Unknown LLM_PROVIDER={provider!r}")
    except LLMError:
        raise
    except Exception as e:
        raise LLMError(f"could not list models for provider {provider}: {e}") from e
