"""Unit tests for sandbox path-escape protection and command allowlist."""
import os
import sys

import pytest

from devmate.sandbox import Sandbox, SandboxError


@pytest.fixture
def repo(tmp_path):
    repo_dir = tmp_path / "demo"
    (repo_dir / "pkg").mkdir(parents=True)
    (repo_dir / "pkg" / "mod.py").write_text("X = 1\n")
    (repo_dir / "tests").mkdir()
    (repo_dir / "tests" / "test_x.py").write_text("def test_x():\n    assert True\n")
    return repo_dir


@pytest.fixture
def sandbox(repo):
    sb = Sandbox(repo)
    yield sb
    sb.cleanup()


def test_original_repo_is_copied_not_modified(sandbox, repo):
    assert (sandbox.workspace / "pkg" / "mod.py").read_text() == "X = 1\n"
    assert sandbox.workspace != repo


def test_resolve_allows_normal_relative_path(sandbox):
    resolved = sandbox.resolve("pkg/mod.py")
    assert resolved.is_file()
    assert str(resolved).startswith(str(sandbox.workspace))


def test_resolve_allows_dot(sandbox):
    assert sandbox.resolve(".") == sandbox.workspace


def test_resolve_blocks_parent_escape(sandbox):
    with pytest.raises(SandboxError):
        sandbox.resolve("../outside.py")


def test_resolve_blocks_deep_parent_escape(sandbox):
    with pytest.raises(SandboxError):
        sandbox.resolve("pkg/../../outside.py")


def test_resolve_blocks_absolute_path(sandbox):
    with pytest.raises(SandboxError):
        sandbox.resolve("/etc/passwd")


def test_resolve_blocks_null_byte(sandbox):
    with pytest.raises(SandboxError):
        sandbox.resolve("pkg/\x00mod.py")


def test_resolve_blocks_symlink_escape(sandbox, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("top secret")
    link = sandbox.workspace / "link.txt"
    os.symlink(outside, link)
    with pytest.raises(SandboxError):
        sandbox.resolve("link.txt")


def test_resolve_blocks_symlink_dir_escape(sandbox, tmp_path):
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "evil.py").write_text("Y = 2\n")
    os.symlink(outside_dir, sandbox.workspace / "dlink")
    with pytest.raises(SandboxError):
        sandbox.resolve("dlink/evil.py")


def test_run_rejects_non_python_commands(sandbox):
    with pytest.raises(SandboxError):
        sandbox.run(["ls", "-la"])


def test_run_rejects_disallowed_module(sandbox):
    with pytest.raises(SandboxError):
        sandbox.run(["python", "-m", "pip", "install", "evil"])


def test_run_rejects_shell_metachar_arguments(sandbox):
    with pytest.raises(SandboxError):
        sandbox.run(["python", "-m", "pytest", ";", "rm", "-rf", "/"])


def test_run_rejects_path_escape_argument(sandbox):
    with pytest.raises(SandboxError):
        sandbox.run(["python", "-m", "pytest", "../../outside"])


def test_run_pytest_executes_inside_workspace(sandbox):
    result = sandbox.run(["python", "-m", "pytest", "-q"], timeout=60)
    assert result.returncode == 0
    assert "1 passed" in result.combined


def test_diff_reports_changes(sandbox, repo):
    target = sandbox.resolve("pkg/mod.py")
    target.write_text("X = 2\n")
    diff = sandbox.diff()
    assert "-X = 1" in diff and "+X = 2" in diff


def test_diff_empty_when_no_changes(sandbox):
    assert sandbox.diff() == ""
