from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
OPERATIONAL_CONTRACT_PATH = REPO_ROOT / "tools" / "news_grasp_operational_contract.py"
REGISTRY_PATH = REPO_ROOT / "config" / "news_grasp_daily_control_routes.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"IMPORT_CONSUMER_UNAVAILABLE: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def observe_historical_failure(
    *, repo: Path, perspective: str
) -> dict[str, Any]:
    module_path = repo / "tools" / "historical_failure_scenarios.py"
    module = _load_module(module_path, "news_grasp_current_historical_failures")
    scenarios = module.historical_failure_scenarios()
    validations = [
        (scenario, module.validate_historical_evidence(repo, scenario))
        for scenario in scenarios
    ]
    valid = [(scenario, result) for scenario, result in validations if result.valid]
    if not valid:
        raise RuntimeError("CURRENT_HISTORICAL_EVIDENCE_VALID_BASELINE_MISSING")
    index = {"primary": 0, "adversarial": len(valid) // 2, "recovery": -1}[
        perspective
    ]
    scenario, validation = valid[index]
    return {
        "schemaVersion": "CURRENT_IMPORT_OBSERVATION_V1",
        "caseId": "G10",
        "perspective": perspective,
        "consumerPath": str(module_path),
        "consumerSymbol": "validate_historical_evidence",
        "consumerSha256": _sha256(module_path),
        "scenario": dataclasses.asdict(scenario),
        "input": dataclasses.asdict(scenario),
        "result": dataclasses.asdict(validation),
        "returnCode": 0,
        "operationalClosure": "ABSENT",
    }


def observe_operational_principle(
    *, case_id: str, perspective: str
) -> dict[str, Any]:
    module = _load_module(
        OPERATIONAL_CONTRACT_PATH, "current_news_grasp_operational_contract"
    )
    row = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    if case_id == "G12":
        if perspective not in {"primary", "adversarial", "recovery"}:
            raise ValueError(f"IMPORT_PERSPECTIVE_UNROUTED:{perspective}")
    elif case_id == "S124":
        if perspective == "primary":
            row["positiveFixtureRouteIds"] = row["positiveFixtureRouteIds"][:-1]
        elif perspective == "adversarial":
            row["consumerRouteIds"] = [*row["consumerRouteIds"], "direct_legacy"]
        elif perspective == "recovery":
            row["routes"][0]["consumerSymbol"] = "foreign_route_symbol"
        elif perspective != "baseline":
            raise ValueError(f"IMPORT_PERSPECTIVE_UNROUTED:{perspective}")
    else:
        raise ValueError(f"IMPORT_CASE_UNROUTED: {case_id}")
    result = module.validate_operational_registry(row, repo_root=REPO_ROOT)
    return {
        "schemaVersion": "CURRENT_IMPORT_OBSERVATION_V1",
        "caseId": case_id,
        "perspective": perspective,
        "consumerPath": str(OPERATIONAL_CONTRACT_PATH),
        "consumerSymbol": "validate_operational_registry",
        "consumerSha256": _sha256(OPERATIONAL_CONTRACT_PATH),
        "registryPath": str(REGISTRY_PATH),
        "registrySha256": _sha256(REGISTRY_PATH),
        "input": row,
        "result": result,
        "returnCode": 0 if result.get("status") == "Green" else 2,
    }
