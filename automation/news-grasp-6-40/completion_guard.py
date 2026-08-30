"""News-Grasp 6:40 automation の read-only stdout JSON projection。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable


DIRECT_MAINLINE_RECEIPT_SCHEMA = "NEWS_GRASP_DIRECT_MAINLINE_RECEIPT_V1"
DIRECT_PUBLIC_VERIFICATION_SCHEMA = "NEWS_GRASP_DIRECT_PUBLIC_VERIFICATION_V1"
DIRECT_PUBLIC_INCOMPLETE_CONTRACT = "public incomplete は direct runtime の exact successor に戻す"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {"_missing": True, "_path": str(path)}
    except Exception as exc:  # noqa: BLE001 - projection must report parse errors as data.
        return {"_invalid": True, "_path": str(path), "_error": str(exc)}
    return value if isinstance(value, dict) else {
        "_invalid": True,
        "_path": str(path),
        "_error": "not_object",
    }


def evaluate(
    manifest: dict[str, Any],
    runner_state: dict[str, Any],
    issue_date: str,
    *,
    historical_recovery_predicate: Callable[..., bool] | None = None,
) -> dict[str, Any]:
    failures: list[str] = []
    if manifest.get("_missing"):
        failures.append("publish_complete_manifest_missing")
    if manifest.get("_invalid"):
        failures.append("publish_complete_manifest_invalid")
    if runner_state.get("_missing"):
        failures.append("runner_state_missing")
    if runner_state.get("_invalid"):
        failures.append("runner_state_invalid")

    if manifest.get("date") != issue_date:
        failures.append("manifest_date_mismatch")
    if manifest.get("ok") is not True:
        failures.append("publish_complete_not_ok")
    if str(manifest.get("public_status") or "").lower() != "green":
        failures.append("public_status_not_green")
    if str(manifest.get("scheduled_attempt_status") or "") not in {
        "succeeded",
        "failed_then_recovered",
    }:
        failures.append("scheduled_attempt_status_unacceptable")
    if str(manifest.get("recovery_attempt_status") or "") not in {
        "not_needed",
        "succeeded",
    }:
        failures.append("recovery_attempt_status_unacceptable")

    distribution = manifest.get("distribution_artifacts")
    if not isinstance(distribution, dict) or distribution.get("missing"):
        failures.append("distribution_artifacts_missing")

    publish = manifest.get("publish")
    if not isinstance(publish, dict) or publish.get("ok") is not True:
        failures.append("publish_probe_not_ok")
    notification = manifest.get("notification")
    if not isinstance(notification, dict) or notification.get("ok") is not True:
        failures.append("notification_not_ok")

    podcasts = manifest.get("podcasts")
    if not isinstance(podcasts, dict):
        failures.append("podcasts_missing")
    else:
        primary = podcasts.get("primary")
        deepdive = podcasts.get("deepdive")
        if not isinstance(primary, dict) or primary.get("ok") is not True:
            failures.append("primary_podcast_not_ok")
        if not isinstance(deepdive, dict) or deepdive.get("ok") is not True:
            failures.append("deepdive_podcast_not_ok")

    readiness = manifest.get("live_runner_readiness")
    next_run = readiness.get("next_run_readiness") if isinstance(readiness, dict) else {}
    historical_recovery = bool(
        historical_recovery_predicate
        and historical_recovery_predicate(
            readiness,
            scheduled_status=str(manifest.get("scheduled_attempt_status") or ""),
            recovery_status=str(manifest.get("recovery_attempt_status") or ""),
        )
    )
    if (
        not isinstance(readiness, dict)
        or readiness.get("ok") is not True
    ) and not historical_recovery:
        failures.append("live_runner_readiness_not_ok")
    if (
        not isinstance(next_run, dict)
        or next_run.get("ok") is not True
    ) and not historical_recovery:
        failures.append("next_run_readiness_not_ok")

    if runner_state.get("date") != issue_date:
        failures.append("runner_state_date_mismatch")
    if runner_state.get("status") != "publish_complete":
        failures.append("runner_state_not_publish_complete")
    if runner_state.get("exit_code") != 0:
        failures.append("runner_state_exit_nonzero")
    if runner_state.get("run_intent") != "ScheduledRecoveryFull":
        failures.append("runner_state_run_intent_mismatch")
    if runner_state.get("publish_manifest_path") and manifest.get("_path"):
        if Path(str(runner_state["publish_manifest_path"])).resolve() != Path(
            str(manifest["_path"])
        ).resolve():
            failures.append("runner_state_manifest_path_mismatch")

    return {
        "schemaVersion": "NEWS_GRASP_640_COMPLETION_GUARD_V1",
        "ok": not failures,
        "issueDate": issue_date,
        "failures": failures,
        "scheduled_attempt_status": manifest.get("scheduled_attempt_status"),
        "recovery_attempt_status": manifest.get("recovery_attempt_status"),
        "public_status": manifest.get("public_status"),
        "runner_status": runner_state.get("status"),
        "run_intent": runner_state.get("run_intent"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--issue-date", default=date.today().isoformat())
    parser.add_argument("--direct-receipt", type=Path)
    parser.add_argument("--direct-run-id")
    parser.add_argument("--direct-state-root", type=Path)
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument("--public-base-url", default="")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--wait-sec", type=int, default=0)
    parser.add_argument("--poll-sec", type=int, default=30)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--runner-state",
        type=Path,
        default=Path.home() / "bin" / "news-grasp-runner-state.json",
    )
    parser.add_argument("--ops-root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    if args.direct_run_id or args.direct_state_root is not None:
        if not args.direct_run_id or args.direct_state_root is None:
            result = {
                "schemaVersion": "NEWS_GRASP_DIRECT_COMPLETION_GUARD_V1",
                "ok": False,
                "issue_date": args.issue_date,
                "failures": ["direct_runtime_arguments_incomplete"],
            }
            sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            return 2
        try:
            ops_root = args.ops_root.resolve(strict=True)
            sys.path.insert(0, str(ops_root))
            from tools.news_grasp_direct_runtime import (
                DirectRunStore,
                verify_public_completion,
            )

            result = verify_public_completion(
                DirectRunStore(args.direct_state_root, create=False),
                run_id=args.direct_run_id,
                repo_root=args.repo_root or ops_root,
                public_base_url=args.public_base_url or None,
                remote=args.remote,
                branch=args.branch,
                wait_sec=args.wait_sec,
                poll_sec=args.poll_sec,
            )
        except (OSError, ImportError, ValueError, RuntimeError) as exc:
            result = {
                "schemaVersion": "NEWS_GRASP_DIRECT_COMPLETION_GUARD_V1",
                "ok": False,
                "issue_date": args.issue_date,
                "failures": [f"direct_runtime_verification_failed:{exc}"],
            }
        sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        return 0 if result["ok"] else 2

    if args.direct_receipt is not None:
        receipt = _load_json(args.direct_receipt)
        state_root = receipt.get("state_root") or receipt.get("stateRoot")
        run_id = receipt.get("run_id") or receipt.get("runId")
        if state_root and run_id:
            try:
                ops_root = args.ops_root.resolve(strict=True)
                sys.path.insert(0, str(ops_root))
                from tools.news_grasp_direct_runtime import (
                    DirectRunStore,
                    verify_public_completion,
                )

                result = verify_public_completion(
                    DirectRunStore(Path(str(state_root)), create=False),
                    run_id=str(run_id),
                    repo_root=receipt.get("repo_root") or receipt.get("repoRoot") or ops_root,
                    public_base_url=receipt.get("public_base_url") or receipt.get("publicBaseUrl") or None,
                    remote=str(receipt.get("remote") or "origin"),
                    branch=str(receipt.get("branch") or "main"),
                    wait_sec=int(receipt.get("wait_sec") or receipt.get("waitSec") or 0),
                    poll_sec=int(receipt.get("poll_sec") or receipt.get("pollSec") or 30),
                )
                if result.get("issue_date") != args.issue_date:
                    result = {
                        **result,
                        "ok": False,
                        "failures": list(result.get("failures") or []) + ["issue_date_mismatch"],
                    }
            except (OSError, ImportError, ValueError, RuntimeError) as exc:
                result = {
                    "schemaVersion": "NEWS_GRASP_DIRECT_COMPLETION_GUARD_V1",
                    "ok": False,
                    "completion_mode": "direct_public_v1",
                    "issue_date": args.issue_date,
                    "failures": [f"direct_runtime_verification_failed:{exc}"],
                }
        else:
            result = {
                "schemaVersion": "NEWS_GRASP_DIRECT_COMPLETION_GUARD_V1",
                "ok": False,
                "completion_mode": "direct_public_v1",
                "issue_date": args.issue_date,
                "failures": ["direct_completion_requires_canonical_runtime_state"],
            }
        sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        return 0 if result["ok"] else 2

    if args.manifest is None:
        parser.error("--manifest is required unless --direct-receipt is supplied")

    manifest = _load_json(args.manifest)
    manifest["_path"] = str(args.manifest)
    runner_state = _load_json(args.runner_state)
    try:
        ops_root = args.ops_root.resolve(strict=True)
        sys.path.insert(0, str(ops_root))
        from tools.news_grasp_operational_contract import (
            require_post_public_green_operation,
        )
        from tools.news_grasp_completion_guard import (
            _historical_scheduled_failure_is_recovered,
        )

        require_post_public_green_operation("completion_guard")
    except (OSError, ImportError, ValueError):
        result = {
            "schemaVersion": "NEWS_GRASP_640_COMPLETION_GUARD_V1",
            "ok": False,
            "issueDate": args.issue_date,
            "failures": ["post_public_closeout_blocker"],
        }
        sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        return 2
    result = evaluate(
        manifest,
        runner_state,
        args.issue_date,
        historical_recovery_predicate=(
            _historical_scheduled_failure_is_recovered
        ),
    )
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
