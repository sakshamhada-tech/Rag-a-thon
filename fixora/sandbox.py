"""Workspace sandbox: copies the target repo to a temp dir and confines all
file access and command execution to it.

Guarantees:
  * the original repo is never modified (all work happens on a fresh copy);
  * every LLM-supplied path is resolved and verified to stay inside the
    workspace (blocks `..`, absolute paths, and symlink escapes);
  * commands run via subprocess without a shell, with a timeout, captured and
    truncated output, and an allowlist (`python -m pytest` style only);
  * a unified diff between the original repo and the workspace can be produced.
"""
from __future__ import annotations

import difflib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .config import MAX_OUTPUT_CHARS
from .security import is_sensitive_path, redact_text

_SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", ".pytest_cache",
              ".venv", "venv", "node_modules", ".mypy_cache", ".ruff_cache",
              ".ssh", ".aws", ".azure", ".gnupg", ".kube", ".docker", ".secrets"}

# Subprocesses spawned inside the sandbox get a minimal, allowlisted
# environment instead of the host's — host API keys/secrets must never be
# visible to repo code (pytest collects and executes it).
_ENV_PASSTHROUGH = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP",
                    "SYSTEMROOT", "PATHEXT", "COMSPEC",
                    "SSL_CERT_FILE", "SSL_CERT_DIR",
                    "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")


def _minimal_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {key: os.environ[key] for key in _ENV_PASSTHROUGH if key in os.environ}
    env.setdefault("PATH", os.defpath)
    env["PYTHONUNBUFFERED"] = "1"
    # Never let stale bytecode shadow freshly patched sources: agent edits can
    # land within the same (uint32-truncated) mtime second as the last pytest
    # run with identical file size — cpython would then reuse the old .pyc and
    # test the wrong code.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if extra:
        env.update(extra)
    return env


class SandboxError(Exception):
    """Raised when a path escapes the workspace or a command is not allowed."""


@dataclass
class CommandResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def combined(self) -> str:
        out = self.stdout
        if self.stderr:
            out += ("\n--- stderr ---\n" if out else "") + self.stderr
        return out


