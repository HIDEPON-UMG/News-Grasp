from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from tools.gate_policy import GateAction, classify_gate_failure


MAX_WALL_CLOCK_SEC = 150 * 60
MAX_GATE_ATTEMPTS = 3
MAX_LLM_REPAIR_PER_SIGNATURE = 1
MAX_REFILL_TRANSACTIONS = 2

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


def classify(gate_id: str, output: str) -> dict[str, str]:
    action = classify_gate_failure(gate_id, output)
    table = HANDLER_BY_GATE.get(gate_id, {})
    handler = table.get(action, "targeted-repair" if action == GateAction.REPAIRABLE else "fatal")
    status = {
        "targeted-repair": "blocked_repair_budget_exhausted",
        "deterministic-repair": "blocked_repair_budget_exhausted",
        "quarantine-refill": "blocked_refill_unresolved",
        "external-readiness": "blocked_external_readiness",
        "distribution-retry": "distribution_pending",
        "distribution-failed": "distribution_failed",
        "fatal": "blocked_repair_budget_exhausted",
    }[handler]
    return {"gate_id": gate_id, "action": str(action), "handler": handler, "failure_status": status}


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

    run_parser = sub.add_parser("run-gate")
    run_parser.add_argument("--date", required=True)
    run_parser.add_argument("--gate-id", required=True)
    run_parser.add_argument("--state", type=Path, required=True)
    run_parser.add_argument("command", nargs=argparse.REMAINDER)

    args = parser.parse_args(argv)
    if args.cmd == "classify":
        print(json.dumps(classify(args.gate_id, args.output), ensure_ascii=False, indent=2))
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
