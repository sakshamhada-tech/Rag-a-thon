"""Cross-run memory: a small JSON file of lessons from completed runs.

Free, no database, no service. Lessons are retrieved with the same local
embedder used for RAG (hash by default; fastembed if available) and injected
into analyse as clearly-labelled hints — never as ground truth and never
overriding the human approval gate. Saving and recall are best-effort: any
failure is logged and ignored.

FIXORA_MEMORY=0 disables load AND save. FIXORA_MEMORY_PATH overrides the file
location (default logs/memory.json; capped at 50 lessons, FIFO).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .security import redact_text

_MAX_LESSONS = 50
_SNIPPET = 160


def _path() -> Path:
    return Path(os.environ.get("FIXORA_MEMORY_PATH", "logs/memory.json"))


def enabled() -> bool:
    return (os.environ.get("FIXORA_MEMORY", "1") or "1").strip().lower() not in ("0", "false", "off", "no")


def load_lessons() -> list[dict]:
    if not enabled():
        return []
    p = _path()
    try:
        if not p.exists():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_lessons(lessons: list[dict]):
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(lessons[-_MAX_LESSONS:], indent=1), encoding="utf-8")


def _clip(text: str, limit: int = _SNIPPET) -> str:
    text = redact_text(str(text or "")).replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def save_run(repo_name: str, task: str, report: dict):
    """Persist one lesson from a completed run (best effort, never raises)."""
    if not enabled():
        return None
    try:
        status = str(report.get("status", "unknown"))
        tests_after = report.get("tests_after") or {}
        diag = report.get("baseline_diagnosis") or ""
        failing = (report.get("tests_before") or {}).get("failing_tests") or []
        failing = failing[:2]
        files = []
        for e in report.get("edits_made", []):
            p = str(e.get("path", ""))
            if p and p not in files:
                files.append(p)
        plan = str(report.get("plan") or "")
        root_cause = _clip(plan.splitlines()[0] if plan else "not recorded", 140)
        fix_summary = f"{len(report.get('edits_made', []))} edit(s) across {len(files)} file(s): {', '.join(files[:4])}"
        lesson = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "repo": _clip(repo_name, 80),
            "symptom": _clip(f"{diag}: {', '.join(failing)}" if diag else task, 160),
            "root_cause": root_cause,
            "fix_summary": _clip(fix_summary, 200),
            "outcome": _clip(f"{status}; tests_after: {tests_after.get('passed', '?')} passed / {tests_after.get('failed', '?')} failed", 160),
            "task": _clip(task, 160),
        }
        lessons = load_lessons()
        lessons.append(lesson)
        _save_lessons(lessons)
        return lesson
    except Exception:
        return None


def _cosine(a, b):
    return sum(x * y for x, y in zip(a, b))


def relevant_hints(state: dict, k: int = 2, min_score: float = 0.15):
    """Return up to k lessons relevant to this run (empty list on any failure)."""
    if not enabled():
        return []
    lessons = load_lessons()
    if not lessons:
        return []
    try:
        from .embeddings import get_embedder
        embedder, _ = get_embedder()
        task = str(state.get("task", ""))
        r = state.get("last_test_result") or {}
        diag = (r.get("diagnosis") or {}).get("summary", "")
        failing = " ".join(str(t) for t in (r.get("failing_tests") or [])[:4])
        query = f"{task} {diag} {failing}".strip()
        if not query:
            return []
        texts = [f"{l.get('symptom', '')} {l.get('root_cause', '')} {l.get('fix_summary', '')}" for l in lessons]
        qvec, *lvecs = embedder.embed([query, *texts])
        scored = sorted(zip(lessons, (_cosine(qvec, v) for v in lvecs)),
                        key=lambda pair: pair[1], reverse=True)
        out = []
        for lesson, score in scored:
            if score >= min_score and len(out) < k:
                hint = dict(lesson)
                hint["score"] = round(float(score), 4)
                out.append(hint)
        return out
    except Exception:
        return []


def hints_block(hints: list[dict]) -> str:
    if not hints:
        return ""
    lines = ["", "", "Prior experience from past runs (hints only, NOT ground truth — "
                     "verify independently before acting; never weaken tests):"]
    for h in hints:
        lines.append(f"- Symptom: {h.get('symptom', '')} | Root cause: {h.get('root_cause', '')} "
                     f"| Fix: {h.get('fix_summary', '')} | Outcome: {h.get('outcome', '')} "
                     f"(relevance {h.get('score', 0):.2f})")
    return "\n".join(lines)
