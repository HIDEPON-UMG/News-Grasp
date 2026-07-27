from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from tools.gate_policy import GateAction, classify_gate_failure
from tools.repair_coverage_matrix import (
    RepairClass,
    RepairDecision,
    classify_gate_issues,
    classify_gate_output,
    structured_gate_payload,
)
from tools.repair_registry import UNIMPLEMENTED_STATUS, metadata as repair_metadata


MAX_WALL_CLOCK_SEC = 150 * 60
MAX_GATE_ATTEMPTS = 3
MAX_LLM_REPAIR_PER_SIGNATURE = 1
MAX_REFILL_TRANSACTIONS = 2


def _audit_current_repair_system():
    from tools.repair_system_completeness import audit_repair_system

    return audit_repair_system()


def _repair_completeness_failure_payload(report) -> dict[str, Any]:
    return {
        "gate_id": "repair-system-completeness",
        "issue_code": "repair_system_incomplete",
        "repair_class": str(RepairClass.TYPED_FATAL),
        "action": str(GateAction.FATAL),
        "handler": "fatal",
        "failure_status": "blocked_repair_system_incomplete",
        "audit_failed": True,
        "findings": [
            {"code": finding.code, "detail": finding.detail}
            for finding in report.findings
        ],
    }

HANDLER_BY_GATE: dict[str, dict[str, str]] = {
    "daily-quality": {
        GateAction.REPAIRABLE: "targeted-repair",
        GateAction.QUARANTINE: "quarantine-refill",
        GateAction.FATAL: "fatal",
    },
    "generation-quality": {
        GateAction.REPAIRABLE: "targeted-repair",
        GateAction.QUARANTINE: "quarantine-refill",
        GateAction.FATAL: "fatal",
    },
    "url-liveness": {
        GateAction.REPAIRABLE: "targeted-repair",
        GateAction.QUARANTINE: "quarantine-refill",
        GateAction.FATAL: "external-readiness",
    },
    "record-schema": {
        GateAction.REPAIRABLE: "deterministic-repair",
        GateAction.QUARANTINE: "quarantine-refill",
        GateAction.FATAL: "fatal",
    },
    "digest-articles-reconcile": {
        GateAction.REPAIRABLE: "targeted-repair",
        GateAction.QUARANTINE: "quarantine-refill",
        GateAction.FATAL: "fatal",
    },
    "ja-callout": {
        GateAction.REPAIRABLE: "targeted-repair",
        GateAction.QUARANTINE: "quarantine-refill",
        GateAction.FATAL: "fatal",
    },
    "pytest-static": {
        GateAction.REPAIRABLE: "targeted-repair",
        GateAction.QUARANTINE: "quarantine-refill",
        GateAction.FATAL: "fatal",
    },
    "deepdive-required": {
        GateAction.REPAIRABLE: "targeted-repair",
        GateAction.QUARANTINE: "quarantine-refill",
        GateAction.FATAL: "fatal",
    },
    "public-html": {
        GateAction.REPAIRABLE: "targeted-repair",
        GateAction.QUARANTINE: "quarantine-refill",
        GateAction.FATAL: "fatal",
    },
    "availability": {
        GateAction.REPAIRABLE: "distribution-retry",
        GateAction.QUARANTINE: "distribution-retry",
        GateAction.FATAL: "distribution-failed",
    },
    "youtube-podcast": {
        GateAction.REPAIRABLE: "distribution-retry",
        GateAction.QUARANTINE: "distribution-retry",
        GateAction.FATAL: "distribution-failed",
    },
}


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    tmp.replace(path)


def _decision_ledger_entry(decision: RepairDecision) -> dict[str, Any]:
    return {
        "gate_id": decision.gate_id,
        "issue_code": decision.issue_code,
        "repair_class": str(decision.repair_class),
        "handler_id": decision.handler_id,
        "artifact_paths": list(decision.artifact_paths),
        "issue_date": decision.issue_date,
        "category": decision.category,
        "allowed_artifacts": list(decision.allowed_artifacts),
        "verify_gate": decision.verify_gate,
        "failure_status": decision.status_on_failure,
        "reason": decision.reason,
        "direction": str(decision.evidence.get("direction") or ""),
        "evidence": dict(decision.evidence),
    }


def _payload_from_decision(decision: RepairDecision, *, decisions: list[RepairDecision] | None = None) -> dict[str, Any]:
    if decision.repair_class == RepairClass.DETERMINISTIC_HANDLER:
        if decision.handler_id == "url-quarantine-refill":
            handler = "deterministic-repair"
            action = GateAction.QUARANTINE
        else:
            handler = "deterministic-repair"
            action = GateAction.REPAIRABLE
    elif decision.repair_class == RepairClass.LLM_GENERATE_MISSING_ARTIFACT:
        handler = "targeted-repair"
        action = GateAction.REPAIRABLE
    elif decision.repair_class == RepairClass.LLM_REWRITE_EXISTING_ARTIFACT:
        handler = "targeted-repair"
        action = GateAction.REPAIRABLE
    elif decision.repair_class == RepairClass.TYPED_EXTERNAL:
        handler = "external-readiness"
        action = GateAction.FATAL
    else:
        handler = "fatal"
        action = GateAction.FATAL

    selected_artifacts = list(decision.artifact_paths)
    if decisions is not None:
        selected_artifacts = []
        for item in decisions:
            for artifact in item.artifact_paths:
                if artifact not in selected_artifacts:
                    selected_artifacts.append(artifact)

    payload: dict[str, Any] = {
        "gate_id": decision.gate_id,
        "issue_code": decision.issue_code,
        "repair_class": str(decision.repair_class),
        "action": str(action),
        "handler": handler,
        "failure_status": decision.status_on_failure,
        "artifact_paths": list(decision.artifact_paths),
        "selected_artifacts": selected_artifacts,
        "issue_date": decision.issue_date,
        "category": decision.category,
        "verify_gate": decision.verify_gate,
        "reason": decision.reason,
        "external_kind": decision.external_kind,
        "external_system": decision.external_system,
        "evidence": decision.evidence,
    }
    if decision.handler_id:
        payload["handler_id"] = decision.handler_id
        registry_meta = repair_metadata(decision.handler_id) or {}
        payload["handler_kind"] = registry_meta.get("handler_kind", "")
        payload["allowed_artifacts"] = list(decision.allowed_artifacts)
        payload["registry_allowed_artifacts"] = list(registry_meta.get("allowed_artifacts") or [])
        payload["registry_verify_gate"] = str(registry_meta.get("verify_gate") or "")
        payload["registry_supported_verify_gates"] = list(
            registry_meta.get("supported_verify_gates") or []
        )
    elif decision.allowed_artifacts:
        payload["allowed_artifacts"] = list(decision.allowed_artifacts)
    if decisions is not None:
        payload["issues"] = [_decision_ledger_entry(item) for item in decisions]
    return payload


