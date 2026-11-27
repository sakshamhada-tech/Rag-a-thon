"""Provider-agnostic LLM client with native tool/function calling.

Supports Anthropic and OpenAI via LLM_PROVIDER. Also provides a deterministic
offline provider `mock` used ONLY to smoke-test the agent pipeline without
network access — it does no real reasoning and must not be used in production.

Internal message format (neutral dicts, OpenAI-flavoured):
  {"role": "system"|"user", "content": str}
  {"role": "assistant", "content": str, "tool_calls": [ToolCall, ...]}
  {"role": "tool", "tool_call_id": str, "name": str, "content": str}
Tool schemas are passed as {"name", "description", "parameters": JSONSchema}.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .config import MAX_OUTPUT_CHARS


class LLMError(Exception):
    """Raised for missing keys, API failures, or malformed responses."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)


def _truncate(s: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    return s if len(s) <= limit else s[:limit] + f"\n...[truncated {len(s) - limit} chars]"


# ---------------------------------------------------------------- Anthropic

class AnthropicClient:
    def __init__(self, api_key: str | None, model: str):
        if not api_key:
            raise LLMError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set (see .env.example)")
        from anthropic import Anthropic
        self.client = Anthropic(api_key=api_key)
        self.model = model

    def chat(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
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
                for tc in m.get("tool_calls", []):
                    blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name,
                                   "input": tc.arguments})
                convo.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            elif role == "tool":
                convo.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": m["tool_call_id"],
                     "content": m["content"]}]})
        anthropic_tools = [{"name": t["name"], "description": t["description"],
                            "input_schema": t["parameters"]} for t in tools]
        kwargs = dict(model=self.model, max_tokens=4096, messages=convo)
        if system:
            kwargs["system"] = system
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools
        try:
            resp = self.client.messages.create(**kwargs)
        except Exception as e:  # SDK raises several subclasses; normalise
            raise LLMError(f"Anthropic API error: {e}") from e
        text, calls = "", []
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text += block.text
            elif getattr(block, "type", None) == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name,
                                      arguments=dict(block.input or {})))
        return LLMResponse(text=text, tool_calls=calls)


# ------------------------------------------------------------------ OpenAI

class OpenAIClient:
    def __init__(self, api_key: str | None, model: str):
        if not api_key:
            raise LLMError("LLM_PROVIDER=openai but OPENAI_API_KEY is not set (see .env.example)")
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def chat(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        oai: list[dict] = []
        for m in messages:
            role = m["role"]
            if role in ("system", "user"):
                oai.append({"role": role, "content": m["content"]})
            elif role == "assistant":
                msg: dict = {"role": "assistant", "content": m.get("content") or None}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [{
                        "id": tc.id, "type": "function",
                        "function": {"name": tc.name,
                                     "arguments": json.dumps(tc.arguments)}} for tc in m["tool_calls"]]
                oai.append(msg)
            elif role == "tool":
                oai.append({"role": "tool", "tool_call_id": m["tool_call_id"],
                            "content": m["content"]})
        oai_tools = [{"type": "function",
                      "function": {"name": t["name"], "description": t["description"],
                                   "parameters": t["parameters"]}} for t in tools]
        kwargs = dict(model=self.model, messages=oai)
        if oai_tools:
            kwargs["tools"] = oai_tools
        try:
            resp = self.client.chat.completions.create(**kwargs)
        except Exception as e:
            raise LLMError(f"OpenAI API error: {e}") from e
        msg = resp.choices[0].message
        calls = [ToolCall(id=tc.id, name=tc.function.name,
                          arguments=json.loads(tc.function.arguments or "{}"))
                 for tc in (msg.tool_calls or [])]
        return LLMResponse(text=msg.content or "", tool_calls=calls)


# -------------------------------------------------------------------- Mock

class MockClient:
    """Deterministic offline provider for pipeline smoke tests ONLY.

    It never talks to a model: it scripts the obvious read -> locate -> patch
    sequence so the tool-dispatch, sandbox, pytest and report plumbing can be
    exercised without network access or API keys. It does NOT prove the agent
    can solve novel bugs. Requires a `PHASE: <name>` marker in the system prompt.
    """

    def __init__(self):
        self._n = 0

    @staticmethod
    def _texts(messages, role):
        return [m["content"] for m in messages if m["role"] == role]

    @staticmethod
    def _phase(messages) -> str:
        for m in messages:
            if m["role"] == "system":
                mm = re.search(r"PHASE:\s*(\w+)", m["content"])
                if mm:
                    return mm.group(1).lower()
        return "fix"

    def chat(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
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
                return call("search_code", {"pattern": "ROUND|quantize|round", "path": "."})
            hits = next(m["content"] for m in tool_results if m.get("name") == "search_code")
            return LLMResponse(text=(
                "Suspected root cause: money/rounding helper truncates instead of using "
                "round-half-up, so odd-cent amounts come out one cent low.\n"
                "Fix plan: open the rounding helper, switch the quantizer to ROUND_HALF_UP, "
                "re-run the tests.\nEvidence:\n" + _truncate(hits, 800)))

        if phase == "fix":
            joined = "\n".join(m["content"] for m in tool_results)
            # Find the file that mentions ROUND_DOWN from earlier tool output.
            if "read_file" not in tool_names:
                m = re.search(r"([\w./-]+\.py):[^\n]*ROUND_DOWN", joined)
                if not m:
                    m = re.search(r"([\w./-]+\.py)", joined)
                target = m.group(1) if m else "swiftcart/money.py"
                return call("read_file", {"path": target})
            if "apply_patch" not in tool_names and "write_file" not in tool_names:
                read = next((m["content"] for m in tool_results if m.get("name") == "read_file"), "")
                path_m = re.search(r"===== ([\w./-]+\.py)", read) or re.search(r"([\w./-]+\.py)", read)
                target = path_m.group(1) if path_m else "swiftcart/money.py"
                if "decimal.ROUND_DOWN" in read:
                    return call("apply_patch", {"path": target,
                                                "old_str": "decimal.ROUND_DOWN",
                                                "new_str": "decimal.ROUND_HALF_UP"})
                m = re.search(r"([\w.]+)\.ROUND_DOWN", read)
                if m:  # generic fallback: swap only the fully-qualified constant
                    return call("apply_patch", {"path": target,
                                                "old_str": f"{m.group(1)}.ROUND_DOWN",
                                                "new_str": f"{m.group(1)}.ROUND_HALF_UP"})
                return LLMResponse(text="No known-fix pattern found in the file (offline mock).")
            return LLMResponse(text="Patch applied; re-running tests.")

        return LLMResponse(text="Offline mock: nothing to do for this phase.")


def get_llm(settings):
    """Factory: build the client selected by LLM_PROVIDER."""
    provider = settings.llm_provider
    if provider == "anthropic":
        return AnthropicClient(settings.anthropic_api_key, settings.anthropic_model)
    if provider == "openai":
        return OpenAIClient(settings.openai_api_key, settings.openai_model)
    if provider == "mock":
        return MockClient()
    raise LLMError(f"Unknown LLM_PROVIDER={provider!r} (expected 'anthropic', 'openai' or 'mock')")
