"""DevMate CLI entry point (Phase 2).

  python -m devmate.cli --repo ./demo_repo --task "Find and fix the failing tests"
  python -m devmate.cli --repo https://github.com/owner/repo --task "Fix the failing tests"
  python -m devmate.cli ... --yes          # approve & apply after reviewer passes

Prints each step live ([ANALYSE] [PLAN] [TOOL CALL ...] [CACHED ...] [TEST]
[RETRY n] [REVIEW] [REPORT]), saves a JSON run log to logs/<timestamp>-<id>.json.
The original repo is NEVER modified unless you pass --yes (or approve via the API).
Exit 0 if the run reaches awaiting_approval/applied, 1 on failed, 2 on error.
"""
from __future__ import annotations

import argparse
import sys

from .config import load_settings
from .llm import list_available_models, LLMError
from .github import RepoInputError
from .runner import RunBusyError, RunManager


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="DevMate Phase 2 — autonomous fix agent")
    parser.add_argument("--repo", help="Local path or https://github.com/<owner>/<repo>")
    parser.add_argument("--task", help="What the agent should do")
    parser.add_argument("--max-attempts", type=int, default=None,
                        help="Override MAX_ATTEMPTS from .env (default 3)")
    parser.add_argument("--list-models", action="store_true",
                        help="List model IDs visible to the configured provider key, then exit")
    parser.add_argument("--yes", action="store_true",
                        help="Human approval: apply the reviewed diff to the original repo "
                             "(local repos) or write a patch file (GitHub repos)")
    args = parser.parse_args(argv)

    settings = load_settings()
    if args.list_models:
        try:
            models = list_available_models(settings)
        except LLMError as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            return 2
        print(f"[MODELS] provider={settings.llm_provider} ({len(models)} visible)")
        for model_id in models:
            print(model_id)
        return 0
    if not args.repo or not args.task:
        parser.error("--repo and --task are required unless --list-models is used")
    if args.max_attempts:
        settings.max_attempts = args.max_attempts

    manager = RunManager()
    print(f"[START] repo={args.repo} max_attempts={settings.max_attempts} "
          f"llm_providers={_provider_plan(settings)}")
    try:
        record = manager.run_blocking(args.repo, args.task,
                                      max_attempts=settings.max_attempts, printer=print)
    except RepoInputError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 2
    except RunBusyError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 429

    exit_code = _finish(manager, record, args.yes)
    if record.log_file:
        print(f"[LOG] run log saved to {record.log_file}")
    print(f"[DONE] exit={exit_code} status={record.status}")
    return exit_code


def _provider_plan(settings) -> str:
    """Show provider labels only; never print API keys from LLM_PROVIDERS."""
    raw = (getattr(settings, "llm_providers", "") or "").strip()
    if not raw:
        return settings.llm_provider
    labels = []
    for entry in raw.split(","):
        parts = [part.strip() for part in entry.split("|")]
        label = parts[0] if parts else ""
        labels.append(label or "invalid-entry")
    return ",".join(labels)


def _finish(manager: RunManager, record, approved: bool) -> int:
    if record.status == "awaiting_approval":
        if approved:
            result = manager.approve(record.run_id)
            if record.patch_file:
                print(f"[APPROVED] patch written to {record.patch_file}")
            else:
                print(f"[APPROVED] applied to {result.get('applied_to')}")
            return 0
        print("[AWAITING APPROVAL] reviewer approved and tests pass. The original repo is "
              "unchanged. Re-run with --yes to apply.")
        return 0
    if record.status == "applied":
        return 0
    if record.status in ("error", "timed_out"):
        print(f"[ERROR] {record.error}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
