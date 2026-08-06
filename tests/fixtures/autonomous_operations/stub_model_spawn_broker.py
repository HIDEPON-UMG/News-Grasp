from __future__ import annotations

import os
import hashlib
import json
import sys


def _arg(name: str) -> str:
    index = sys.argv.index(name)
    return sys.argv[index + 1]


def _sealed(body: dict[str, object]) -> dict[str, object]:
    payload = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {**body, "receiptSha256": hashlib.sha256(payload).hexdigest()}


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "record-news-grasp-failure":
        receipt = _sealed(
            {
                "schemaVersion": "SCHEDULED_FAILURE_RECEIPT_V1",
                "productId": "News-Grasp",
                "issueDate": _arg("--issue-date"),
                "runId": _arg("--run-id"),
                "scheduledAttemptStatus": "failed",
                "lastTaskResult": int(_arg("--last-task-result")),
                "runnerState": _arg("--runner-state"),
                "stateSha256": _arg("--state-sha256"),
                "logSha256": _arg("--log-sha256"),
                "taskActionSha256": _arg("--task-action-sha256"),
                "runnerSha256": _arg("--runner-sha256"),
            }
        )
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
        return 0
    mode = os.environ.get("NEWS_GRASP_BROKER_MODE", "reject_exit_1")
    if mode == "reject_exit_1":
        print("HIGH_COST_OPERATION_ADMISSION_REJECTED", file=sys.stderr)
        return 1
    if mode == "accept_invalid_json":
        print("not-json")
        return 0
    print(f"UNKNOWN_STUB_MODE:{mode}", file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main())
