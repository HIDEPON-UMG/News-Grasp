from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


@pytest.fixture
def canonical_model_broker(tmp_path: Path) -> tuple[list[str], dict[str, str]]:
    """本番wrapperと同じcanonical broker境界を外部modelなしで再演する。"""
    broker = tmp_path / "bin" / "ai-model-spawn-broker.py"
    workspace = tmp_path / "workspace"
    registry = workspace / "docs" / "harness" / "high_cost_model_routes_v1.json"
    admission = tmp_path / "scheduled-operation-admission.json"
    broker.parent.mkdir(parents=True, exist_ok=True)
    registry.parent.mkdir(parents=True, exist_ok=True)
    broker.write_text(
        "import json\n"
        "import subprocess\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        "if args and args[0] == 'admit':\n"
        "    operation_kind = args[args.index('--operation-kind') + 1]\n"
        "    issue_date = args[args.index('--issue-date') + 1]\n"
        "    print(json.dumps({'schemaVersion': 'HIGH_COST_SCHEDULED_OPERATION_ADMISSION_V1', 'operationKind': operation_kind, 'issueDate': issue_date}))\n"
        "    raise SystemExit(0)\n"
        "if not args or args[0] != 'exec':\n"
        "    raise SystemExit(97)\n"
        "try:\n"
        "    executable = args[args.index('--executable') + 1]\n"
        "    separator = args.index('--')\n"
        "except (ValueError, IndexError):\n"
        "    raise SystemExit(98)\n"
        "raise SystemExit(subprocess.run([executable, *args[separator + 1:]]).returncode)\n",
        encoding="utf-8",
    )
    registry.write_text("{}\n", encoding="utf-8")
    admission.write_text("{}\n", encoding="utf-8")
    env = os.environ.copy()
    env["USERPROFILE"] = str(tmp_path)
    args = [
        "-HighCostWorkspaceRoot", str(workspace),
        "-HighCostBudgetToolPath", str(broker),
        "-HighCostPythonExe", sys.executable,
        "-HighCostCallId", f"test-{tmp_path.name}",
        "-HighCostAdmissionPath", str(admission),
        "-HighCostExpectedOperationKind", "full_e2e",
    ]
    return args, env
