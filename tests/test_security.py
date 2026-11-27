"""Security hardening tests: secrets, sandbox, logs, API auth."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fixora.events import EventEmitter
from fixora.runner import RunManager, RunRecord
from fixora.sandbox import Sandbox, SandboxError
from fixora.tools import build_tools
from fixora.config import Settings
from fixora.security import redact_text


def _settings():
    return Settings(
        llm_provider="mock", anthropic_api_key=None, anthropic_model="claude",
        openai_api_key=None, openai_model="gpt-4o-mini", xai_api_key=None,
        xai_model="grok-4-latest", xai_base_url="https://api.x.ai/v1",
        groq_api_key=None, groq_model="openai/gpt-oss-120b",
        groq_base_url="https://api.groq.com/openai/v1",
        max_attempts=3, command_timeout=60, max_tool_steps=12, llm_providers="",
        run_timeout=900, clone_timeout=120, max_repo_mb=50, read_cache_chars=24000)


def test_sensitive_env_file_is_not_copied_or_readable(tmp_path):
    source = tmp_path / "repo"
    source.mkdir()
    (source / ".env").write_text("FAKE_SECRET=supersecretvalue123456\n", encoding="utf-8")
    (source / ".env.example").write_text("FAKE_SECRET=replace-me\n", encoding="utf-8")
    (source / "app.py").write_text("X = 1\n", encoding="utf-8")
    sb = Sandbox(source)
    try:
        assert not (sb.workspace / ".env").exists()
        assert (sb.workspace / ".env.example").exists()
        with pytest.raises(SandboxError, match="sensitive"):
            sb.resolve(".env")
        tools = build_tools(sb, _settings())
        out = json.loads(tools["read_file"].run({"path": ".env"}))
        assert "error" in out
        write_out = json.loads(tools["write_file"].run({"path": ".env", "content": "new"}))
        assert "error" in write_out
    finally:
        sb.cleanup()


def test_sensitive_env_file_does_not_appear_as_delete_in_diff(tmp_path):
    source = tmp_path / "repo"
    source.mkdir()
    (source / ".env").write_text("FAKE_SECRET=supersecretvalue123456\n", encoding="utf-8")
    (source / "app.py").write_text("X = 1\n", encoding="utf-8")
    sb = Sandbox(source)
    try:
        (sb.workspace / "app.py").write_text("X = 2\n", encoding="utf-8")
        diff = sb.diff()
        assert ".env" not in diff
        assert "supersecretvalue123456" not in diff
        assert "+X = 2" in diff
    finally:
        sb.cleanup()


def test_event_emitter_redacts_exact_and_known_token_patterns(monkeypatch):
    monkeypatch.setenv("EXAMPLE_API_KEY", "token-value-that-is-long-enough-123")
    emitter = EventEmitter(run_id="redact-test")
    event = emitter.emit("tool_result", result="token-value-that-is-long-enough-123 gsk_abcdefghijklmnopqrstuvwxyz123456")
    encoded = json.dumps(event)
    assert "token-value-that-is-long-enough-123" not in encoded
    assert "gsk_abcdefghijklmnopqrstuvwxyz123456" not in encoded
    assert "[REDACTED]" in encoded


def test_redact_text_redacts_secret_assignments():
    assert "supersecretvalue" not in redact_text("password=supersecretvalue")


def test_saved_run_logs_are_private(monkeypatch, tmp_path):
    import fixora.runner as runner_module

    monkeypatch.setattr(runner_module, "LOGS_DIR", tmp_path / "logs")
    manager = RunManager()
    record = RunRecord(run_id="logtest123", repo_input="x", task="y", is_github=False,
                       max_attempts=1, report={"diff": "diff"})
    record.emitter = EventEmitter(run_id=record.run_id)
    manager._save_log(record)

    files = list((tmp_path / "logs").glob("*.json"))
    assert len(files) == 1
    assert stat.S_IMODE((tmp_path / "logs").stat().st_mode) == 0o700
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
    assert record.log_file == str(files[0])


def test_api_requires_bearer_token_when_configured(monkeypatch):
    from fixora.api import app

    monkeypatch.setenv("FIXORA_API_TOKEN", "test-token-that-is-long-enough-123")
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/runs/not-a-run").status_code == 401
    assert client.get("/runs/not-a-run",
                      headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/runs/not-a-run",
                      headers={"Authorization": "Bearer test-token-that-is-long-enough-123"}
                      ).status_code == 404
    monkeypatch.delenv("FIXORA_API_TOKEN")
    assert client.get("/runs/not-a-run").status_code == 404
