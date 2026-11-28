"""Built-in demo repositories.

Demos are copied to a temporary directory so an approval can export a patch
without ever mutating the checked-in fixtures.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from .github import RepoInputError
from .security import is_sensitive_path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEMOS = [
    {
        "id": "demo_repo",
        "name": "SwiftCart",
        "description": "One planted rounding bug. Baseline: 3 failing tests.",
    },
    {
        "id": "demo_repo_hard",
        "name": "Ledgerline",
        "description": "Two related bugs across money.py and discounts.py. Baseline: 5 failing tests.",
    },
    {
        "id": "demo_repo_syntax",
        "name": "Skylark",
        "description": "Syntax error: unterminated f-string blocks import and test collection. Baseline: 0 passed, collection blocked.",
    },
    {
        "id": "demo_repo_import",
        "name": "Meridian",
        "description": "Import error: wrong name imported from an internal module. Baseline: collection blocked; one-line import fix.",
    },
]

_SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules"}


def list_demos() -> list[dict]:
    return [dict(demo) for demo in DEMOS if (PROJECT_ROOT / demo["id"]).is_dir()]


def prepare_demo(demo_id: str) -> tuple[Path, str]:
    demo_id = (demo_id or "").strip()
    demo = next((demo for demo in DEMOS if demo["id"] == demo_id), None)
    source = PROJECT_ROOT / demo["id"] if demo else None
    if source is None or not source.is_dir():
        raise RepoInputError(f"unknown demo: {demo_id!r}")

    tmp_root = Path(tempfile.mkdtemp(prefix="fixora-demo-"))
    workspace = tmp_root / demo["id"]

    def ignore(directory, names):
        return {name for name in names
                if name in _SKIP_DIRS or is_sensitive_path(Path(directory) / name)}

    shutil.copytree(source, workspace, ignore=ignore)
    return workspace, f"demo:{demo_id}"
