"""Public GitHub repo input for the API.

Only https://github.com/<owner>/<repo> URLs are accepted — no credentials,
hosts, file:// URLs, SSH or local paths. Repos are shallow-cloned (depth 1)
into the sandbox temp area with a timeout and a size cap.
"""
from __future__ import annotations

import re
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

_GITHUB_RE = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})?)/(?P<repo>[A-Za-z0-9._-]{1,100})(?:\.git)?/?$")


class RepoInputError(Exception):
    """Raised when a repo input fails validation or cloning."""


def validate_github_url(url: str) -> str:
    """Return a normalized https://github.com/owner/repo URL, or raise."""
    if not isinstance(url, str):
        raise RepoInputError("repo must be a string")
    url = url.strip()
    if not url:
        raise RepoInputError("repo URL is required")
    if any(ch in url for ch in ("@", "://", "\n", "\r", " ")) and not url.startswith("https://github.com/"):
        raise RepoInputError("only https://github.com/<owner>/<repo> URLs are accepted")
    match = _GITHUB_RE.match(url)
    if not match:
        raise RepoInputError(
            "only public GitHub URLs are accepted, e.g. https://github.com/owner/repo")
    repo = match.group("repo")
    if repo in {".", ".."} or repo.startswith("-"):
        raise RepoInputError("invalid repo name")
    return f"https://github.com/{match.group('owner')}/{repo}"


def is_github_url(value: str) -> bool:
    try:
        validate_github_url(value)
        return True
    except RepoInputError:
        return False


def clone_public_repo(url: str, *, clone_timeout: int = 120, max_repo_mb: int = 50) -> Path:
    """Shallow-clone a validated public repo; returns the clone path."""
    normalized = validate_github_url(url)
    dest = Path(tempfile.mkdtemp(prefix="devmate-github-")) / "repo"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", "--quiet", normalized, str(dest)],
            capture_output=True, text=True, timeout=clone_timeout, shell=False)
    except subprocess.TimeoutExpired:
        shutil.rmtree(dest.parent, ignore_errors=True)
        raise RepoInputError(f"clone timed out after {clone_timeout}s") from None
    if proc.returncode != 0:
        shutil.rmtree(dest.parent, ignore_errors=True)
        msg = (proc.stderr or proc.stdout or "git clone failed").strip().splitlines()
        raise RepoInputError(f"clone failed: {msg[-1] if msg else 'unknown error'}")
    size_mb = _dir_size_mb(dest)
    if size_mb > max_repo_mb:
        shutil.rmtree(dest.parent, ignore_errors=True)
        raise RepoInputError(f"repo is {size_mb:.1f} MB, over the {max_repo_mb} MB limit")
    # .git metadata is not needed for analysis; drop it to keep the sandbox small.
    shutil.rmtree(dest / ".git", ignore_errors=True)
    return dest


def _dir_size_mb(path: Path) -> float:
    total = 0
    for dirpath, _, files in os.walk(path):
        for name in files:
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                continue
    return total / (1024 * 1024)
