"""Unit tests for the edit tools: apply_patch uniqueness and write_file."""
import json

import pytest

from devmate.sandbox import Sandbox
from devmate.tools import edit_tools


@pytest.fixture
def sandbox(tmp_path):
    repo_dir = tmp_path / "demo"
    repo_dir.mkdir()
    (repo_dir / "mod.py").write_text("VALUE = 1\nOTHER = 1\n")
    (repo_dir / "multi.py").write_text("word\nword\nunique\n")
    sb = Sandbox(repo_dir)
    yield sb
    sb.cleanup()


def test_apply_patch_unique_match(sandbox):
    out = json.loads(edit_tools.apply_patch(sandbox, "mod.py", "VALUE", "RENAMED"))
    assert out["ok"] is True
    assert "RENAMED = 1" in (sandbox.workspace / "mod.py").read_text()


def test_apply_patch_fails_when_not_found(sandbox):
    before = (sandbox.workspace / "mod.py").read_text()
    out = json.loads(edit_tools.apply_patch(sandbox, "mod.py", "NOPE", "X"))
    assert out["ok"] is False
    assert "not found" in out["error"]
    assert (sandbox.workspace / "mod.py").read_text() == before  # untouched


def test_apply_patch_fails_when_not_unique(sandbox):
    before = (sandbox.workspace / "multi.py").read_text()
    out = json.loads(edit_tools.apply_patch(sandbox, "multi.py", "word", "X"))
    assert out["ok"] is False
    assert "2 times" in out["error"]
    assert (sandbox.workspace / "multi.py").read_text() == before  # untouched


def test_apply_patch_unique_anchor_in_multi_file(sandbox):
    out = json.loads(edit_tools.apply_patch(sandbox, "multi.py", "unique", "changed"))
    assert out["ok"] is True
    assert "changed" in (sandbox.workspace / "multi.py").read_text()


def test_apply_patch_empty_old_str_rejected(sandbox):
    out = json.loads(edit_tools.apply_patch(sandbox, "mod.py", "", "X"))
    assert out["ok"] is False


def test_apply_patch_path_escape_rejected(sandbox):
    out = json.loads(edit_tools.apply_patch(sandbox, "../escape.py", "a", "b"))
    assert out["ok"] is False


def test_write_file_creates_file_and_parents(sandbox):
    out = json.loads(edit_tools.write_file(sandbox, "pkg/new/file.py", "print('hi')\n"))
    assert out["ok"] is True
    assert (sandbox.workspace / "pkg" / "new" / "file.py").read_text() == "print('hi')\n"


def test_write_file_path_escape_rejected(sandbox):
    out = json.loads(edit_tools.write_file(sandbox, "/tmp/evil.py", "x"))
    assert out["ok"] is False
