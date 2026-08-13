"""News-Grasp 6:40 audit completion vector と recovery SLO の typed guard。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    # The live runner invokes this verified file directly under isolated mode.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


SCHEMA_VERSION = "NEWS_GRASP_640_COMPLETION_GUARD_V1"
PUBLISH_SCHEMA_VERSION = "NEWS_GRASP_PUBLISH_COMPLETE_V2"
GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_JSON_BYTES = 1024 * 1024
FUTURE_TOLERANCE = timedelta(minutes=5)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_JSON_BYTES:
            raise ValueError("json_too_large")
        value = json.loads(raw.decode("utf-8-sig"))
    except FileNotFoundError:
        return {"_missing": True, "_path": str(path)}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        return {"_invalid": True, "_path": str(path), "_error": str(error)}
    if not isinstance(value, dict):
        return {"_invalid": True, "_path": str(path), "_error": "not_object"}
    value["_path"] = str(path)
    return value


def _parse_clock(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def evaluate(
    manifest: dict[str, Any],
    runner_state: dict[str, Any],
    issue_date: str,
    *,
    audit_accepted_at: str | None = None,
    public_green_at: str | None = None,
    done_at: str | None = None,
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
    if manifest.get("schemaVersion") != PUBLISH_SCHEMA_VERSION:
        failures.append("publish_complete_schema_invalid")
    if manifest.get("date") != issue_date:
        failures.append("manifest_date_mismatch")
    if manifest.get("ok") is not True:
        failures.append("publish_complete_not_ok")
    if str(manifest.get("public_status") or "").lower() != "green":
        failures.append("public_status_not_green")

    scheduled_status = str(manifest.get("scheduled_attempt_status") or "")
    recovery_status = str(manifest.get("recovery_attempt_status") or "")
    if scheduled_status not in {"succeeded", "failed_then_recovered"}:
        failures.append("scheduled_attempt_status_unacceptable")
    if recovery_status not in {"not_required", "succeeded"}:
        failures.append("recovery_attempt_status_unacceptable")
    if runner_state.get("scheduled_attempt_status") != scheduled_status:
        failures.append("scheduled_attempt_status_mismatch")
    if runner_state.get("recovery_attempt_status") != recovery_status:
        failures.append("recovery_attempt_status_mismatch")

    distribution = manifest.get("distribution_artifacts")
    if not isinstance(distribution, dict) or distribution.get("missing"):
        failures.append("distribution_artifacts_missing")
    publish = manifest.get("publish")
    if not isinstance(publish, dict) or publish.get("ok") is not True:
        failures.append("publish_probe_not_ok")
        publish = {}
    notification = manifest.get("notification")
    if not isinstance(notification, dict) or notification.get("ok") is not True:
        failures.append("notification_not_ok")
    podcasts = manifest.get("podcasts")
    if not isinstance(podcasts, dict):
        failures.append("podcasts_missing")
    else:
        for kind in ("primary", "deepdive"):
            value = podcasts.get(kind)
            if not isinstance(value, dict) or value.get("ok") is not True:
                failures.append(f"{kind}_podcast_not_ok")
    readiness = manifest.get("live_runner_readiness")
    next_run = readiness.get("next_run_readiness") if isinstance(readiness, dict) else {}
    if not isinstance(readiness, dict) or readiness.get("ok") is not True:
        failures.append("live_runner_readiness_not_ok")
    if not isinstance(next_run, dict) or next_run.get("ok") is not True:
        failures.append("next_run_readiness_not_ok")

    source_commit = str(manifest.get("source_commit") or "")
    artifact_commit = str(manifest.get("artifact_commit") or "")
    publish_commit = str(manifest.get("publish_commit") or "")
    deploy_head = str(publish.get("deploy_head") or "")
    for role, value in (
        ("source", source_commit),
        ("artifact", artifact_commit),
        ("publish", publish_commit),
    ):
        if not GIT_COMMIT_RE.fullmatch(value):
            failures.append(f"{role}_commit_invalid")
    if publish_commit != deploy_head:
        failures.append("publish_commit_deploy_head_mismatch")

    if runner_state.get("date") != issue_date:
        failures.append("runner_state_date_mismatch")
    if runner_state.get("status") != "publish_complete":
        failures.append("runner_state_not_publish_complete")
    if runner_state.get("exit_code") != 0:
        failures.append("runner_state_exit_nonzero")
    if str(runner_state.get("publish_commit") or "") != publish_commit:
        failures.append("runner_state_publish_commit_mismatch")
    if runner_state.get("publish_manifest_path") and manifest.get("_path"):
        if Path(str(runner_state["publish_manifest_path"])).resolve() != Path(
            str(manifest["_path"])
        ).resolve():
            failures.append("runner_state_manifest_path_mismatch")

    clocks = {
        "T0": _parse_clock(audit_accepted_at),
        "Tgreen": _parse_clock(public_green_at),
        "Tdone": _parse_clock(done_at),
    }
    post_green_minutes: float | None = None
    overall_minutes: float | None = None
    if any(value is None for value in clocks.values()):
        failures.append("slo_clock_missing")
    else:
        t0 = clocks["T0"]
        tgreen = clocks["Tgreen"]
        tdone = clocks["Tdone"]
        assert t0 is not None and tgreen is not None and tdone is not None
        if not (t0 <= tgreen <= tdone):
            failures.append("slo_clock_order_invalid")
        else:
            now = datetime.now(timezone.utc)
            if any(value > now + FUTURE_TOLERANCE for value in (t0, tgreen, tdone)):
                failures.append("slo_clock_future_invalid")
            post_green_minutes = (tdone - tgreen).total_seconds() / 60
            overall_minutes = (tdone - t0).total_seconds() / 60
            if post_green_minutes > 15:
                failures.append("post_green_slo_exceeded")
            if overall_minutes > 60:
                failures.append("overall_slo_exceeded")

    return {
        "schemaVersion": SCHEMA_VERSION,
        "ok": not failures,
        "issueDate": issue_date,
        "failures": failures,
        "scheduled_attempt_status": scheduled_status,
        "recovery_attempt_status": recovery_status,
        "public_status": manifest.get("public_status"),
        "runner_status": runner_state.get("status"),
        "source_commit": source_commit,
        "artifact_commit": artifact_commit,
        "publish_commit": publish_commit,
        "slo": {
            "T0": audit_accepted_at or "",
            "Tgreen": public_green_at or "",
            "Tdone": done_at or "",
            "postGreenMinutes": post_green_minutes,
            "overallMinutes": overall_minutes,
            "postGreenLimitMinutes": 15,
            "overallLimitMinutes": 60,
        },
    }


def evaluate_finalization_receipt(
    receipt_path: Path,
    *,
    artifact_root: Path,
    ops_root: Path,
    production_runtime_root: Path,
    live_bin_root: Path,
    runner_state_path: Path,
    runner_script_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """sealed finalization receiptから自己申告でないclock/root/stateを再検証する。"""
    from tools import news_grasp_recovery_receipts

    resolved_receipt = receipt_path.resolve(strict=True)
    if (
        resolved_receipt.parent.name != "publish-complete"
        or resolved_receipt.parent.parent.name != "build"
    ):
        raise ValueError("FINALIZATION_RECEIPT_PATH_INVALID")
    trusted_artifact_root = artifact_root.resolve(strict=True)
    if resolved_receipt.parents[2] != trusted_artifact_root:
        raise ValueError("FINALIZATION_RECEIPT_PATH_INVALID")
    untrusted = _load_json(resolved_receipt)
    if untrusted.get("_invalid") or untrusted.get("_missing"):
        raise ValueError("FINALIZATION_RECEIPT_INVALID")
    receipt = news_grasp_recovery_receipts.validate_finalization_receipt(
        receipt_path=resolved_receipt,
        issue_date=str(untrusted.get("issueDate") or ""),
        artifact_root=trusted_artifact_root,
        ops_root=ops_root,
        production_runtime_root=production_runtime_root,
        live_bin_root=live_bin_root,
        runner_state_path=runner_state_path,
        runner_script_path=runner_script_path,
    )
    issue_date = str(receipt["issueDate"])
    manifest = _load_json(Path(str(receipt["manifestPath"])))
    state = _load_json(Path(str(receipt["runnerStatePath"])))
    if (
        os.path.normcase(str(Path(str(state.get("recovery_finalization_receipt_path") or "")).resolve()))
        != os.path.normcase(str(resolved_receipt))
        or state.get("recovery_finalization_receipt_sha256") != receipt.get("receiptSha256")
        or state.get("scheduled_failure_receipt_path") != receipt.get("scheduledFailureReceiptPath")
        or state.get("scheduled_failure_receipt_sha256") != receipt.get("scheduledFailureReceiptSha256")
        or not news_grasp_recovery_receipts.finalization_state_applied(
            receipt=receipt, live_bin_root=live_bin_root
        )
    ):
        raise ValueError("FINALIZATION_STATE_BINDING_INVALID")
    result = evaluate(
        manifest,
        state,
        issue_date,
        audit_accepted_at=str(receipt["auditAcceptedAt"]),
        public_green_at=str(receipt["publicGreenAt"]),
        done_at=str(state.get("updated_at") or ""),
    )
    result["finalizationReceiptPath"] = str(resolved_receipt)
    result["finalizationReceiptSha256"] = str(receipt["receiptSha256"])
    return result, receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="News-Grasp 6:40 completion guard")
    parser.add_argument("--finalization-receipt", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--ops-root", type=Path, required=True)
    parser.add_argument("--production-runtime-root", type=Path, required=True)
    parser.add_argument("--live-bin-root", type=Path, required=True)
    parser.add_argument("--runner-state", type=Path, required=True)
    parser.add_argument("--runner-script", type=Path, required=True)
    args = parser.parse_args(argv)
    result, receipt = evaluate_finalization_receipt(
        args.finalization_receipt,
        artifact_root=args.artifact_root,
        ops_root=args.ops_root,
        production_runtime_root=args.production_runtime_root,
        live_bin_root=args.live_bin_root,
        runner_state_path=args.runner_state,
        runner_script_path=args.runner_script,
    )
    issue_date = str(receipt["issueDate"])
    artifact_root = args.artifact_root.resolve(strict=True)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    output = Path(str(receipt["completionGuardOutputPath"]))
    expected_output = artifact_root / "build" / "publish-complete" / f"{issue_date}.automation-guard.json"
    if os.path.normcase(str(output.resolve())) != os.path.normcase(str(expected_output.resolve())):
        raise ValueError("COMPLETION_GUARD_OUTPUT_INVALID")
    # A failed re-evaluation must replace any stale Green guard from an older
    # finalization receipt.  Consumers may inspect this fixed path, so leaving
    # a prior ok=true document behind would turn a current failure into a
    # false completion signal.
    from tools import news_grasp_recovery_receipts

    news_grasp_recovery_receipts.write_atomic_json(
        output, result, root=artifact_root
    )
    print(text, end="")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
