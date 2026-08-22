"""News-Grasp production control-plane root の純粋 preflight。

Global HighCost の authority/budget/terminal state は複製しない。この module は
News-Grasp が所有する artifact/ops/runtime/live の役割と managed byte parity だけを
検証し、生成・model・publish を開始する前の typed decision を返す。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import news_grasp_high_cost_binding


SCHEMA_VERSION = "NEWS_GRASP_CONTROL_PLANE_PREFLIGHT_V1"
ISSUE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ALLOWED_RUN_INTENTS = {
    "ScheduledProduction",
    "ScheduledRecoveryFull",
    "ScheduledEquivalentNoPublish",
    "StartupCanary",
}
ISOLATED_RUNNER_STATE_INTENTS = {"StartupCanary"}
BOOTSTRAP_REFRESHABLE_OBSERVATION_REASONS = {
    "execution_receipt_missing",
    "execution_receipt_mismatch",
    "execution_receipt_stale",
    "bootstrap_last_run_issue_date_stale",
    "bootstrap_generation_timestamp_stale",
    "bootstrap_task_last_result_not_ok",
}
MANAGED_OPS_FILES = (
    "news-grasp-task-launcher.pyw",
    "news-grasp-bootstrap.ps1",
    "watch-news-grasp-runner.ps1",
    "news-grasp-runner.ps1",
    "run_codex_with_timeout.ps1",
)


def _sha256(path: Path) -> str:
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat()
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after:
        raise OSError("CONTROL_PLANE_FILE_CHANGED_DURING_HASH")
    return digest.hexdigest()


def _is_reparse_or_symlink(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        return bool(path.stat().st_file_attributes & 0x400)
    except (AttributeError, OSError):
        return False


def _resolved_directory(path: Path) -> Path | None:
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not resolved.is_dir() or _is_reparse_or_symlink(path):
        return None
    return resolved


def _git_head(root: Path) -> str:
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        process = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=creationflags,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    value = process.stdout.strip().lower()
    return value if process.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", value) else ""


def _file_observation(path: Path) -> dict:
    valid = path.is_file() and not _is_reparse_or_symlink(path)
    if valid:
        cursor = path
        for _ in range(4):
            if _is_reparse_or_symlink(cursor):
                valid = False
                break
            cursor = cursor.parent
    try:
        digest = _sha256(path) if valid else ""
    except OSError:
        valid = False
        digest = ""
    return {
        "path": str(path),
        "exists": valid,
        "sha256": digest,
    }


def _base_result(
    *,
    artifact_root: Path,
    ops_root: Path,
    production_runtime_root: Path,
    live_bin_root: Path,
    issue_date: str,
    run_intent: str,
) -> dict:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "ok": False,
        "status": "not_ready",
        "reasonCode": "CONTROL_PLANE_NOT_VERIFIED",
        "issueDate": issue_date,
        "runIntent": run_intent,
        "roots": {
            "artifact_root": {"path": str(artifact_root), "head": ""},
            "ops_root": {"path": str(ops_root), "head": ""},
            "production_runtime_root": {
                "path": str(production_runtime_root),
                "head": "",
            },
            "live_bin_root": {"path": str(live_bin_root)},
        },
        "managedFiles": [],
        "scheduledTask": {},
        "nextRunReadiness": {},
        "lastScheduledAttempt": {},
        "runnerState": {
            "path": str(live_bin_root / "news-grasp-runner-state.json"),
            "exists": False,
            "sha256": "",
        },
        "globalBinding": {
            "status": "not_verified",
            "authorityReplicated": False,
            "budgetReplicated": False,
            "terminalStateReplicated": False,
        },
    }


def verify_control_plane(
    *,
    artifact_root: Path,
    ops_root: Path,
    production_runtime_root: Path,
    live_bin_root: Path,
    issue_date: str,
    run_intent: str,
    managed_files: Iterable[str] = MANAGED_OPS_FILES,
    runner_readiness: dict[str, Any] | None = None,
    runner_state_path: Path | None = None,
    high_cost_binding_path: Path | None = None,
    high_cost_binding_receipt_sha256: str = "",
    allow_isolated_high_cost_fixture: bool = False,
) -> dict:
    """4 root を副作用なしで検証し、最初の drift を typed reason で返す。"""
    raw_roots = {
        "artifact_root": Path(artifact_root),
        "ops_root": Path(ops_root),
        "production_runtime_root": Path(production_runtime_root),
        "live_bin_root": Path(live_bin_root),
    }
    result = _base_result(
        **raw_roots,
        issue_date=issue_date,
        run_intent=run_intent,
    )
    if not ISSUE_DATE_RE.fullmatch(issue_date):
        return {**result, "reasonCode": "ISSUE_DATE_INVALID"}
    if run_intent not in ALLOWED_RUN_INTENTS:
        return {**result, "reasonCode": "RUN_INTENT_INVALID"}

    root_reason = {
        "artifact_root": "ARTIFACT_ROOT_INVALID",
        "ops_root": "OPS_ROOT_INVALID",
        "production_runtime_root": "PRODUCTION_RUNTIME_ROOT_INVALID",
        "live_bin_root": "LIVE_BIN_ROOT_INVALID",
    }
    roots: dict[str, Path] = {}
    for role, path in raw_roots.items():
        resolved = _resolved_directory(path)
        if resolved is None:
            return {**result, "reasonCode": root_reason[role]}
        roots[role] = resolved
        result["roots"][role]["path"] = str(resolved)
    if high_cost_binding_path is not None or high_cost_binding_receipt_sha256:
        if high_cost_binding_path is None or not high_cost_binding_receipt_sha256:
            return {**result, "reasonCode": "HIGH_COST_WORKSPACE_BINDING_MISSING"}
        try:
            resolved_binding = news_grasp_high_cost_binding.resolve_binding(
                binding_path=Path(high_cost_binding_path),
                expected_receipt_sha256=high_cost_binding_receipt_sha256,
            )
            binding_path = Path(str(resolved_binding["bindingPath"])).resolve(strict=True)
        except (OSError, news_grasp_high_cost_binding.HighCostBindingError) as error:
            reason = getattr(error, "reason", "HIGH_COST_WORKSPACE_BINDING_MISSING")
            return {**result, "reasonCode": str(reason)}
        if binding_path.parent != roots["live_bin_root"]:
            return {**result, "reasonCode": "HIGH_COST_IDENTITY_DRIFT"}
        result["globalBinding"] = {
            "status": "available",
            "schemaVersion": str(resolved_binding["bindingSchemaVersion"]),
            "path": str(binding_path),
            "receiptSha256": str(resolved_binding["bindingReceiptSha256"]),
            "descriptorSha256": str(resolved_binding["descriptorSha256"]),
            "adapterSha256": str(resolved_binding["adapterSha256"]),
            "generation": int(resolved_binding["generation"]),
            "authorityReplicated": False,
            "budgetReplicated": False,
            "terminalStateReplicated": False,
        }
    elif allow_isolated_high_cost_fixture:
        result["globalBinding"]["status"] = "not_supplied_isolated_fixture"
    else:
        return {**result, "reasonCode": "HIGH_COST_WORKSPACE_BINDING_MISSING"}
    for role in ("artifact_root", "ops_root", "production_runtime_root"):
        result["roots"][role]["head"] = _git_head(roots[role])
    ops_head = result["roots"]["ops_root"]["head"]
    runtime_head = result["roots"]["production_runtime_root"]["head"]
    if ops_head and runtime_head != ops_head:
        return {**result, "reasonCode": "PRODUCTION_RUNTIME_HEAD_DRIFT"}

    normalized_files: list[str] = []
    for name in managed_files:
        candidate = str(name).strip()
        if not candidate or Path(candidate).name != candidate:
            return {**result, "reasonCode": "MANAGED_FILE_NAME_INVALID"}
        if candidate not in normalized_files:
            normalized_files.append(candidate)
    if not normalized_files:
        return {**result, "reasonCode": "MANAGED_FILE_SET_EMPTY"}

    for name in normalized_files:
        ops_path = roots["ops_root"] / "scripts" / "ops" / name
        runtime_path = roots["production_runtime_root"] / "scripts" / "ops" / name
        live_path = roots["live_bin_root"] / name
        observation = {
            "name": name,
            "ops": _file_observation(ops_path),
            "runtime": _file_observation(runtime_path),
            "live": _file_observation(live_path),
        }
        result["managedFiles"].append(observation)
        if not observation["ops"]["exists"]:
            return {**result, "reasonCode": "OPS_MANAGED_FILE_MISSING"}
        if (
            not observation["runtime"]["exists"]
            or observation["runtime"]["sha256"] != observation["ops"]["sha256"]
        ):
            return {**result, "reasonCode": "PRODUCTION_RUNTIME_DRIFT"}
        if (
            not observation["live"]["exists"]
            or observation["live"]["sha256"] != observation["ops"]["sha256"]
        ):
            return {**result, "reasonCode": "LIVE_BIN_DRIFT"}

    if runner_readiness is None:
        from tools import daily_self_heal

        runner_readiness = daily_self_heal.verify_live_runner_readiness(
            repo_root=roots["artifact_root"],
            ops_repo_root=roots["ops_root"],
            date=issue_date,
            live_runner_path=roots["live_bin_root"] / "news-grasp-runner.ps1",
            live_watcher_path=roots["live_bin_root"] / "watch-news-grasp-runner.ps1",
            live_bootstrap_path=roots["live_bin_root"] / "news-grasp-bootstrap.ps1",
            live_task_launcher_path=roots["live_bin_root"]
            / "news-grasp-task-launcher.pyw",
            run_canary=False,
        )
    if not isinstance(runner_readiness, dict):
        return {**result, "reasonCode": "SCHEDULED_TASK_READINESS_INVALID"}
    result["scheduledTask"] = runner_readiness.get("scheduled_task") or {}
    result["nextRunReadiness"] = (
        runner_readiness.get("next_run_readiness") or {}
    )
    result["lastScheduledAttempt"] = (
        runner_readiness.get("last_scheduled_attempt") or {}
    )
    if runner_readiness.get("ok") is not True:
        readiness_reason = str(runner_readiness.get("reason") or "")
        task_definition_ok = result["scheduledTask"].get("definition_ok") is True
        historical_observation_only = bool(
            run_intent == "ScheduledRecoveryFull"
            and task_definition_ok
            and readiness_reason == "bootstrap_task_last_result_not_ok"
        )
        bootstrap_refresh_observation = bool(
            run_intent == "StartupCanary"
            and task_definition_ok
            and readiness_reason in BOOTSTRAP_REFRESHABLE_OBSERVATION_REASONS
        )
        recovery_missed_run = bool(
            run_intent == "ScheduledRecoveryFull"
            and task_definition_ok
            and readiness_reason == "scheduled_task_missed_runs"
        )
        if bootstrap_refresh_observation:
            result["bootstrapRefreshObservation"] = {
                "reason": readiness_reason,
                "preserved": True,
            }
            result["nextRunReadiness"] = {
                **result["nextRunReadiness"],
                "ok": True,
                "status": "ready_for_current_bootstrap_canary",
                "historicalObservationPreserved": True,
            }
        elif recovery_missed_run:
            result["recoveryAdmissionObservation"] = {
                "reason": readiness_reason,
                "preserved": True,
                "numberOfMissedRuns": result["scheduledTask"].get(
                    "number_of_missed_runs"
                ),
                "lastScheduledAttemptStatus": result["lastScheduledAttempt"].get(
                    "status"
                ),
            }
            result["nextRunReadiness"] = {
                **result["nextRunReadiness"],
                "ok": True,
                "status": "ready_for_scheduled_recovery",
                "scheduledFailurePreserved": True,
            }
        elif historical_observation_only:
            result["historicalReadinessObservation"] = {
                "reason": readiness_reason,
                "preserved": True,
                "bootstrapLastTaskResult": result["scheduledTask"].get(
                    "bootstrap_last_task_result"
                ),
            }
            result["nextRunReadiness"] = {
                **result["nextRunReadiness"],
                "ok": True,
                "status": "ready_after_current_control_plane_preflight",
                "historicalObservationPreserved": True,
            }
        else:
            action_drift_reasons = (
                "scheduled_task_",
                "task_launcher_",
                "bootstrap_task_",
            )
            reason_code = (
                "SCHEDULED_TASK_ACTION_DRIFT"
                if readiness_reason.startswith(action_drift_reasons)
                else "NEXT_RUN_READINESS_DRIFT"
            )
            return {
                **result,
                "reasonCode": reason_code,
                "readinessReason": readiness_reason,
            }

    canonical_state_path = (
        roots["live_bin_root"] / "news-grasp-runner-state.json"
    ).resolve(strict=False)
    if run_intent in ISOLATED_RUNNER_STATE_INTENTS:
        if runner_state_path is None:
            return {**result, "reasonCode": "ISOLATED_RUNNER_STATE_REQUIRED"}
        state_path = Path(runner_state_path).resolve(strict=False)
        if os.path.normcase(str(state_path)) == os.path.normcase(
            str(canonical_state_path)
        ):
            return {**result, "reasonCode": "ISOLATED_RUNNER_STATE_REQUIRED"}
    else:
        state_path = Path(
            runner_state_path or canonical_state_path
        ).resolve(strict=False)
        if os.path.normcase(str(state_path)) != os.path.normcase(
            str(canonical_state_path)
        ):
            return {**result, "reasonCode": "RUNNER_STATE_PATH_NOT_ALLOWED"}
    result["runnerState"]["path"] = str(state_path)
    if state_path.exists() or state_path.is_symlink():
        if not state_path.is_file() or _is_reparse_or_symlink(state_path):
            return {**result, "reasonCode": "RUNNER_STATE_INVALID"}
        try:
            raw_state = state_path.read_bytes()
            if len(raw_state) > 1024 * 1024:
                raise ValueError("runner state exceeds bounded size")
            state = json.loads(raw_state.decode("utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return {**result, "reasonCode": "RUNNER_STATE_INVALID"}
        if not isinstance(state, dict):
            return {**result, "reasonCode": "RUNNER_STATE_INVALID"}
        result["runnerState"] = {
            "path": str(state_path),
            "exists": True,
            "sha256": hashlib.sha256(raw_state).hexdigest(),
            "date": str(state.get("date") or ""),
            "status": str(state.get("status") or ""),
            "runIntent": str(state.get("run_intent") or ""),
            "artifactRoot": str(
                state.get("artifactRoot") or state.get("repo_dir") or ""
            ),
            "opsRoot": str(state.get("opsRoot") or ""),
        }
        if result["runnerState"]["date"] == issue_date:
            role_roots = (
                ("artifactRoot", roots["artifact_root"]),
                ("opsRoot", roots["ops_root"]),
            )
            for field, expected_root in role_roots:
                observed_root = str(result["runnerState"].get(field) or "")
                if not observed_root:
                    continue
                try:
                    observed_resolved = Path(observed_root).resolve(strict=True)
                except (OSError, RuntimeError):
                    return {**result, "reasonCode": "RUNNER_STATE_ROOT_DRIFT"}
                if os.path.normcase(str(observed_resolved)) != os.path.normcase(
                    str(expected_root)
                ):
                    return {**result, "reasonCode": "RUNNER_STATE_ROOT_DRIFT"}

    result.update(ok=True, status="ready", reasonCode="CONTROL_PLANE_READY")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="News-Grasp control-plane preflight")
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--ops-root", type=Path, required=True)
    parser.add_argument("--production-runtime-root", type=Path, required=True)
    parser.add_argument("--live-bin-root", type=Path, required=True)
    parser.add_argument("--issue-date", required=True)
    parser.add_argument("--run-intent", required=True, choices=sorted(ALLOWED_RUN_INTENTS))
    parser.add_argument("--runner-state", type=Path, default=None)
    parser.add_argument("--high-cost-binding", type=Path, default=None)
    parser.add_argument("--high-cost-binding-receipt-sha256", default="")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = verify_control_plane(
        artifact_root=args.artifact_root,
        ops_root=args.ops_root,
        production_runtime_root=args.production_runtime_root,
        live_bin_root=args.live_bin_root,
        issue_date=args.issue_date,
        run_intent=args.run_intent,
        runner_state_path=args.runner_state,
        high_cost_binding_path=args.high_cost_binding,
        high_cost_binding_receipt_sha256=args.high_cost_binding_receipt_sha256,
    )
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_name(f".{args.output.name}.{os.getpid()}.tmp")
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, args.output)
    print(payload, end="")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
