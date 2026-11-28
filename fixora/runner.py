"""Run orchestration shared by the CLI and the FastAPI server.

Lifecycle: pending -> running -> {awaiting_approval, failed, error, timed_out}
                                        |-- approve --> applied
                                        |-- reject  --> rejected

The workspace Sandbox stays alive between the graph finishing and a human
decision; nothing is written back to the original repo (local runs) or exported
(GitHub runs) until an explicit approve call. One run at a time per process.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings, load_settings
from .events import EventEmitter
from .github import RepoInputError, is_github_url, clone_public_repo
from .llm import LLMError, FallbackLLMClient, get_llm, friendly_llm_error
from . import memory
from .sandbox import Sandbox, SandboxError, _snapshot
from .demos import demo_fixture_paths
from .tools import build_tools
from .agents.common import RunContext
from .agents.graph import build_graph, initial_state
from langgraph.checkpoint.memory import MemorySaver

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = PROJECT_ROOT / "logs"

ACTIVE = {"pending", "running"}            # blocks new runs
HOLD = {"awaiting_approval"}               # blocks new runs, keeps workspace
TERMINAL = {"applied", "rejected", "failed", "error", "timed_out"}
MAX_RECORDS = 50


class RunBusyError(Exception):
    """Another run is already active on this server."""


class RunNotFoundError(Exception):
    pass


class RunConflictError(Exception):
    """The run is not in the state required for this action."""


@dataclass
class RunRecord:
    run_id: str
    repo_input: str
    task: str
    is_github: bool
    max_attempts: int
    status: str = "pending"
    patch_only: bool = False
    source_repo: str = ""             # actual path/URL used for cloning; never shown as a display label
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    error: str | None = None
    report: dict = field(default_factory=dict)
    patch_file: str | None = None
    log_file: str | None = None
    source_path: Path | None = None        # github clone (kept for diff); local repo path otherwise
    sandbox: Sandbox | None = None
    ctx: RunContext | None = None
    emitter: EventEmitter = field(default_factory=EventEmitter)
    graph: object | None = None
    graph_config: dict | None = None
    resumable: bool = False

    def public(self) -> dict:
        diff = self.report.get("diff", "")
        return {
            "run_id": self.run_id,
            "repo": self.repo_input,
            "is_github": self.is_github,
            "task": self.task,
            "status": self.status,
            "error": self.error,
            "resumable": self.resumable and self.status == "error",
            "patch_file": self.patch_file,
            "created": time.strftime("%H:%M:%S", time.localtime(self.created)),
            "elapsed_s": round((self.finished or time.time()) - self.created, 2),
            "tests_before": self.report.get("tests_before"),
            "tests_after": self.report.get("tests_after"),
            "review": self.report.get("review"),
            "attempts": self.report.get("attempts"),
            "edits_made": self.report.get("edits_made"),
            "diff": diff,
            "timings": self.report.get("timings"),
        }


class RunManager:
    def __init__(self):
        self._runs: dict[str, RunRecord] = {}
        self._active: str | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------- control
    def start(self, repo: str, task: str, *, max_attempts: int | None = None,
              allow_local: bool = True, printer=print,
              display_repo: str | None = None, patch_only: bool = False) -> RunRecord:
        settings = load_settings()
        repo = (repo or "").strip()
        if not repo:
            raise RepoInputError("repo is required")
        if is_github_url(repo):
            is_github = True
            source_repo = repo
        else:
            if not allow_local:
                raise RepoInputError("only public GitHub URLs are accepted via the API")
            path = Path(repo).expanduser().resolve()
            if not path.is_dir():
                raise RepoInputError(f"local repo not found or not a directory: {path}")
            if path == Path(path.anchor) or path == Path.cwd():
                raise RepoInputError("refusing to use an overly broad directory as a repo")
            source_repo, is_github = str(path), False
        if not task or not task.strip():
            raise RepoInputError("task is required")
        with self._lock:
            active = self._runs.get(self._active) if self._active else None
            if active and active.status in ACTIVE | HOLD:
                raise RunBusyError(f"run {active.run_id} is {active.status}; try again later")
            record = RunRecord(run_id=uuid.uuid4().hex[:12], repo_input=display_repo or source_repo,
                               task=task.strip(), is_github=is_github,
                               patch_only=patch_only, source_repo=source_repo,
                               max_attempts=max_attempts or settings.max_attempts)
            self._runs[record.run_id] = record
            self._active = record.run_id
            self._evict_old()
        watchdog = threading.Timer(settings.run_timeout, self._timed_out,
                                   args=(record.run_id, settings.run_timeout))
        watchdog.daemon = True
        self._watchdogs = getattr(self, "_watchdogs", {})
        self._watchdogs[record.run_id] = watchdog
        watchdog.start()
        thread = threading.Thread(target=self._run, args=(record, settings, printer),
                                  daemon=True, name=f"fixora-{record.run_id}")
        thread.start()
        return record

    def _timed_out(self, run_id: str, run_timeout: int):
        record = self._runs.get(run_id)
        if record is None or record.status != "running":
            return
        # Mark failed and free the slot. We deliberately do NOT delete the
        # workspace here (the graph thread may still be mid-write).
        record.status = "timed_out"
        record.error = f"run exceeded RUN_TIMEOUT ({run_timeout}s)"
        record.report = {"status": "timed_out", "error": record.error}
        record.finished = time.time()
        record.emitter.emit("done", status="timed_out", error=record.error)
        self._save_log(record)
        self._release(run_id)

    def run_blocking(self, repo: str, task: str, *, max_attempts: int | None = None,
                     printer=print) -> RunRecord:
        """CLI path: run synchronously and return the finished record."""
        record = self.start(repo, task, max_attempts=max_attempts,
                            allow_local=True, printer=printer)
        while record.status in ACTIVE:
            time.sleep(0.05)
        return record

    # ---------------------------------------------------------------- run
    def _run(self, record: RunRecord, settings: Settings, printer):
        record.status = "running"
        record.started = time.time()
        ctx: RunContext | None = None
        try:
            if record.is_github:
                record.source_path = clone_public_repo(
                    record.source_repo or record.repo_input, clone_timeout=settings.clone_timeout,
                    max_repo_mb=settings.max_repo_mb)
            else:
                record.source_path = Path(record.source_repo or record.repo_input)
            sandbox = Sandbox(record.source_path)
            record.sandbox = sandbox
            llm = get_llm(settings)
            record.emitter = EventEmitter(run_id=record.run_id)
            record.emitter.emit("start", repo=record.repo_input, task=record.task,
                                is_github=record.is_github)
            tools = build_tools(sandbox, settings)
            ctx = RunContext(sandbox=sandbox, settings=settings, llm=llm, tools=tools,
                             printer=printer, emitter=record.emitter)
            FallbackLLMClient.on_fallback = lambda msg: ctx.log_step("llm", msg)
            record.ctx = ctx
            # Checkpoint the graph per node so a mid-run provider failure can be
            # resumed (see resume()): only the failed node re-executes.
            graph = build_graph(ctx, checkpointer=MemorySaver())
            record.graph = graph
            record.graph_config = {"configurable": {"thread_id": record.run_id}}
            final = graph.invoke(initial_state(record.task, record.max_attempts),
                                 record.graph_config)
            record.report = final.get("report", {})
            record.status = final.get("status", "failed")
            record.report.setdefault("timings", ctx.timings())
            if record.report:
                repo_name = record.repo_input.rstrip("/").rsplit("/", 1)[-1]
                lesson = memory.save_run(repo_name, record.task, record.report)
                if lesson and ctx:
                    ctx.log_step("memory", "lesson recorded for future runs",
                                 outcome=lesson.get("outcome"))
        except RepoInputError as e:
            record.status, record.error = "error", str(e)
            record.report = {"status": "error", "error": str(e)}
        except LLMError as e:
            friendly = friendly_llm_error(e)
            record.status, record.error = "error", friendly
            record.report = {"status": "error", "error": friendly}
            # Provider failure mid-graph: the checkpoint holds every completed
            # node, so this run can be resumed from the failed node instead of
            # starting over. Keep the workspace alive until decide/resume.
            record.resumable = bool(record.ctx is not None and record.sandbox is not None)
        except Exception as e:  # never lose the log
            record.status, record.error = "error", (
                f"{type(e).__name__}: the run failed unexpectedly. The run log has the technical details.")
            record.report = {"status": "error", "error": record.error}
        finally:
            self._finalize(record)

    def resume(self, run_id: str) -> RunRecord:
        """Re-invoke a checkpointed run from the node that failed.

        Resume keeps everything that already happened (baseline test, analyse,
        plan, partial fix work, edits applied in the sandbox). Only the node
        that raised is re-executed. Raises RunConflictError when the run is
        not resumable (wrong status, or its workspace was discarded).
        """
        record = self._get(run_id)
        if record.status != "error":
            raise RunConflictError(f"run {run_id} is {record.status}; only failed runs can be resumed")
        if (not record.resumable or record.ctx is None or record.graph is None
                or record.graph_config is None or record.sandbox is None):
            raise RunConflictError("this run cannot be resumed — its workspace was discarded")
        with self._lock:
            active = self._runs.get(self._active) if self._active else None
            if active and active.status in ACTIVE | HOLD:
                raise RunBusyError(f"run {active.run_id} is {active.status}; try again later")
            record.status = "running"
            record.finished = None
            record.error = None
            record.report = {}
            record.resumable = False
            self._active = record.run_id
        settings = load_settings()
        # Resume should pick up keys/models edited in .env since the failure —
        # rebuild the LLM chain with the fresh settings instead of reusing the
        # exhausted one. Tools keep their settings; llm budgets re-read from ctx.
        try:
            record.ctx.llm = get_llm(settings)
            record.ctx.settings = settings
            record.ctx.log_step("llm", "Rebuilt the LLM chain from the current .env for resume")
        except LLMError:
            pass  # keep the original chain; resume will surface a friendly error if it fails
        watchdog = threading.Timer(settings.run_timeout, self._timed_out,
                                   args=(record.run_id, settings.run_timeout))
        watchdog.daemon = True
        self._watchdogs = getattr(self, "_watchdogs", {})
        self._watchdogs[record.run_id] = watchdog
        watchdog.start()
        thread = threading.Thread(target=self._resume_run, args=(record,),
                                  daemon=True, name=f"fixora-resume-{record.run_id}")
        thread.start()
        return record

    def _resume_run(self, record: RunRecord):
        ctx = record.ctx
        assert ctx is not None and record.graph is not None and record.graph_config is not None
        record.started = record.started or time.time()
        record.emitter.emit("step", node="llm",
                            message="Resuming from the point of failure — completed phases are kept")
        try:
            final = record.graph.invoke(None, record.graph_config)
            record.report = final.get("report", {})
            record.status = final.get("status", "failed")
            record.report.setdefault("timings", ctx.timings())
            if record.report:
                repo_name = record.repo_input.rstrip("/").rsplit("/", 1)[-1]
                lesson = memory.save_run(repo_name, record.task, record.report)
                if lesson and record.ctx:
                    record.ctx.log_step("memory", "lesson recorded for future runs",
                                        outcome=lesson.get("outcome"))
        except LLMError as e:
            friendly = friendly_llm_error(e)
            record.status, record.error = "error", friendly
            record.report = {"status": "error", "error": friendly}
            # Still provider-side: keep the checkpoint so the user can resume again.
            record.resumable = True
        except Exception as e:  # never lose the log
            record.status, record.error = "error", (
                f"{type(e).__name__}: the run failed unexpectedly. The run log has the technical details.")
            record.report = {"status": "error", "error": record.error}
        finally:
            self._finalize(record)

    def _finalize(self, record: RunRecord):
        """Shared tail for _run/_resume_run: persist log, cancel watchdog,
        emit a terminal event, release the one-run slot, clean up unless a
        resumable checkpoint or an approval is pending."""
        record.finished = time.time()
        self._save_log(record)
        watchdog = getattr(self, "_watchdogs", {}).pop(record.run_id, None)
        if watchdog:
            watchdog.cancel()
        if record.status != "awaiting_approval":
            # The UI needs a terminal event or it shows 'running' forever.
            # Graph-completed outcomes already emit done from report_node; the
            # exception paths (error set, or no ctx) did not — emit one here.
            if record.ctx is None or record.error is not None:
                payload = {"status": record.status, "error": record.error}
                if record.resumable:
                    payload["resumable"] = True
                record.emitter.emit("done", **payload)
            if not record.resumable:
                self._cleanup(record)
            self._release(record.run_id)

    # ------------------------------------------------------------- human gate
    def approve(self, run_id: str) -> dict:
        record = self._get(run_id)
        if record.status != "awaiting_approval":
            raise RunConflictError(f"run {run_id} is {record.status}, not awaiting_approval")
        diff = record.report.get("diff", "")
        if record.is_github or record.patch_only:
            LOGS_DIR.mkdir(mode=0o700, exist_ok=True)
            os.chmod(LOGS_DIR, 0o700)
            patch_path = LOGS_DIR / f"{run_id}.patch"
            patch_path.write_text(diff, encoding="utf-8")
            os.chmod(patch_path, 0o600)
            record.patch_file = str(patch_path)
            record.report["patch_file"] = record.patch_file
            message = ("Built-in demo runs never modify the checked-in fixtures; apply this patch to your own copy."
                       if record.patch_only and not record.is_github
                       else "GitHub clones are never pushed; apply this patch locally.")
            applied = {"patch_file": record.patch_file,
                       "message": message}
        else:
            assert record.sandbox is not None and record.source_path is not None
            source = Path(record.source_path).resolve()
            fixtures = demo_fixture_paths()
            if any(source == fixture or fixture in source.parents for fixture in fixtures):
                # Never rewrite the checked-in fixtures: it removes the seeded
                # bugs and the demos then start with a green suite forever.
                raise RunConflictError(
                    "Refusing to apply the fix into a built-in demo fixture "
                    f"({source}). Demos run on a temporary copy — use the demo "
                    "picker (Run setup → Built-in demo) or point --repo at your own copy. "
                    "If a fixture was already rewritten, restore it with: "
                    "git checkout -- demo_repo demo_repo_hard demo_repo_syntax demo_repo_import")
            self._apply_workspace_to_original(record.sandbox.workspace, record.source_path)
            applied = {"applied_to": str(record.source_path)}
        record.status = "applied"
        record.report["status"] = "applied"
        record.finished = time.time()
        record.emitter.emit("applied", run_id=run_id, **applied)
        record.emitter.emit("done", status="applied")
        self._cleanup(record)
        self._save_log(record)
        self._release(run_id)
        return {"run_id": run_id, "status": "applied", **applied}

    def reject(self, run_id: str) -> dict:
        record = self._get(run_id)
        if record.status != "awaiting_approval":
            raise RunConflictError(f"run {run_id} is {record.status}, not awaiting_approval")
        record.status = "rejected"
        record.report["status"] = "rejected"
        record.finished = time.time()
        record.emitter.emit("done", status="rejected")
        self._cleanup(record)
        self._save_log(record)
        self._release(run_id)
        return {"run_id": run_id, "status": "rejected"}

    # ---------------------------------------------------------------- info
    def get(self, run_id: str) -> dict:
        return self._get(run_id).public()

    def events(self, run_id: str) -> EventEmitter:
        return self._get(run_id).emitter

    def list_runs(self) -> list[dict]:
        runs = []
        for r in sorted(self._runs.values(), key=lambda r: r.created, reverse=True)[:MAX_RECORDS]:
            runs.append({
                "run_id": r.run_id,
                "repo": r.repo_input,
                "status": r.status,
                "task": r.task,
                "error": r.error,
                "created": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(r.created)),
                "duration_s": round((r.finished or time.time()) - r.created, 2),
            })
        return runs

    # ------------------------------------------------------------ internals
    def _get(self, run_id: str) -> RunRecord:
        record = self._runs.get(run_id)
        if record is None:
            raise RunNotFoundError(f"unknown run: {run_id}")
        return record

    def _release(self, run_id: str):
        with self._lock:
            if self._active == run_id:
                self._active = None

    def _evict_old(self):
        if len(self._runs) <= MAX_RECORDS:
            return
        finished = [r for r in self._runs.values() if r.status in TERMINAL]
        finished.sort(key=lambda r: r.created)
        for r in finished[:len(self._runs) - MAX_RECORDS]:
            self._runs.pop(r.run_id, None)

    def _cleanup(self, record: RunRecord):
        if record.sandbox is not None:
            record.sandbox.cleanup()
        if record.source_path is not None and (record.is_github or record.patch_only):
            src = record.source_path.resolve()
            parent = src.parent
            tmp_root = Path(tempfile.gettempdir()).resolve()
            safe_temp = parent.parent == tmp_root and parent.name.startswith(("fixora-github-", "fixora-demo-"))
            if safe_temp:
                shutil.rmtree(parent, ignore_errors=True)

    @staticmethod
    def _apply_workspace_to_original(workspace: Path, original: Path):
        """Copy changed/new files back; remove deleted files. Local repos only."""
        new_files = _snapshot(workspace)
        old_files = _snapshot(original)
        for rel, content in new_files.items():
            if old_files.get(rel) != content:
                dest = original / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")
        for rel in set(old_files) - set(new_files):
            try:
                (original / rel).unlink()
            except OSError:
                pass

    def _save_log(self, record: RunRecord):
        LOGS_DIR.mkdir(mode=0o700, exist_ok=True)
        os.chmod(LOGS_DIR, 0o700)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = LOGS_DIR / f"{stamp}-{record.run_id}.json"
        payload = {
            "run_id": record.run_id,
            "task": record.task,
            "repo": record.repo_input,
            "is_github": record.is_github,
            "status": record.status,
            "error": record.error,
            "patch_file": record.patch_file,
            "steps": record.ctx.steps if record.ctx else [],
            "events": record.emitter.history,
            "report": record.report,
        }
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        os.chmod(path, 0o600)
        record.log_file = str(path)