def classify(gate_id: str, output: str) -> dict[str, Any]:
    completeness = _audit_current_repair_system()
    if not completeness.ok:
        return _repair_completeness_failure_payload(completeness)

    decisions = classify_gate_issues(gate_id, output)
    decision = decisions[0] if decisions else classify_gate_output(gate_id, output)
    if decision.status_on_failure != "blocked_unknown_repair_class":
        return _payload_from_decision(decision, decisions=decisions)

    # Structured output の issue_code は validator/matrix が唯一の正本。
    # message prose から別 class へ救済すると unknown の発生源が隠れるため禁止する。
    if structured_gate_payload(output) is not None:
        return _payload_from_decision(decision, decisions=decisions)

    # 既存 unstructured output 互換: URL 404/410 等の明示 quarantine だけは旧 gate policy へ委譲する。
    # 未知 failure を repairable へ倒す用途には使わない。
    action = classify_gate_failure(gate_id, output)
    if action == GateAction.QUARANTINE:
        return {
            "gate_id": gate_id,
            "issue_code": "url_dead_or_stale",
            "repair_class": str(RepairClass.DETERMINISTIC_HANDLER),
            "action": str(action),
            "handler": "quarantine-refill",
            "handler_id": "url-quarantine-refill",
            "failure_status": "blocked_refill_unresolved",
            **(repair_metadata("url-quarantine-refill") or {}),
        }
    if action == GateAction.FATAL:
        return {
            "gate_id": gate_id,
            "issue_code": "unknown",
            "repair_class": str(RepairClass.TYPED_FATAL),
            "action": str(action),
            "handler": "fatal",
            "failure_status": "blocked_unknown_repair_class",
        }

    return _payload_from_decision(decision, decisions=decisions)


def _legacy_classify(gate_id: str, output: str) -> dict[str, Any]:
    action = classify_gate_failure(gate_id, output)
    table = HANDLER_BY_GATE.get(gate_id, {})
    handler = table.get(action, "targeted-repair" if action == GateAction.REPAIRABLE else "fatal")
    status = {
        "targeted-repair": "blocked_repair_budget_exhausted",
        "deterministic-repair": UNIMPLEMENTED_STATUS,
        "quarantine-refill": "blocked_refill_unresolved",
        "external-readiness": "blocked_external_readiness",
        "distribution-retry": "distribution_pending",
        "distribution-failed": "distribution_failed",
        "fatal": "blocked_repair_budget_exhausted",
    }[handler]
    result: dict[str, Any] = {"gate_id": gate_id, "action": str(action), "handler": handler, "failure_status": status}
    return result


def run_gate(*, date: str, gate_id: str, state_path: Path, command: list[str]) -> dict[str, Any]:
    started = time.monotonic()
    cp = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    output = "\n".join(part for part in [cp.stdout, cp.stderr] if part)
    if cp.returncode == 0:
        result = {"ok": True, "gate_id": gate_id, "returncode": 0}
    else:
        result = {
            "ok": False,
            "gate_id": gate_id,
            "returncode": cp.returncode,
            "elapsed_sec": round(time.monotonic() - started, 3),
            **classify(gate_id, output),
        }
    state = {"date": date, "updated_at_monotonic": time.monotonic(), "last_result": result}
    _atomic_write_json(state_path, state)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="News-Grasp autonomous gate classifier/orchestrator.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    classify_parser = sub.add_parser("classify")
    classify_parser.add_argument("--gate-id", required=True)
    classify_parser.add_argument("--output", default="")
    classify_parser.add_argument("--output-file", type=Path)

    run_parser = sub.add_parser("run-gate")
    run_parser.add_argument("--date", required=True)
    run_parser.add_argument("--gate-id", required=True)
    run_parser.add_argument("--state", type=Path, required=True)
    run_parser.add_argument("command", nargs=argparse.REMAINDER)

    args = parser.parse_args(argv)
    if args.cmd == "classify":
        output = args.output
        if args.output_file is not None:
            output = args.output_file.read_text(encoding="utf-8", errors="replace")
        payload = classify(args.gate_id, output)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        # classify の成功は「typed decision を返せた」ことを意味する。
        # audit failure 自体は typed_fatal payload として runner が terminal state に保存する。
        return 0
    if args.cmd == "run-gate":
        command = list(args.command)
        if command and command[0] == "--":
            command = command[1:]
        if not command:
            print("run-gate requires a command after --", file=sys.stderr)
            return 2
        result = run_gate(date=args.date, gate_id=args.gate_id, state_path=args.state, command=command)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    raise AssertionError(args.cmd)


if __name__ == "__main__":
    raise SystemExit(main())