@dataclass
class Sandbox:
    source_repo: Path
    root: Path = field(init=False)          # workspace root (temp copy)

    def __post_init__(self):
        self.source_repo = Path(self.source_repo).resolve()
        if not self.source_repo.is_dir():
            raise SandboxError(f"Target repo not found or not a directory: {self.source_repo}")
        self.root = Path(tempfile.mkdtemp(prefix="fixora-workspace-")).resolve()
        workspace_copy = self.root / self.source_repo.name

        def ignore(directory, names):
            return {name for name in names
                    if name in _SKIP_DIRS or is_sensitive_path(Path(directory) / name)}

        shutil.copytree(self.source_repo, workspace_copy,
                        ignore=ignore)
        self._workspace_copy = workspace_copy

    # ---------------------------------------------------------- paths
    @property
    def workspace(self) -> Path:
        """Directory the agent actually works in (the copied repo)."""
        return self._workspace_copy

    def resolve(self, path: str) -> Path:
        """Resolve an LLM-supplied relative path inside the workspace.

        Rejects absolute paths, `..` escapes, symlink escapes and null bytes.
        Returns the verified absolute Path (which may or may not exist yet).
        """
        if path is None:
            raise SandboxError("path is required")
        if "\x00" in path:
            raise SandboxError("path contains a null byte")
        if os.path.isabs(path):
            raise SandboxError(f"absolute paths are not allowed: {path!r}")
        if is_sensitive_path(path):
            raise SandboxError("sensitive env/credential files are not accessible in the sandbox")
        candidate = self.workspace / path
        resolved = Path(os.path.realpath(candidate))  # resolves symlinks
        ws_real = Path(os.path.realpath(self.workspace))
        if resolved != ws_real and ws_real not in resolved.parents:
            raise SandboxError(f"path escapes the workspace: {path!r}")
        if is_sensitive_path(resolved):
            raise SandboxError("sensitive env/credential files are not accessible in the sandbox")
        return resolved

    @property
    def deps_dir(self) -> Path:
        """Isolated target for sandboxed `pip --target` installs.

        Lives next to (not inside) the copied repo so installed packages never
        leak into the final diff; removed with the sandbox on cleanup.
        """
        target = self.root / "fixora-deps"
        target.mkdir(parents=True, exist_ok=True)
        return target

    def has_installed_deps(self) -> bool:
        target = self.root / "fixora-deps"
        try:
            return any(target.iterdir())
        except OSError:
            return False

    def pip_install_specifiers(self, specifiers: list[str], timeout: int = 300) -> CommandResult:
        """Install declared dependency specifiers (from pyproject [project]).

        Specifiers originate from the repo's own pyproject.toml — never from
        the model — and are charset-validated before reaching pip. Pure
        specifier installs run no repo build code.
        """
        # PEP 508-shaped, deliberately narrow: name[extras] (op version)* —
        # no markers, no URLs, no direct references, no environment overriding.
        name = r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?"
        version = r"[0-9][0-9A-Za-z.*+!]{0,40}"
        constraint = rf"(?:==|>=|<=|!=|~=|>|<){version}"
        safe = re.compile(rf"^{name}(\[[A-Za-z0-9,_-]{{1,64}}\])?({constraint}(,{constraint})*)?$")
        for spec in specifiers:
            cleaned = spec.strip()
            if len(cleaned) > 140 or not safe.match(cleaned):
                raise SandboxError(f"unsafe dependency specifier: {spec!r}")
        target = self.deps_dir
        argv = [sys.executable, "-m", "pip", "install",
                "--no-input", "--disable-pip-version-check", "--no-cache-dir",
                "--target", str(target), *[s.strip() for s in specifiers]]
        try:
            proc = subprocess.run(argv, cwd=self.workspace, capture_output=True, text=True,
                                  timeout=timeout, shell=False, env=_minimal_env())
            return CommandResult(argv=argv[1:], returncode=proc.returncode,
                                 stdout=_trunc(redact_text(proc.stdout)),
                                 stderr=_trunc(redact_text(proc.stderr)))
        except subprocess.TimeoutExpired as e:
            return CommandResult(argv=argv[1:], returncode=-1,
                                 stdout=_trunc(redact_text(e.stdout or "")),
                                 stderr=_trunc(redact_text(e.stderr or "")),
                                 timed_out=True)

    def pip_install_target(self, requirements_rel: str, timeout: int = 300) -> CommandResult:
        """Install the repo's own declared requirements into deps_dir.

        Only `-r <file>` installs are allowed — the LLM cannot name packages.
        The file must live inside the workspace. Runs with a minimal env and
        no console-script cache; output is redacted and truncated.
        """
        req = self.resolve(requirements_rel)
        if not req.is_file():
            raise SandboxError(f"not a file: {requirements_rel}")
        target = self.deps_dir
        argv = [sys.executable, "-m", "pip", "install",
                "--no-input", "--disable-pip-version-check", "--no-cache-dir",
                "--target", str(target), "-r", str(req)]
        try:
            proc = subprocess.run(argv, cwd=self.workspace, capture_output=True, text=True,
                                  timeout=timeout, shell=False, env=_minimal_env())
            return CommandResult(argv=argv[1:], returncode=proc.returncode,
                                 stdout=_trunc(redact_text(proc.stdout)),
                                 stderr=_trunc(redact_text(proc.stderr)))
        except subprocess.TimeoutExpired as e:
            return CommandResult(argv=argv[1:], returncode=-1,
                                 stdout=_trunc(redact_text(e.stdout or "")),
                                 stderr=_trunc(redact_text(e.stderr or "")),
                                 timed_out=True)

    def invalidate_bytecode(self) -> int:
        """Remove __pycache__ dirs from the workspace; returns count removed."""
        removed = 0
        for dirpath, dirs, _files in os.walk(self.workspace):
            for name in list(dirs):
                if name == "__pycache__":
                    shutil.rmtree(Path(dirpath) / name, ignore_errors=True)
                    dirs.remove(name)
                    removed += 1
        return removed

    def rel(self, resolved: Path) -> str:
        return str(resolved.relative_to(Path(os.path.realpath(self.workspace))))

    # ---------------------------------------------------------- commands
    _ALLOWED_MODULES = {"pytest", "py_compile", "unittest"}
    _ARG_SAFE = re.compile(r"^[\w./:=+\-]+$")

    def run(self, argv: list[str], timeout: int = 60,
            extra_env: dict[str, str] | None = None) -> CommandResult:
        """Run an allowlisted command inside the workspace (no shell)."""
        if not argv or argv[0] != "python":
            raise SandboxError(f"command not allowed (must start with 'python'): {argv!r}")
        if len(argv) < 3 or argv[1] != "-m" or argv[2] not in self._ALLOWED_MODULES:
            raise SandboxError(
                f"command not allowed (only 'python -m pytest|py_compile|unittest'): {argv!r}")
        for a in argv[2:]:
            if not self._ARG_SAFE.match(a):
                raise SandboxError(f"unsafe argument: {a!r}")
        # Defense in depth: any path-ish argument must stay in the workspace.
        for a in argv[2:]:
            if "/" in a or "." in a:
                try:
                    self.resolve(a)
                except SandboxError:
                    raise SandboxError(f"argument escapes the workspace: {a!r}") from None
        try:
            proc = subprocess.run(
                [sys.executable, *argv[1:]], cwd=self.workspace,
                capture_output=True, text=True, timeout=timeout, shell=False,
                env=_minimal_env(extra_env))
            return CommandResult(argv=argv, returncode=proc.returncode,
                                 stdout=_trunc(redact_text(proc.stdout)),
                                 stderr=_trunc(redact_text(proc.stderr)))
        except subprocess.TimeoutExpired as e:
            return CommandResult(argv=argv, returncode=-1,
                                 stdout=_trunc(redact_text(e.stdout or "")),
                                 stderr=_trunc(redact_text(e.stderr or "")),
                                 timed_out=True)

    # ---------------------------------------------------------- diff
    def diff(self, max_chars: int = 20000) -> str:
        """Unified diff between the original repo and the workspace copy."""
        original_files = _snapshot(self.source_repo)
        workspace_files = _snapshot(Path(os.path.realpath(self.workspace)))
        chunks: list[str] = []
        for rel in sorted(original_files.keys() | workspace_files.keys()):
            old = original_files.get(rel)
            new = workspace_files.get(rel)
            if old == new:
                continue
            diff = difflib.unified_diff(
                (old or "").splitlines(keepends=True),
                (new or "").splitlines(keepends=True),
                fromfile=f"a/{rel}", tofile=f"b/{rel}")
            chunks.append("".join(diff))
        text = "".join(chunks)
        return _trunc(text, max_chars)

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


def _trunc(s, limit=MAX_OUTPUT_CHARS):
    if isinstance(s, bytes):
        s = s.decode("utf-8", errors="replace")
    return s if len(s) <= limit else s[:limit] + f"\n...[truncated {len(s) - limit} chars]"


def _snapshot(base: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for dirpath, dirs, names in os.walk(base):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not is_sensitive_path(Path(dirpath) / d)]
        for name in names:
            if name.endswith((".pyc", ".pyo")) or is_sensitive_path(Path(dirpath) / name):
                continue
            full = Path(dirpath) / name
            rel = str(full.relative_to(base))
            try:
                files[rel] = full.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return files
