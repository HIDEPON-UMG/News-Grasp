#!/usr/bin/env python3
"""runner から gate 失敗の retry budget を記録・判定する CLI。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tools.gate_contract import (
    DEFAULT_MAX_CATEGORY_FAILURES,
    DEFAULT_MAX_SAME_SIGNATURE_RETRIES,
    GateFailure,
    record_gate_failure,
)


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "gates": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "gates": {}}
    return data if isinstance(data, dict) else {"version": 1, "gates": {}}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="News-Grasp runner gate retry budget")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--gate-id", required=True)
    parser.add_argument("--category", default="global")
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--artifact-identity", default="")
    parser.add_argument("--output-file", type=Path, required=True)
    parser.add_argument("--non-retryable", action="store_true")
    parser.add_argument("--max-same-signature-retries", type=int, default=DEFAULT_MAX_SAME_SIGNATURE_RETRIES)
    parser.add_argument("--max-category-failures", type=int, default=DEFAULT_MAX_CATEGORY_FAILURES)
    args = parser.parse_args(argv)

    output = args.output_file.read_text(encoding="utf-8", errors="replace") if args.output_file.exists() else ""
    failure = GateFailure(
        gate_id=args.gate_id,
        category=args.category,
        artifact_paths=tuple(args.artifact),
        output=output,
        retryable=not args.non_retryable,
        artifact_identity=args.artifact_identity,
    )
    state = _load_state(args.state)
    decision = record_gate_failure(
        state,
        failure,
        repo_root=args.repo_root,
        max_same_signature_retries=args.max_same_signature_retries,
        max_category_failures=args.max_category_failures,
    )
    _save_state(args.state, state)
    print(json.dumps(decision.__dict__, ensure_ascii=False, sort_keys=True))
    return 0 if decision.retry_allowed else 20


if __name__ == "__main__":
    sys.exit(main())
