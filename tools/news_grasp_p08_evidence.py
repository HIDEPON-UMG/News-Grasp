"""P08の高コスト証跡を実測して固定するproducer。

このproducerはGreen receiptを手書きしない。設計・route・静的検証・simulationを
実際のsourceと実コマンドへ束縛し、公式admission bridgeが再計算できるJSONだけを
atomicに出力する。NoPublish E2Eの起動はこのモジュールの責務ではない。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable


DESIGN_SCHEMA = "MAXIMUM_EFFICIENCY_TASK_DESIGN_V1"
ROUTE_MANIFEST_SCHEMA = "HIGH_COST_ROUTE_MANIFEST_V1"
REVIEW_SCHEMA = "ADVERSARIAL_HIGH_COST_REVIEW_V1"
STATIC_SCHEMA = "HIGH_COST_STATIC_VERIFICATION_V1"
SIMULATION_SCHEMA = "HIGH_COST_SIMULATION_VERIFICATION_V1"
P08_MANIFEST_SCHEMA = "NEWS_GRASP_P08_EVIDENCE_MANIFEST_V1"
GLOBAL_REVIEW_SHA256 = "52d373c852d9f864679f1f7ad50700f877bc3c1935fba05a9e647ddaf6055050"
GLOBAL_BUDGET_SHA256 = "8e12cc48a0416204a1a662a128cf190d6df8c596267e6a660ca0ce415fa409f5"
MAX_COMMAND_SECONDS = 180
MAX_COMMAND_OUTPUT_BYTES = 4 * 1024 * 1024
CALLER_EVIDENCE_KINDS = (
    "efficiency_design",
    "adversarial_review",
    "route_manifest",
    "red_suite_coverage",
    "static",
    "simulation",
    "isolation",
)
RESOURCE_FIELDS = {
    "externalModelCalls",
    "fullE2EAttempts",
    "wallClockSeconds",
    "usageDelta",
    "tempBytes",
    "peakMemoryBytes",
    "externalMutations",
}
LIMIT_FIELDS = {
    "maxExternalModelCalls",
    "maxFullE2EAttempts",
    "maxWallClockSeconds",
    "maxUsageDelta",
    "maxTempBytes",
    "maxPeakMemoryBytes",
    "maxExternalMutations",
}


class P08EvidenceError(RuntimeError):
    """P08 upstream evidence is not source-bound or is not Green."""


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.resolve(strict=True).read_bytes()).hexdigest()


def _canonical_workspace_root(candidate: Path) -> Path:
    parents = Path(__file__).resolve().parents
    expected = (
        parents[3].resolve(strict=True)
        if len(parents) > 3
        and (parents[3] / "tools" / "harness" / "high_cost_operation_budget.py").is_file()
        else None
    )
    actual = candidate.resolve(strict=True)
    if expected is None or actual != expected:
        raise P08EvidenceError("HIGH_COST_WORKSPACE_ROOT_INVALID")
    return actual


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _seal(value: dict[str, Any], field: str) -> dict[str, Any]:
    value[field] = canonical_sha256({key: item for key, item in value.items() if key != field})
    return value


def _task_identity(*, workspace_root: Path, thread_id: str, task_root_user_event_hash: str, operation_class: str) -> str:
    return canonical_sha256(
        {
            "schemaVersion": "DURABLE_HIGH_COST_TASK_IDENTITY_V1",
            "workspaceRootSha256": canonical_sha256(str(workspace_root.resolve()).casefold()),
            "threadId": thread_id,
            "taskRootUserEventHash": task_root_user_event_hash,
            "operationClass": operation_class,
        }
    )


def build_design(
    *, workspace_root: Path, thread_id: str, task_root_user_event_hash: str,
    latest_actual_user_event_hash: str | None = None,
) -> dict[str, Any]:
    if len(task_root_user_event_hash) != 64 or any(c not in "0123456789abcdef" for c in task_root_user_event_hash.lower()):
        raise P08EvidenceError("HIGH_COST_DURABLE_TASK_IDENTITY_INVALID")
    latest = latest_actual_user_event_hash or task_root_user_event_hash
    if len(latest) != 64 or any(c not in "0123456789abcdef" for c in latest.lower()):
        raise P08EvidenceError("HIGH_COST_DURABLE_TASK_IDENTITY_INVALID")
    operation_class = "news_grasp_0640_p08_final_nopublish"
    requirements = [
        "R01_single_operation_owner", "R02_completion_single_writer", "R03_precommit_finalization",
        "R04_invalid_receipt_start_zero", "R05_public_readiness_slo_separation", "R06_automation_read_only",
        "R07_capsule_immutability", "R08_source_installed_task_parity", "R09_historical_replay_finite",
        "R10_bounded_human_impact", "P08_upstream_evidence_provenance",
    ]
    route_ids = ["bridge_admission", "runner_entry", "installed_launcher", "final_nopublish", "budget_gate"]
    chosen = {
        "strategyId": "local_evidence_first",
        "coveredRequirementIds": requirements,
        "reusedEvidenceIds": ["red_suite_coverage", "isolation", "run_envelope", "release_reflection"],
        "externalModelCalls": 0,
        "fullE2EAttempts": 0,
        "wallClockSeconds": 1800,
        "usageDelta": 0,
        "tempBytes": 128 * 1024 * 1024,
        "peakMemoryBytes": 768 * 1024 * 1024,
        "externalMutations": 0,
    }
    comparison = {
        "strategyId": "external_review_first",
        "coveredRequirementIds": requirements,
        "reusedEvidenceIds": [],
        "externalModelCalls": 1,
        "fullE2EAttempts": 1,
        "wallClockSeconds": 1200,
        "usageDelta": 64,
        "tempBytes": 96 * 1024 * 1024,
        "peakMemoryBytes": 896 * 1024 * 1024,
        "externalMutations": 0,
    }
    value: dict[str, Any] = {
        "schemaVersion": DESIGN_SCHEMA,
        "workspaceRootSha256": canonical_sha256(str(workspace_root.resolve()).casefold()),
        "taskIdentity": _task_identity(
            workspace_root=workspace_root, thread_id=thread_id,
            task_root_user_event_hash=task_root_user_event_hash, operation_class=operation_class,
        ),
        "threadId": thread_id,
        "taskRootUserEventHash": task_root_user_event_hash,
        "latestActualUserEventHash": latest,
        "operationClass": operation_class,
        "requirementIds": requirements,
        "candidateStrategies": [chosen, comparison],
        "chosenStrategyId": chosen["strategyId"],
        "localCriticalPathStrategyId": chosen["strategyId"],
        "budgetLimits": {
            "maxExternalModelCalls": 0, "maxFullE2EAttempts": 1, "maxWallClockSeconds": 3600,
            "maxUsageDelta": 0, "maxTempBytes": 256 * 1024 * 1024,
            "maxPeakMemoryBytes": 1024 * 1024 * 1024, "maxExternalMutations": 0,
        },
        "policies": {
            "evidenceReuse": "reuse_before_regenerate",
            "fullE2E": "final_confirmation_only",
            "usageComplaint": "defer_high_cost_continue_local",
            "unknownCost": "reject_high_cost",
            "monitoring": "boundary_event_only_no_polling",
            "noFocusTheft": True,
        },
        "requiredRouteIds": route_ids,
    }
    return _seal(value, "designSha256")


def build_route_manifest(
    *, workspace_root: Path, task_identity: str,
    route_specs: Iterable[tuple[str, Path, str, str]], required_route_ids: list[str],
) -> dict[str, Any]:
    routes: list[dict[str, str]] = []
    root = workspace_root.resolve(strict=True)
    for route_id, source_path, gate, launch in route_specs:
        source = source_path.resolve(strict=True)
        try:
            source.relative_to(root)
        except ValueError as error:
            raise P08EvidenceError("HIGH_COST_ROUTE_OUTSIDE_WORKSPACE") from error
        if source.is_symlink():
            raise P08EvidenceError("HIGH_COST_ROUTE_SOURCE_DRIFT")
        raw = source.read_bytes()
        text = raw.decode("utf-8-sig")
        if not gate or not launch or gate not in text or launch not in text:
            raise P08EvidenceError("HIGH_COST_ROUTE_SENTINEL_MISSING")
        if text.index(gate) >= text.index(launch):
            raise P08EvidenceError("HIGH_COST_GATE_AFTER_LAUNCH")
        routes.append({
            "routeId": route_id, "sourcePath": str(source), "sourceSha256": hashlib.sha256(raw).hexdigest(),
            "admissionSentinel": gate, "launchSentinel": launch,
        })
    if [row["routeId"] for row in routes] != required_route_ids:
        raise P08EvidenceError("HIGH_COST_ROUTE_SET_MISMATCH")
    return _seal({"schemaVersion": ROUTE_MANIFEST_SCHEMA, "taskIdentity": task_identity, "routes": routes}, "manifestSha256")


def validate_route_manifest(value: dict[str, Any], *, workspace_root: Path, task_identity: str, required_route_ids: list[str]) -> None:
    expected = canonical_sha256({key: item for key, item in value.items() if key != "manifestSha256"})
    if value.get("schemaVersion") != ROUTE_MANIFEST_SCHEMA or value.get("taskIdentity") != task_identity or value.get("manifestSha256") != expected:
        raise P08EvidenceError("HIGH_COST_ROUTE_MANIFEST_HASH_INVALID")
    for row in value.get("routes", []):
        source = Path(str(row.get("sourcePath") or ""))
        if not source.is_file() or source.is_symlink():
            raise P08EvidenceError("HIGH_COST_ROUTE_SOURCE_DRIFT")
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row.get("sourceSha256"):
            raise P08EvidenceError("HIGH_COST_ROUTE_SOURCE_DRIFT")
        text = raw.decode("utf-8-sig")
        gate = str(row.get("admissionSentinel") or "")
        launch = str(row.get("launchSentinel") or "")
        if not gate or not launch or gate not in text or launch not in text:
            raise P08EvidenceError("HIGH_COST_ROUTE_SENTINEL_MISSING")
        if text.index(gate) >= text.index(launch):
            raise P08EvidenceError("HIGH_COST_GATE_AFTER_LAUNCH")
    if [row.get("routeId") for row in value.get("routes", [])] != required_route_ids:
        raise P08EvidenceError("HIGH_COST_ROUTE_SET_MISMATCH")


def run_verification_command(*, schema: str, command: list[str], cwd: Path) -> dict[str, Any]:
    started = time.monotonic()
    stdout = b""
    stderr = b""
    exit_code = 127
    timed_out = False
    try:
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            process = subprocess.Popen(
                command,
                cwd=str(cwd.resolve(strict=True)),
                shell=False,
                stdout=stdout_file,
                stderr=stderr_file,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            try:
                process.wait(timeout=MAX_COMMAND_SECONDS)
            except subprocess.TimeoutExpired:
                timed_out = True
                process.kill()
                process.wait(timeout=10)
            exit_code = 124 if timed_out else int(process.returncode)
            stdout_file.seek(0, os.SEEK_END)
            stdout_size = stdout_file.tell()
            stderr_file.seek(0, os.SEEK_END)
            stderr_size = stderr_file.tell()
            if stdout_size > MAX_COMMAND_OUTPUT_BYTES or stderr_size > MAX_COMMAND_OUTPUT_BYTES:
                exit_code = 125
            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout = stdout_file.read(MAX_COMMAND_OUTPUT_BYTES)
            stderr = stderr_file.read(MAX_COMMAND_OUTPUT_BYTES)
    except (OSError, subprocess.SubprocessError) as error:
        stderr = str(error).encode("utf-8", "replace")[:MAX_COMMAND_OUTPUT_BYTES]
    value: dict[str, Any] = {
        "schemaVersion": schema,
        "status": "Green" if exit_code == 0 else "Red",
        "command": command,
        "cwd": str(cwd.resolve()),
        "exitCode": exit_code,
        "stdoutSha256": hashlib.sha256(stdout).hexdigest(),
        "stderrSha256": hashlib.sha256(stderr).hexdigest(),
        "stdoutBytes": len(stdout),
        "stderrBytes": len(stderr),
        "timedOut": timed_out,
        "elapsedMilliseconds": int((time.monotonic() - started) * 1000),
        "executionMode": "bounded_local_subprocess",
        "externalModelCalls": 0,
        "externalMutations": 0,
    }
    return _seal(value, "receiptSha256")


def caller_evidence_bindings(paths: dict[str, Path]) -> list[dict[str, str]]:
    if tuple(paths) != CALLER_EVIDENCE_KINDS:
        raise P08EvidenceError("E2E_UPSTREAM_EVIDENCE_INCOMPLETE")
    return [{"kind": kind, "path": str(paths[kind].resolve()), "sha256": file_sha256(paths[kind])} for kind in CALLER_EVIDENCE_KINDS]


def _load_global_module(workspace_root: Path, filename: str, expected_sha256: str) -> Any:
    workspace = _canonical_workspace_root(workspace_root)
    path = (workspace / "tools" / "harness" / filename).resolve(strict=True)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256 or path.is_symlink():
        raise P08EvidenceError("HIGH_COST_GLOBAL_MODULE_BINDING_INVALID")
    module_name = f"_news_grasp_p08_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise P08EvidenceError("HIGH_COST_GLOBAL_MODULE_BINDING_INVALID")
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    # Imports used by the canonical module are resolved only from the verified directory.
    old_path = list(sys.path)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
        if Path(str(getattr(module, "__file__", ""))).resolve() != path:
            raise P08EvidenceError("HIGH_COST_GLOBAL_MODULE_BINDING_INVALID")
        return module
    except Exception as error:  # pragma: no cover - the authority owns detailed codes
        if isinstance(error, P08EvidenceError):
            raise
        raise P08EvidenceError("HIGH_COST_GLOBAL_MODULE_BINDING_INVALID") from error
    finally:
        sys.path[:] = old_path


def _load_user_event_authority(workspace_root: Path, thread_id: str) -> dict[str, Any]:
    try:
        budget = _load_global_module(workspace_root, "high_cost_operation_budget.py", GLOBAL_BUDGET_SHA256)
        return budget.inspect_canonical_user_event_authority(thread_id)
    except Exception as error:  # pragma: no cover - the authority owns detailed codes
        if isinstance(error, P08EvidenceError):
            raise
        raise P08EvidenceError("HIGH_COST_USER_EVENT_AUTHORITY_INVALID") from error


def _validate_sealed_artifact(path: Path, *, schema: str | None = None, require_status: bool = True) -> dict[str, Any]:
    raw = path.resolve(strict=True).read_bytes()
    value = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise P08EvidenceError("P08_EVIDENCE_INVALID")
    if schema is not None and value.get("schemaVersion") != schema:
        raise P08EvidenceError("P08_EVIDENCE_SCHEMA_INVALID")
    if require_status and value.get("status") != "Green":
        raise P08EvidenceError("P08_EVIDENCE_NOT_GREEN")
    receipt_field = "receiptSha256" if "receiptSha256" in value else None
    if receipt_field and value[receipt_field] != canonical_sha256({key: item for key, item in value.items() if key != receipt_field}):
        raise P08EvidenceError("P08_EVIDENCE_HASH_INVALID")
    return value


def _validate_design_and_review(design: dict[str, Any], route: dict[str, Any], review: dict[str, Any], workspace: Path) -> None:
    if design.get("designSha256") != canonical_sha256({key: item for key, item in design.items() if key != "designSha256"}):
        raise P08EvidenceError("HIGH_COST_DESIGN_HASH_INVALID")
    if route.get("manifestSha256") != canonical_sha256({key: item for key, item in route.items() if key != "manifestSha256"}):
        raise P08EvidenceError("HIGH_COST_ROUTE_MANIFEST_HASH_INVALID")
    if review.get("schemaVersion") != REVIEW_SCHEMA or review.get("status") != "Green":
        raise P08EvidenceError("HIGH_COST_REVIEW_INVALID")
    if review.get("taskIdentity") != design.get("taskIdentity") or review.get("designSha256") != design.get("designSha256") or review.get("routeManifestSha256") != route.get("manifestSha256"):
        raise P08EvidenceError("HIGH_COST_REVIEW_IDENTITY_INVALID")
    if review.get("reviewSha256") != canonical_sha256({key: item for key, item in review.items() if key != "reviewSha256"}):
        raise P08EvidenceError("HIGH_COST_REVIEW_HASH_INVALID")


def generate(*, repo_root: Path, workspace_root: Path, output_dir: Path, thread_id: str, issue_date: str) -> dict[str, Any]:
    repo = repo_root.resolve(strict=True)
    workspace = _canonical_workspace_root(workspace_root)
    out = output_dir.resolve()
    try:
        out.relative_to(repo)
    except ValueError as error:
        raise P08EvidenceError("P08_OUTPUT_OUTSIDE_REPO") from error
    authority = _load_user_event_authority(workspace, thread_id)
    design = build_design(
        workspace_root=workspace, thread_id=thread_id,
        task_root_user_event_hash=str(authority["taskRootUserEventHash"]),
        latest_actual_user_event_hash=str(authority["latestActualUserEventHash"]),
    )
    _write_json(out / "efficiency-design.json", design)
    specs = [
        ("bridge_admission", repo / "tools" / "e2e_final_admission_bridge.py", "def issue_admission(", "execute_red_suite("),
        ("runner_entry", repo / "scripts" / "ops" / "invoke-scheduled-equivalent-nopublish.ps1", "Get-CanonicalExistingFile", "& $installedTaskPythonPath"),
        ("installed_launcher", repo / "scripts" / "ops" / "news-grasp-task-launcher.pyw", "def _load_stable_launcher_identity", "subprocess.run("),
        ("final_nopublish", repo / "scripts" / "ops" / "invoke-scheduled-equivalent-nopublish.ps1", "Get-CanonicalExistingFile", "& $installedTaskPythonPath"),
        ("budget_gate", workspace / "tools" / "harness" / "high_cost_operation_budget.py", "def authorize(", "authorization_id = _canonical_sha256("),
    ]
    route = build_route_manifest(workspace_root=workspace, task_identity=design["taskIdentity"], route_specs=specs, required_route_ids=design["requiredRouteIds"])
    _write_json(out / "route-manifest.json", route)
    validate_route_manifest(route, workspace_root=workspace, task_identity=design["taskIdentity"], required_route_ids=design["requiredRouteIds"])
    # The canonical reviewer is the workspace-global source used again by authorize.
    reviewer = _load_global_module(workspace, "high_cost_adversarial_review.py", GLOBAL_REVIEW_SHA256)
    review = reviewer.evaluate(design, route, workspace_root=workspace)
    _validate_design_and_review(design, route, review, workspace)
    _write_json(out / "adversarial-review.json", review)
    static = run_verification_command(
        schema=STATIC_SCHEMA,
        command=[sys.executable, "-m", "pytest", "-q", "tests/test_news_grasp_generation_boundary.py", "tests/test_runner_convergence_contract.py", "tests/test_e2e_final_admission_bridge.py", "tests/test_high_cost_operation_admission_contract.py"],
        cwd=repo,
    )
    _write_json(out / "static-verification.json", static)
    simulation = run_verification_command(
        schema=SIMULATION_SCHEMA,
        command=[sys.executable, "-m", "pytest", "-q", "tests/test_news_grasp_finalization.py", "tests/test_news_grasp_recovery_receipts.py", "tests/test_audit_recovery_control.py", "tests/test_2026_08_14_recovery_replay.py", "tests/test_2026_08_15_recovery_replay.py", "tests/test_2026_08_16_recovery_replay.py"],
        cwd=repo,
    )
    _write_json(out / "simulation-verification.json", simulation)
    coverage_path = repo / "build" / "operational-redesign" / "red-suite-coverage-report.json"
    isolation_path = repo / "build" / "operational-redesign" / "e2e-isolation-20260817.json"
    paths = {
        "efficiency_design": out / "efficiency-design.json",
        "adversarial_review": out / "adversarial-review.json",
        "route_manifest": out / "route-manifest.json",
        "red_suite_coverage": coverage_path,
        "static": out / "static-verification.json",
        "simulation": out / "simulation-verification.json",
        "isolation": isolation_path,
    }
    _validate_sealed_artifact(paths["efficiency_design"], schema=DESIGN_SCHEMA, require_status=False)
    _validate_sealed_artifact(paths["adversarial_review"], schema=REVIEW_SCHEMA)
    _validate_sealed_artifact(paths["static"], schema=STATIC_SCHEMA)
    _validate_sealed_artifact(paths["simulation"], schema=SIMULATION_SCHEMA)
    _validate_sealed_artifact(paths["red_suite_coverage"], require_status=True)
    _validate_sealed_artifact(paths["isolation"], require_status=True)
    bindings = caller_evidence_bindings(paths)
    manifest: dict[str, Any] = {
        "schemaVersion": P08_MANIFEST_SCHEMA,
        "status": "Green" if static["status"] == "Green" and simulation["status"] == "Green" else "Red",
        "issueDate": issue_date,
        "taskIdentity": design["taskIdentity"],
        "threadId": thread_id,
        "userEventAuthoritySha256": authority["authoritySha256"],
        "evidenceBindings": bindings,
        "sourceToolPath": str(Path(__file__).resolve()),
        "sourceToolSha256": file_sha256(Path(__file__)),
    }
    _write_json(out / "caller-evidence-manifest.json", _seal(manifest, "manifestSha256"))
    if manifest["status"] != "Green":
        raise P08EvidenceError("P08_VERIFICATION_NOT_GREEN")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("generate", choices=("generate",))
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--issue-date", required=True)
    args = parser.parse_args(argv)
    try:
        result = generate(repo_root=args.repo_root, workspace_root=args.workspace_root, output_dir=args.output_dir, thread_id=args.thread_id, issue_date=args.issue_date)
    except (P08EvidenceError, OSError, ValueError, ImportError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 80
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
