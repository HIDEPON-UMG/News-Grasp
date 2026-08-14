from __future__ import annotations

import os
import sys
import hashlib
import json
import atexit
import shutil
import tempfile
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
_SESSION_BINDING_ROOT: Path | None = None
for _candidate in _REPO_ROOT.parents:
    _adapter = _candidate / "tools" / "harness" / "high_cost_capability_adapter.py"
    _descriptor = (
        Path.home()
        / ".codex"
        / "state"
        / "high-cost-operation"
        / "capability-v1.json"
    )
    if _adapter.is_file() and _descriptor.is_file():
        from tools.news_grasp_high_cost_binding import HighCostBindingError, create_binding

        _SESSION_BINDING_ROOT = Path(
            tempfile.mkdtemp(prefix="news-grasp-test-high-cost-binding-")
        )
        _binding_path = _SESSION_BINDING_ROOT / "binding.json"
        try:
            _binding = create_binding(
                adapter_path=_adapter,
                descriptor_path=_descriptor,
                output_path=_binding_path,
            )
        except HighCostBindingError:
            # test collection / constitution projection は live broker の可用性を
            # production admission として消費しない。high-cost 経路の各fixtureが
            # 明示的な adapter/descriptor/binding を所有する。
            shutil.rmtree(_SESSION_BINDING_ROOT, ignore_errors=True)
            _SESSION_BINDING_ROOT = None
            continue
        os.environ.setdefault("NEWS_GRASP_HIGH_COST_BINDING_PATH", str(_binding_path))
        os.environ.setdefault(
            "NEWS_GRASP_HIGH_COST_BINDING_RECEIPT_SHA256",
            str(_binding["bindingReceiptSha256"]),
        )
        atexit.register(shutil.rmtree, _SESSION_BINDING_ROOT, ignore_errors=True)
        break


def _write_external_health_authority(profile: Path, *, installed_broker: Path | None = None) -> None:
    """wrapper fixtureへ、固定pathの外部health authorityを明示注入する。"""
    profile = profile.resolve()
    authority_path = profile / ".codex" / "state" / "high-cost-operation" / "external-health-authority-v1.json"
    authority_path.parent.mkdir(parents=True, exist_ok=True)
    broker_path = (installed_broker or (profile / "bin" / "ai-model-spawn-broker.py")).resolve()
    body = {
        "schemaVersion": "EXTERNAL_CONTROL_PLANE_HEALTH_AUTHORITY_V1",
        "authorityLineageId": "lineage-a",
        "authorityLineageDerivation": "sha256-utf8-lf-v1",
        "authorityGeneration": 1,
        "previousReceiptSha256": "0" * 64,
        "canonicalDescriptorPath": str((profile / "descriptor.json").resolve()),
        "canonicalDescriptorSha256": "1" * 64,
        "sourceBrokerPath": str(broker_path),
        "sourceBrokerSha256": "2" * 64,
        "installedBrokerPath": str(broker_path),
        "installedBrokerSha256": "2" * 64,
        "dependencyGenerationHash": "3" * 64,
        "routeGenerationHash": "4" * 64,
        "ledgerGenerationId": "ledger-fixture",
        "registryAnchorGenerationId": "registry-fixture",
        "promotionGuardGenerationId": "promotion-fixture",
        "statefulSelfTestStatus": "green",
        "statefulSelfTestId": "self-test-fixture",
        "testedAt": "2026-08-11T00:00:00+00:00",
        "publisherId": "global-control-plane-owner",
    }
    body["receiptSha256"] = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    authority_path.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


