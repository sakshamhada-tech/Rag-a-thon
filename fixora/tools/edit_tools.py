"""Edit tools: write_file, apply_patch. Exact-match patching that fails when
the target string is not found or is found more than once.
"""
from __future__ import annotations

import difflib
import json


def _err(msg: str) -> str:
    return json.dumps({"ok": False, "error": msg})


def write_file(sandbox, path: str, content: str) -> str:
    try:
        target = sandbox.resolve(path)
    except Exception as e:
        return _err(str(e))
    if target == sandbox.workspace:
        return _err("refusing to overwrite the workspace root")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as e:
        return _err(f"cannot write {path}: {e}")
    return json.dumps({"ok": True, "path": sandbox.rel(target), "bytes": len(content.encode())})


def apply_patch(sandbox, path: str, old_str: str, new_str: str) -> str:
    """Replace exactly one occurrence of old_str with new_str.

    Fails (returns an error payload, file untouched) when old_str occurs zero
    times or more than once — the agent must provide a unique anchor.
    """
    if not old_str:
        return _err("old_str must not be empty")
    try:
        target = sandbox.resolve(path)
    except Exception as e:
        return _err(str(e))
    if not target.is_file():
        return _err(f"not a file: {path}")
    try:
        content = target.read_text(encoding="utf-8")
    except OSError as e:
        return _err(f"cannot read {path}: {e}")
    count = content.count(old_str)
    if count == 0:
        return _err(f"old_str not found in {sandbox.rel(target)}")
    if count > 1:
        return _err(f"old_str found {count} times in {sandbox.rel(target)}; "
                    "provide a longer, unique anchor")
    updated = content.replace(old_str, new_str, 1)
    target.write_text(updated, encoding="utf-8")
    diff = "".join(difflib.unified_diff(
        content.splitlines(keepends=True), updated.splitlines(keepends=True),
        fromfile=f"a/{sandbox.rel(target)}", tofile=f"b/{sandbox.rel(target)}", n=1))
    return json.dumps({"ok": True, "path": sandbox.rel(target), "diff": diff[:4000]})
