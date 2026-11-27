"""Regression: stale bytecode must never shadow a patched source file.

Python validates .pyc files by (truncated uint32 mtime, source size). An agent
edit that lands within the same second as the previous test run AND keeps the
file size identical (e.g. ROUND_HALF_EVEN -> ROUND_HALF_UP, custody-sensitive
bugfixes) would otherwise test the OLD code — and could approve a fix that
never actually ran.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from fixora.config import Settings
from fixora.sandbox import Sandbox, _minimal_env
from fixora.tools import build_tools

REPO_ROOT = Path(__file__).resolve().parents[1]


def _settings(**overrides):
    values = dict(
        llm_provider="mock",
        anthropic_api_key=None,
        anthropic_model="claude-sonnet-4-5",
        openai_api_key=None,
        openai_model="gpt-4o-mini",
        xai_api_key=None,
        xai_model="grok-4-latest",
        xai_base_url="https://api.x.ai/v1",
        groq_api_key=None,
        groq_model="openai/gpt-oss-120b",
        groq_base_url="https://api.groq.com/openai/v1",
        max_attempts=3,
        command_timeout=60,
        max_tool_steps=12,
        llm_providers="",
        run_timeout=900,
        clone_timeout=120,
        max_repo_mb=50,
        read_cache_chars=24000,
    )
    values.update(overrides)
    return Settings(**values)


def test_minimal_env_blocks_bytecode_writes():
    env = _minimal_env()
    assert env.get("PYTHONDONTWRITEBYTECODE") == "1"
    assert "GROQ_API_KEY" not in env and "FIXORA_API_TOKEN" not in env


def test_same_second_same_size_edit_is_tested_fresh(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text('VALUE = "aaaaaaaa"\n', encoding="utf-8")
    (repo / "test_mod.py").write_text(
        "from mod import VALUE\n\n\ndef test_value():\n    assert VALUE == 'bbbbbbbb'\n",
        encoding="utf-8")
    sandbox = Sandbox(repo)
    tools = build_tools(sandbox, _settings())
    try:
        baseline = json.loads(tools["run_tests"].run({}))
        assert baseline["status"] == "failed"

        # Guarantee a fresh .pyc is on disk (baseline run may already have made one).
        compile_result = sandbox.run(["python", "-m", "py_compile", "mod.py"], timeout=30)
        assert compile_result.returncode == 0
        workspace_mod = Path(sandbox.workspace) / "mod.py"
        assert list(Path(sandbox.workspace).glob("**/__pycache__/*.pyc")), "expected cached bytecode"

        before_mtime = int(os.stat(workspace_mod).st_mtime)

        # Same-length patch (8 chars -> 8 chars), then pin mtime back into the
        # same uint32 second so the old .pyc is unambiguously "valid" per CPython.
        patch = tools["apply_patch"].run(
            {"path": "mod.py", "old_str": "aaaaaaaa", "new_str": "bbbbbbbb"})
        assert json.loads(patch)["ok"] is True
        os.utime(workspace_mod, ns=(before_mtime * 10**9, before_mtime * 10**9))

        after = json.loads(tools["run_tests"].run({}))
        assert after["status"] == "passed", \
            f"tests ran against stale bytecode: {after['output_tail'][-200:]}"
    finally:
        sandbox.cleanup()