@pytest.fixture
def canonical_model_broker(tmp_path: Path) -> tuple[list[str], dict[str, str]]:
    """本番wrapperと同じcanonical broker境界を外部modelなしで再演する。"""
    broker = tmp_path / "bin" / "ai-model-spawn-broker.py"
    workspace = tmp_path / "workspace"
    registry = workspace / "docs" / "harness" / "high_cost_model_routes_v1.json"
    budget_validator = workspace / "tools" / "harness" / "high_cost_operation_budget.py"
    admission = tmp_path / "scheduled-operation-admission.json"
    broker.parent.mkdir(parents=True, exist_ok=True)
    registry.parent.mkdir(parents=True, exist_ok=True)
    budget_validator.parent.mkdir(parents=True, exist_ok=True)
    broker.write_text(
        "import json\n"
        "import subprocess\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        "if args and args[0] == 'admit':\n"
        "    operation_kind = args[args.index('--operation-kind') + 1]\n"
        "    issue_date = args[args.index('--issue-date') + 1]\n"
        "    authority_path = args[args.index('--authority-evidence') + 1]\n"
        "    with open(authority_path, encoding='utf-8-sig') as stream: authority = json.load(stream)\n"
        "    print(json.dumps({'schemaVersion': 'HIGH_COST_SCHEDULED_OPERATION_ADMISSION_V1', 'operationKind': operation_kind, 'issueDate': issue_date, 'operationAuthoritySha256': authority['receiptSha256']}))\n"
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
    broker_source = workspace / "tools" / "harness" / "model_spawn_broker.py"
    broker_source.write_bytes(broker.read_bytes())
    adapter = workspace / "tools" / "harness" / "high_cost_capability_adapter.py"
    descriptor = tmp_path / "descriptor.json"
    descriptor.write_text(
        json.dumps(
            {
                "schemaVersion": "HIGH_COST_CAPABILITY_DESCRIPTOR_V1",
                "workspaceRoot": str(workspace.resolve()),
                "brokerSourcePath": str(broker_source.resolve()),
                "brokerSourceSha256": hashlib.sha256(broker_source.read_bytes()).hexdigest(),
                "brokerInstalledPath": str(broker.resolve()),
                "brokerInstalledSha256": hashlib.sha256(broker.read_bytes()).hexdigest(),
                "reasonSchemaVersion": "HIGH_COST_TYPED_REASON_V1",
                "generation": 1,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    adapter.write_text(
        "from __future__ import annotations\n"
        "import argparse, json\n"
        "from pathlib import Path\n"
        "p=argparse.ArgumentParser(); p.add_argument('command'); p.add_argument('--descriptor', type=Path, required=True)\n"
        "a=p.parse_args(); v=json.loads(a.descriptor.read_text(encoding='utf-8')); v.update({'descriptorPath': str(a.descriptor.resolve()), 'status': 'available'}); print(json.dumps(v, sort_keys=True))\n",
        encoding="utf-8",
    )
    from tools.news_grasp_high_cost_binding import create_binding

    binding_path = tmp_path / "news-grasp-high-cost-binding-v1.json"
    binding_value = create_binding(
        adapter_path=adapter,
        descriptor_path=descriptor,
        output_path=binding_path,
    )
    registry.write_text("{}\n", encoding="utf-8")
    budget_validator.write_text("# fixture sentinel\n", encoding="utf-8")
    (workspace / "tools" / "news_grasp_external_control.py").write_text(
        (Path(__file__).resolve().parent.parent / "tools" / "news_grasp_external_control.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    admission.write_text("{}\n", encoding="utf-8")
    env = os.environ.copy()
    env["USERPROFILE"] = str(tmp_path)
    _write_external_health_authority(tmp_path, installed_broker=broker)
    args = [
        "-HighCostBindingPath", str(binding_path),
        "-HighCostBindingReceiptSha256", binding_value["bindingReceiptSha256"],
        "-HighCostBindingResolverPath", str((_REPO_ROOT / "tools" / "news_grasp_high_cost_binding.py").resolve()),
        "-HighCostBindingResolverSha256", hashlib.sha256(
            (_REPO_ROOT / "tools" / "news_grasp_high_cost_binding.py").read_bytes()
        ).hexdigest(),
        "-HighCostPythonExe", sys.executable,
        "-HighCostCallId", f"test-{tmp_path.name}",
        "-HighCostAdmissionPath", str(admission),
        "-HighCostExpectedOperationKind", "scheduled_production",
    ]
    return args, env
