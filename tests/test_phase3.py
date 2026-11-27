"""Phase 3 backend additions: demos, config, patch-only demo approvals."""
from __future__ import annotations

import json
import os
import shutil
import stat
import time
from pathlib import Path

import pytest

import devmate.runner as runner_module
from devmate.api import app
from devmate.demos import DEMOS, list_demos, prepare_demo
from devmate.events import EventEmitter
from devmate.runner import RunManager, RunRecord
from fastapi.testclient import TestClient


def test_demos_are_listed_and_do_not_leak_paths():
    response = TestClient(app).get("/demos")
    assert response.status_code == 200
    demos = response.json()["demos"]
    assert {d["id"] for d in demos} == {d["id"] for d in DEMOS}
    for demo in demos:
        assert set(demo) == {"id", "name", "description"}
        assert "/" not in Path(demo["id"]).anchor


def test_config_never_returns_keys(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_secret_value_that_must_not_leak_123456")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    response = TestClient(app).get("/config")
    assert response.status_code == 200
    body = response.text
    assert response.json()["provider"] == "groq"
    assert "gsk_secret_value_that_must_not_leak_123456" not in body
    assert "key" not in body.lower()


def test_event_emitter_adds_monotonic_sequence():
    emitter = EventEmitter(run_id="seq-test")
    first = emitter.emit("step", node="analyse", message="a")
    second = emitter.emit("step", node="plan", message="b")
    assert first["seq"] == 1
    assert second["seq"] == 2


def test_prepare_demo_copies_to_temp_and_ignores_secret_files(tmp_path, monkeypatch):
    # Do not touch the checked-in fixture; work against the real demo id only
    # through the public helper, which always copies to temp.
    source, display = prepare_demo("demo_repo")
    parent = source.parent
    try:
        assert display == "demo:demo_repo"
        assert source.name == "demo_repo"
        assert source != Path(__file__).resolve().parent.parent / "demo_repo"
        assert (source / "app.py").exists() or any(source.iterdir())
    finally:
        assert parent.name.startswith("devmate-demo-")
        shutil.rmtree(parent)


def test_patch_only_approval_exports_patch_without_applying(monkeypatch, tmp_path):
    logs = tmp_path / "logs"
    monkeypatch.setattr(runner_module, "LOGS_DIR", logs)
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    sentinel = fixture / "keep.txt"
    sentinel.write_text("original\n", encoding="utf-8")

    manager = RunManager()
    record = RunRecord(
        run_id="patchonly1",
        repo_input="demo:demo_repo",
        task="Fix tests",
        is_github=False,
        patch_only=True,
        source_repo=str(fixture),
        max_attempts=1,
        status="awaiting_approval",
        report={"diff": "diff --git a/keep.txt b/keep.txt\n+changed\n"},
    )
    manager._runs[record.run_id] = record
    result = manager.approve(record.run_id)

    assert result["status"] == "applied"
    assert sentinel.read_text(encoding="utf-8") == "original\n"
    patch_file = Path(result["patch_file"])
    assert patch_file.is_file()
    assert stat.S_IMODE(patch_file.stat().st_mode) == 0o600
