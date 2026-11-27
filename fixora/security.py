"""Secret handling shared by sandbox, events, logs and printing.

This module is defense in depth. It does not replace keeping credentials out of
repos; it makes accidental leaks harder to cross process/log/terminal
boundaries.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_SECRET_NAME_SUFFIXES = (
    "_API_KEY", "_TOKEN", "_SECRET", "_PRIVATE_KEY", "_PASSWORD", "_CREDENTIALS",
)

_REDACT_PATTERNS = [
    re.compile(r"gsk_[A-Za-z0-9_-]{8,}"),
    re.compile(r"sk-(?:ant-)?[A-Za-z0-9_-]{8,}"),
    re.compile(r"xai-[A-Za-z0-9_-]{8,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{8,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{10,}"),
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|auth[_-]?token|secret|password)"
               r"\s*[:=]\s*[^\s,;'\"]{8,}"),
]

_SENSITIVE_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".env.staging",
    ".env.test",
    ".env.preview",
    ".envrc",
    ".netrc",
    ".pypirc",
    ".npmrc",
    ".git-credentials",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "id_dsa",
    "credentials",
    "credentials.json",
    "service-account.json",
    "gcp-credentials.json",
}
_SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore")
_SENSITIVE_DIR_PARTS = {".ssh", ".aws", ".azure", ".gnupg", ".kube", ".docker", ".secrets"}


def is_sensitive_path(path: str | Path) -> bool:
    """Return True for env/credential files that agents must not read or edit."""
    normalized = str(path).replace("\\", "/")
    parts = [part.lower() for part in normalized.split("/") if part not in ("", ".")]
    name = parts[-1] if parts else ""

    if any(part in _SENSITIVE_DIR_PARTS for part in parts):
        return True
    if name in _SENSITIVE_FILE_NAMES:
        return True
    return name.endswith(_SENSITIVE_SUFFIXES)


def redact_text(text) -> str:
    if text is None:
        return ""
    redacted = str(text)
    for name, value in os.environ.items():
        upper = name.upper()
        if not (upper.endswith(_SECRET_NAME_SUFFIXES) or upper in {"LLM_PROVIDERS", "FIXORA_API_TOKEN"}):
            continue
        if value and len(value) >= 8:
            redacted = redacted.replace(value, "[REDACTED]")
    for pattern in _REDACT_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def redact_value(value):
    if isinstance(value, dict):
        return {str(k): redact_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
