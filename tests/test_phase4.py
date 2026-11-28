"""Phase 4: failure-diagnosis integration and sandboxed dependency install.

Covers:
  * demos for the overlooked failure classes (syntax / import error) reaching
    awaiting_approval through the real graph with the offline mock provider;
  * install_dependencies installing only the repo's own declared dependencies
    into an isolated temp target, with the killer offline round-trip:
    failing tests -> diagnose dependency_missing -> install -> tests pass;
  * the tool's safety rails (no declarations, kill switch, specifier charset).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path

import pytest

from fixora.config import Settings
from fixora.demos import list_demos
from fixora.runner import RunManager
from fixora.sandbox import Sandbox, SandboxError
from fixora.tools import build_tools
from fixora.agents.common import RunContext

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


def _tools_for(tmp_path, source: Path, settings=None):
    """Sandbox copies the source into its own temp dir; no manual copy needed."""
    settings = settings or _settings()
    sandbox = Sandbox(Path(source))
    tools = build_tools(sandbox, settings)
    return sandbox, tools


# ------------------------------------------------------ new demos, offline

# The checked-in demo fixtures are intentionally broken, but they live in the
# working tree and anyone (a human runner, an agent) can repair them in place by
# running the offline demo with --yes. These integration tests therefore build
# their own broken copy: the fixture supplies the repo structure (tests, package,
# README), and the one buggy file is materialised here so the suite is green
# regardless of local edits. Keep the embedded broken content in sync with
# demo_repo_syntax / demo_repo_import at HEAD.
_BROKEN_PRICING_PY = (
    '"""Discount and formatting helpers."""\n'
    "\n"
    "\n"
    "def apply_discount(total, discount):\n"
    "    if discount < 0 or discount > 1:\n"
    '        raise ValueError("discount must be between 0 and 1")\n'
    "    return total * (1 - discount)\n"
    "\n"
    "\n"
    "def format_total(total):\n"
    '    return f"${total:.2f}\n'
)
_BROKEN_CATALOG_PY = (
    '"""Catalogue price and availability helpers."""\n'
    "from .inventory import stock_level\n"
    "\n"
    "\n"
    "def price(unit_price, quantity):\n"
    "    return round(unit_price * quantity, 2)\n"
    "\n"
    "\n"
    "def available(item):\n"
    "    return stock_level(item)\n"
)


def _broken_syntax_demo(tmp_path: Path) -> Path:
    src = REPO_ROOT / "demo_repo_syntax"
    dst = tmp_path / "repo"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    (dst / "skylark" / "pricing.py").write_text(_BROKEN_PRICING_PY, encoding="utf-8")
    return dst


def _broken_import_demo(tmp_path: Path) -> Path:
    src = REPO_ROOT / "demo_repo_import"
    dst = tmp_path / "repo"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    (dst / "meridian" / "catalog.py").write_text(_BROKEN_CATALOG_PY, encoding="utf-8")
    return dst


def test_syntax_error_demo_reaches_approval_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_PROVIDERS", "")
    source = _broken_syntax_demo(tmp_path)
    record = RunManager().run_blocking(str(source), "Fix the failing tests",
                                       max_attempts=3, printer=lambda *a, **k: None)
    assert record.status == "awaiting_approval"
    assert record.report.get("baseline_diagnosis") == "Syntax error"


def test_import_error_demo_reaches_approval_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_PROVIDERS", "")
    source = _broken_import_demo(tmp_path)
    record = RunManager().run_blocking(str(source), "Fix the failing tests",
                                       max_attempts=3, printer=lambda *a, **k: None)
    assert record.status == "awaiting_approval"
    assert record.report.get("baseline_diagnosis") == "Import error"
    assert record.report.get("attempts", 0) >= 2


def test_new_demos_are_registered():
    ids = {d["id"] for d in list_demos()}
    assert {"demo_repo", "demo_repo_hard", "demo_repo_syntax", "demo_repo_import"} <= ids


# ------------------------------------------- dependency install: safety

def test_install_dependencies_disabled_by_env(tmp_path):
    settings = _settings(allow_dependency_install=False)
    sandbox, tools = _tools_for(tmp_path, REPO_ROOT / "demo_repo", settings)
    try:
        out = json.loads(tools["install_dependencies"].run({}))
        assert out["ok"] is False and "disabled" in out["error"]
    finally:
        sandbox.cleanup()


def test_install_dependencies_without_declarations(tmp_path):
    empty = tmp_path / "repo"
    empty.mkdir()
    sandbox, tools = _tools_for(tmp_path, empty)
    try:
        out = json.loads(tools["install_dependencies"].run({}))
        assert out["ok"] is False and "nothing to install" in out["error"]
    finally:
        sandbox.cleanup()


def test_install_dependencies_pyproject_without_deps(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname = 'x'\nversion = '1.0.0'\n",
                                          encoding="utf-8")
    sandbox, tools = _tools_for(tmp_path, repo)
    try:
        out = json.loads(tools["install_dependencies"].run({}))
        assert out["ok"] is False and "no [project] dependencies" in out["error"]
    finally:
        sandbox.cleanup()


def test_specifier_validator_rejects_urls_and_markers(tmp_path):
    sandbox, _tools = _tools_for(tmp_path, REPO_ROOT / "demo_repo")
    try:
        for bad in ("git+https://evil.example/pkg",
                    "pkg @ https://evil.example/x.whl",
                    "pkg; python_version<'3.12'",
                    "pkg --index-url https://evil.example",
                    "pkg==",
                    "../local/path"):
            with pytest.raises(SandboxError):
                sandbox.pip_install_specifiers([bad], timeout=5)
    finally:
        sandbox.cleanup()


def test_install_tool_accepts_no_model_supplied_packages():
    """The LLM cannot name packages: the tool takes zero properties."""
    sandbox = Sandbox(REPO_ROOT / "demo_repo")
    try:
        tool = build_tools(sandbox, _settings())["install_dependencies"]
        assert tool.parameters.get("properties") == {}
        assert tool.parameters.get("required") == []
    finally:
        sandbox.cleanup()


# --------------------- dependency install: full offline round-trip with wheel

def _build_tiny_wheel(path: Path) -> None:
    files = {
        "tiny_example.py": b"VALUE = 42\n",
        "tiny_example-0.1.0.dist-info/METADATA":
            b"Metadata-Version: 2.1\nName: tiny-example\nVersion: 0.1.0\nSummary: fixture\n",
        "tiny_example-0.1.0.dist-info/WHEEL":
            b"Wheel-Version: 1.0\nGenerator: manual\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }

    def record_line(name: str, data: bytes) -> str:
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        return f"{name},sha256={digest},{len(data)}"

    lines = [record_line(name, data) for name, data in files.items()]
    lines.append("tiny_example-0.1.0.dist-info/RECORD,,")
    files["tiny_example-0.1.0.dist-info/RECORD"] = ("\n".join(lines) + "\n").encode()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)


def test_failing_tests_diagnosed_then_fixed_by_install(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    wheel_name = "tiny_example-0.1.0-py3-none-any.whl"
    _build_tiny_wheel(repo / wheel_name)
    (repo / "requirements.txt").write_text(f"./{wheel_name}\n", encoding="utf-8")
    (repo / "test_tiny.py").write_text(
        "import tiny_example\n\n\ndef test_value():\n    assert tiny_example.VALUE == 42\n",
        encoding="utf-8")
    sandbox, tools = _tools_for(tmp_path, repo)
    try:
        before = json.loads(tools["run_tests"].run({}))
        assert before["status"] == "failed"
        assert before["diagnosis"]["category"] == "dependency_missing"
        assert before["diagnosis"]["module"] == "tiny_example"

        installed = json.loads(tools["install_dependencies"].run({}))
        assert installed["ok"] is True
        assert installed["installed_from"] == "requirements.txt"

        after = json.loads(tools["run_tests"].run({}))
        assert after["status"] == "passed" and after["passed"] == 1
        # isolated target: deps live next to the copied repo, never inside it
        assert not (Path(sandbox.workspace) / "tiny_example.py").exists()
    finally:
        sandbox.cleanup()
