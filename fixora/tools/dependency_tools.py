"""Dependency installation tool.

Installs exactly what the repository declares — never packages the model names:

  1. requirements.txt (workspace root)  ->  pip install --target <isolated> -r ...
  2. pyproject.toml [project] dependencies -> specifiers are validated (name,
     extras and version constraints only; no URLs/markers) and installed the
     same way.

Installed into <sandbox-root>/fixora-deps — a temp directory next to the
copied repo, never the host interpreter's environment, and never inside the
repo copy (so it cannot leak into the final diff). pytest runs pick the
directory up via PYTHONPATH. The kill switch FIXORA_ALLOW_DEPENDENCY_INSTALL=0
disables the tool entirely.
"""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

from ..config import MAX_OUTPUT_CHARS


def _tail(text: str, limit: int = 1200) -> str:
    text = text or ""
    return text if len(text) <= limit else text[-limit:]


def _pyproject_dependencies(path: Path):
    """Return list of declared deps, [] if none, None if file absent/unparseable."""
    if not path.is_file():
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    project = data.get("project") or {}
    deps = [str(d) for d in (project.get("dependencies") or [])]
    return deps


def install_dependencies(sandbox, settings) -> str:
    if not getattr(settings, "allow_dependency_install", True):
        return json.dumps({"ok": False,
                           "error": "dependency installation is disabled "
                                    "(FIXORA_ALLOW_DEPENDENCY_INSTALL=0)"})
    timeout = int(getattr(settings, "install_timeout", 300))
    workspace = Path(sandbox.workspace)

    req = workspace / "requirements.txt"
    if req.is_file():
        try:
            result = sandbox.pip_install_target("requirements.txt", timeout=timeout)
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)})
        return _result_payload(result, source="requirements.txt")

    deps = _pyproject_dependencies(workspace / "pyproject.toml")
    if deps is None:
        return json.dumps({"ok": False,
                           "error": "nothing to install: no requirements.txt and no "
                                    "parseable pyproject.toml [project] dependencies"})
    if not deps:
        return json.dumps({"ok": False,
                           "error": "nothing to install: pyproject.toml declares no "
                                    "[project] dependencies"})
    try:
        result = sandbox.pip_install_specifiers(deps, timeout=timeout)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})
    return _result_payload(result, source="pyproject.toml")


def _dir_size_mb(path: str) -> float:
    total = 0
    try:
        for p in Path(path).rglob("*"):
            try:
                total += p.stat().st_size if p.is_file() else 0
            except OSError:
                pass
    except OSError:
        pass
    return round(total / (1024 * 1024), 1)


def _size_limit_mb() -> float:
    import os
    try:
        return max(1.0, float(os.environ.get("FIXORA_INSTALL_MAX_MB", "512") or 512))
    except ValueError:
        return 512.0


def _result_payload(result, source: str) -> str:
    tail = _tail((result.stdout or "") + (("\n--- stderr ---\n" + result.stderr) if result.stderr else ""))
    if result.timed_out:
        return json.dumps({"ok": False, "error": "pip install timed out",
                           "installed_from": source, "output_tail": tail[-MAX_OUTPUT_CHARS:]})
    if result.returncode != 0:
        return json.dumps({"ok": False,
                           "error": f"pip install exited with code {result.returncode}",
                           "installed_from": source,
                           "output_tail": tail[-MAX_OUTPUT_CHARS:]})
    try:
        target = str(result.argv[result.argv.index("--target") + 1])
    except (ValueError, IndexError):
        target = "<isolated dir>"
    size_mb = _dir_size_mb(target)
    limit = _size_limit_mb()
    if size_mb > limit:
        return json.dumps({"ok": False,
                           "error": f"installed dependencies exceed the size limit ({size_mb} MB > {limit} MB); "
                                    "the temp dir is discarded with this run",
                           "installed_from": source, "installed_mb": size_mb, "limit_mb": limit,
                           "output_tail": tail[-1200:]})
    return json.dumps({"ok": True,
                       "installed_from": source,
                       "target": target,
                       "installed_mb": size_mb,
                       "limit_mb": limit,
                       "note": "installed into an isolated temp dir; pytest runs pick it up via PYTHONPATH; the repo itself is unchanged",
                       "output_tail": tail[-1200:]})
