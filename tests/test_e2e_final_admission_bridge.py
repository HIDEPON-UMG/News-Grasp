from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import runpy
import sys
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from tools import e2e_final_admission_bridge as bridge_module
from tools.deepdive_red_suite_coverage import (
    build_requirement_viewpoint_pair_cases,
    validate_red_suite_coverage,
)
from tools.e2e_final_admission_bridge import (
    E2EFinalAdmissionError,
    consume_admission as _consume_admission,
    issue_admission as _issue_admission,
)
from tools.news_grasp_e2e_attempt_policy import (
    bind_policy_admission,
    issue_logical_attempt,
    new_policy,
    validate_policy_ledger,
)
from tools.red_suite_execution import (
    PAIR_TEST_SELECTOR,
    _fixture_selectors,
    _production_dependency_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "ops" / "invoke-scheduled-equivalent-nopublish.ps1"
BRIDGE = ROOT / "tools" / "e2e_final_admission_bridge.py"
RED_VIEWPOINTS = (
    "normal",
    "failure",
    "boundary",
    "substitution",
    "drift",
    "replay",
    "missing",
    "cross_lineage",
    "recovery",
    "human_impact",
)


@pytest.fixture(autouse=True)
def _synthetic_workspace_anchor(monkeypatch: pytest.MonkeyPatch) -> None:
    """合成repoはworkspace markerを持たないため、正本anchorだけを局所注入する。"""
    monkeypatch.setattr(
        bridge_module,
        "_require_trusted_workspace_root",
        lambda _repo: None,
    )


def _write_json(path: Path, value: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _write_terminal_authority(
    *, admission: Path, arguments: Path, state: Path, policy: Path, ledger: Path, attempt: int = 1
) -> Path:
    value = json.loads(admission.read_text(encoding="utf-8-sig"))
    claim = Path(value["expectedClaimReceiptPath"])
    reservation_path = Path(value["expectedReservationReceiptPath"])
    reservation = json.loads(reservation_path.read_text(encoding="utf-8-sig"))
    executable = Path(value["runnerExecutablePath"])
    owner = {
        "pid": 123,
        "parentPid": 1,
        "creationFileTimeUtc": "fixture",
        "imagePath": str(executable.resolve()),
        "imageSha256": _sha256(executable),
    }
    claim_value = bridge_module._claim_receipt(
        reservation=reservation,
        reservation_path=reservation_path,
        claim_nonce="a" * 64,
        runner_pid=123,
        owner_process_identity=owner,
    )
    _write_json(claim, claim_value)
    ledger_value = {
        "schemaVersion": bridge_module.LEDGER_SCHEMA,
        "attempts": {
            value["attemptKey"]: bridge_module._claim_row(reservation, claim_value, claim)
        },
        "replacements": {},
    }
    _write_json(ledger, ledger_value)
    launcher = ROOT / "scripts" / "ops" / "news-grasp-task-launcher.pyw"
    namespace = runpy.run_path(str(launcher), run_name="_news_grasp_launcher_terminal_fixture")
    return namespace["_write_runner_terminal_authority"](
        policy_path=policy,
        attempt=attempt,
        admission_path=admission,
        runner_arguments_path=arguments,
        runner_state_path=state,
        claim_path=claim,
        process_identity={
            "pid": 123,
            "parentPid": 1,
            "creationTime": "fixture",
            "imagePath": str(Path(value["runnerExecutablePath"]).resolve()),
            "imageSha256": _sha256(Path(value["runnerExecutablePath"])),
        },
        runner_exit_code=0,
        child_launch_evidence={"status": "terminal_state_reached", "childExitCode": 0},
        ledger_path=ledger,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def consume_admission(*, admission_path: Path, ledger_path: Path) -> dict[str, object]:
    """issue後にmaterializeしてvalidate-issuedを通してからconsumeする。"""
    original_require = bridge_module._require_trusted_workspace_root
    bridge_module._require_trusted_workspace_root = lambda _repo: None
    try:
        return _consume_admission_fixture(admission_path=admission_path, ledger_path=ledger_path)
    finally:
        bridge_module._require_trusted_workspace_root = original_require


def _consume_admission_fixture(*, admission_path: Path, ledger_path: Path) -> dict[str, object]:
    """合成repo用の内部materialize実装。"""
    value = json.loads(admission_path.read_text(encoding="utf-8"))
    arguments_path = Path(value["expectedRunnerArgumentsPath"])
    arguments_path.write_bytes(
        bridge_module._canonical_runner_arguments_bytes(
            list(value["runnerArguments"])
        )
    )
    parent_path = Path(value["expectedParentAuthorityPath"])
    _write_json(
        parent_path,
        {
            "schemaVersion": "HIGH_COST_OPERATION_ADMISSION_V1",
            "state": "activated",
            "taskIdentity": "fixture-task",
            "threadId": "fixture-thread",
            "taskRootUserEventHash": "a" * 64,
            "latestActualUserEventHash": "b" * 64,
            "authorizationId": "fixture-authorization",
            "lineageEpoch": 1,
        },
    )
    bridge_module.validate_issued_admission(
        admission_path=admission_path,
        runner_arguments=list(value["runnerArguments"]),
        expected_parent_authority_path=parent_path,
        runner_arguments_path=arguments_path,
        reservation_output=Path(value["expectedReservationReceiptPath"]),
        claim_output=Path(value["expectedClaimReceiptPath"]),
        claim_witness_output=Path(value["expectedClaimWitnessPath"]),
        actual_runner_executable_path=Path(value["runnerExecutablePath"]),
        actual_authority_python_executable_path=Path(
            value["authorityPythonExecutablePath"]
        ),
    )
    return _consume_admission(
        admission_path=admission_path,
        ledger_path=ledger_path,
        runner_arguments=list(value["runnerArguments"]),
        parent_authority_path=parent_path,
        runner_arguments_path=arguments_path,
        reservation_output=Path(value["expectedReservationReceiptPath"]),
        actual_runner_executable_path=Path(value["runnerExecutablePath"]),
        actual_authority_python_executable_path=Path(
            value["authorityPythonExecutablePath"]
        ),
    )


def issue_admission(**kwargs: object) -> dict[str, object]:
    """issue時は4つの将来ファイルを作らず、deterministic pathだけを注入する。"""

    arguments = dict(kwargs)
    repo = Path(str(arguments["repo_root"]))
    output = Path(str(arguments["output_path"]))
    try:
        output.resolve().relative_to(repo.resolve())
    except ValueError:
        mapped_output = repo / ".e2e-final-admissions" / output.name
        if output.exists():
            mapped_output.parent.mkdir(parents=True, exist_ok=True)
            mapped_output.write_bytes(output.read_bytes())
        arguments["output_path"] = mapped_output
        output = mapped_output
    stem = output.stem
    arguments.setdefault(
        "expected_parent_authority_path",
        repo / f"{stem}.high-cost-parent-authority.json",
    )
    arguments.setdefault(
        "runner_arguments_path",
        repo / f"{stem}.runner-arguments.json",
    )
    arguments.setdefault(
        "expected_reservation_receipt_path",
        repo / f"{stem}.e2e-final-reservation.json",
    )
    arguments.setdefault(
        "expected_claim_receipt_path",
        repo / f"{stem}.e2e-final-claim.json",
    )
    arguments.setdefault(
        "runner_executable_path", Path(str(arguments["runner_path"]))
    )
    arguments.setdefault("authority_python_executable_path", Path(sys.executable))
    original_require = bridge_module._require_trusted_workspace_root
    bridge_module._require_trusted_workspace_root = lambda _repo: None
    try:
        return _issue_admission(**arguments)
    finally:
        bridge_module._require_trusted_workspace_root = original_require


def _materialize_runner_arguments_only(admission: Path) -> tuple[dict[str, object], Path]:
    value = json.loads(admission.read_text(encoding="utf-8"))
    arguments_path = Path(value["expectedRunnerArgumentsPath"])
    arguments_path.write_bytes(
        bridge_module._canonical_runner_arguments_bytes(
            list(value["runnerArguments"])
        )
    )
    return value, arguments_path


def _lock_probe_worker(
    local_app_data: str,
    temp_root: str,
    tmp_root: str,
    lock_target: str,
    barrier: object,
    result_queue: object,
) -> None:
    os.environ["LOCALAPPDATA"] = local_app_data
    os.environ["TEMP"] = temp_root
    os.environ["TMP"] = temp_root
    # 合成repoはworkspace markerを持たないため、子processでも同じテストanchorを使う。
    bridge_module._require_trusted_workspace_root = lambda _repo: None
    try:
        ledger = bridge_module.default_attempt_ledger_path()
        barrier.wait(timeout=15)
        started = time.monotonic()
        for _ in range(750):
            try:
                with bridge_module._issue_execution_lock(
                    Path(lock_target), wait_for_lock=False
                ):
                    entered = time.monotonic()
                    Path(tmp_root, f"entered-{os.getpid()}").write_text("1", encoding="utf-8")
                    time.sleep(0.25)
                    exited = time.monotonic()
                break
            except E2EFinalAdmissionError as error:
                if str(error) != "E2E_ADMISSION_ISSUE_BUSY":
                    raise
                time.sleep(0.02)
        else:
            raise RuntimeError("lock probe exhausted")
        result_queue.put(
            {
                "ledger": str(ledger),
                "started": started,
                "entered": entered,
                "exited": exited,
            }
        )
    except BaseException as error:  # pragma: no cover - surfaced by parent assertion
        result_queue.put({"error": repr(error)})


def _consume_claim_process_worker(
    admission_text: str,
    ledger_text: str,
    parent_text: str,
    arguments_text: str,
    local_app_data: str,
    temp_root: str,
    barrier: object,
    result_queue: object,
    nonce: str,
) -> None:
    os.environ["LOCALAPPDATA"] = local_app_data
    os.environ["TEMP"] = temp_root
    os.environ["TMP"] = temp_root
    # 合成repoはworkspace markerを持たないため、子processでも同じテストanchorを使う。
    bridge_module._require_trusted_workspace_root = lambda _repo: None
    admission = Path(admission_text)
    ledger = Path(ledger_text)
    parent = Path(parent_text)
    arguments = Path(arguments_text)
    value = json.loads(admission.read_text(encoding="utf-8"))
    try:
        barrier.wait(timeout=30)
        reserved = _consume_admission(
            admission_path=admission,
            ledger_path=ledger,
            runner_arguments=list(value["runnerArguments"]),
            parent_authority_path=parent,
            runner_arguments_path=arguments,
            reservation_output=Path(value["expectedReservationReceiptPath"]),
            actual_runner_executable_path=Path(value["runnerExecutablePath"]),
            actual_authority_python_executable_path=Path(
                value["authorityPythonExecutablePath"]
            ),
        )
        reserved_state = str(reserved.get("state"))
    except E2EFinalAdmissionError as error:
        reserved_state = f"error:{error}"
    try:
        claimed = bridge_module.claim_runner(
            admission_path=admission,
            ledger_path=ledger,
            runner_arguments=list(value["runnerArguments"]),
            parent_authority_path=parent,
            runner_arguments_path=arguments,
            reservation_receipt=Path(value["expectedReservationReceiptPath"]),
            claim_output=Path(value["expectedClaimReceiptPath"]),
            actual_runner_executable_path=Path(value["runnerExecutablePath"]),
            actual_authority_python_executable_path=Path(
                value["authorityPythonExecutablePath"]
            ),
            current_runner_pid=os.getpid(),
            claim_nonce=nonce,
        )
        claimed_state = str(claimed.get("state"))
    except E2EFinalAdmissionError as error:
        claimed_state = f"error:{error}"
    result_queue.put(
        {
            "ledger": str(bridge_module.default_attempt_ledger_path()),
            "reserved": reserved_state,
            "claimed": claimed_state,
        }
    )


def _configure_spawn_executable() -> None:
    """venv launcherの二重spawnを避け、focused process fixtureを安定させる。"""
    if os.name == "nt":
        base_executable = Path(sys.base_prefix) / "python.exe"
        if base_executable.is_file():
            multiprocessing.set_executable(str(base_executable))


def _install_red_suite_source(repo_root: Path) -> dict[str, object]:
    repo_root.mkdir(parents=True, exist_ok=True)
    test_path = repo_root / "tests" / "red_suite_fixture.py"
    test_path.parent.mkdir(parents=True, exist_ok=True)
    function_names = [f"test_contract_{index}" for index in range(60)]
    test_path.write_text(
        "\n\n".join(
            (
                f"def {name}():\n"
                f"    marker_{index} = {index}\n"
                f"    assert marker_{index} == {index}"
            )
            for index, name in enumerate(function_names)
        )
        + "\n",
        encoding="utf-8",
    )
    pair_test_path = repo_root / PAIR_TEST_SELECTOR.split("::", 1)[0]
    pair_test_path.write_text(
        "def test_requirement_viewpoint_pair_observes_its_own_red():\n"
        "    pass\n",
        encoding="utf-8",
    )
    producer_path = repo_root / "tools" / "red_suite_execution.py"
    producer_path.parent.mkdir(parents=True, exist_ok=True)
    producer_path.write_text("# synthetic execution producer\n", encoding="utf-8")
    shared_routes = [
        "production_generation",
        "repair_publish",
        "daily_quality",
        "codex_daily_audit",
    ]
    route_ids = [*shared_routes, "final_e2e_wrapper"]
    routes = {
        "schemaVersion": "DEEPDIVE_SHARED_QUALITY_ROUTES_V1",
        "declaredRoutes": shared_routes,
    }
    _write_json(repo_root / "config" / "deepdive_quality_routes.json", routes)
    route_rows = [
        {
            "id": route_id,
            "scope": "final_e2e" if route_id == "final_e2e_wrapper" else "shared_quality",
            "fixture": f"tests/red_suite_fixture.py::{function_names[index]}",
            "productionConsumer": f"consumer:{route_id}",
        }
        for index, route_id in enumerate(route_ids)
    ]
    e2e_requirements = [
        "e2e_purpose",
        "e2e_non_purpose",
        "e2e_layer_model",
        "e2e_readiness_admission",
        "e2e_attempt_identity",
        "e2e_checkpoint_boundary",
        "e2e_exploration_separation",
        "e2e_resource_budget",
        "e2e_side_effect_boundary",
        "e2e_stop_and_failure",
        "e2e_evidence_contract",
        "e2e_completion_boundary",
    ]
    requirement_specs = [
        *((requirement_id, ["final_e2e_wrapper"]) for requirement_id in e2e_requirements),
        ("deepdive_url_provenance", shared_routes),
        ("deepdive_rendered_public_surface", shared_routes),
        ("podcast_reader_value", shared_routes),
    ]
    requirements: list[dict[str, object]] = []
    function_index = len(route_rows)
    for requirement_id, requirement_routes in requirement_specs:
        requirements.append(
            {
                "id": requirement_id,
                "acceptanceId": f"acceptance:{requirement_id}",
                "fixture": (
                    "tests/red_suite_fixture.py::"
                    f"{function_names[function_index]}"
                ),
                "productionConsumer": f"consumer:{requirement_id}",
                "expectedRed": f"red:{requirement_id}",
                "counterevidence": f"counter:{requirement_id}",
                "routeIds": list(requirement_routes),
            }
        )
        function_index += 1
    viewpoint_scopes: list[dict[str, object]] = []
    scope_ids = [
        "final_e2e",
        "deepdive_url_provenance",
        "deepdive_rendered_public_surface",
        "podcast_reader_value",
    ]
    for scope_id in scope_ids:
        bindings: list[dict[str, str]] = []
        for viewpoint in RED_VIEWPOINTS:
            bindings.append(
                {
                    "viewpoint": viewpoint,
                    "acceptanceId": f"{scope_id}:{viewpoint}",
                    "fixture": (
                        "tests/red_suite_fixture.py::"
                        f"{function_names[function_index]}"
                    ),
                    "expectedRed": f"red:{scope_id}:{viewpoint}",
                    "counterevidence": f"counter:{scope_id}:{viewpoint}",
                }
            )
            function_index += 1
        viewpoint_scopes.append({"id": scope_id, "bindings": bindings})
    requirement_viewpoint_scopes = {
        requirement_id: (
            "final_e2e"
            if requirement_id in e2e_requirements
            else requirement_id
        )
        for requirement_id, _ in requirement_specs
    }
    matrix = {
        "schemaVersion": "NEWS_GRASP_DEEPDIVE_TDD_ACCEPTANCE_MATRIX_V2",
        "taskIdentity": "synthetic-red-suite",
        "coverageRule": "requirement_viewpoint_route_composite_proof",
        "redSuiteCoverage": {
            "schemaVersion": "RED_SUITE_COVERAGE_V2",
            "requiredViewpoints": list(RED_VIEWPOINTS),
            "viewpoints": [{"id": viewpoint} for viewpoint in RED_VIEWPOINTS],
            "viewpointScopes": viewpoint_scopes,
            "requirementViewpointScopes": requirement_viewpoint_scopes,
            "routes": route_rows,
            "requirements": requirements,
        },
        "historicalFailureCorpus": [],
    }
    _write_json(
        repo_root / "fixtures" / "deepdive_quality" / "tdd_acceptance_matrix.json",
        matrix,
    )
    report = validate_red_suite_coverage(matrix, root=repo_root, route_registry=routes)
    assert report["status"] == "Green", report
    return report


def _synthetic_execution_receipt(repo_root: Path) -> dict[str, object]:
    matrix_path = (
        repo_root / "fixtures" / "deepdive_quality" / "tdd_acceptance_matrix.json"
    )
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    report = validate_red_suite_coverage(matrix, root=repo_root)
    selectors = _fixture_selectors(matrix["redSuiteCoverage"])
    pair_nodes = sorted(
        f"{PAIR_TEST_SELECTOR}[{case['caseId']}]"
        for case in build_requirement_viewpoint_pair_cases(matrix)
    )
    collected = sorted(
        [
            f"{selectors[0]}[case-a]",
            f"{selectors[0]}[case-b]",
            *selectors[1:],
            *pair_nodes,
        ]
    )
    producer_path = repo_root / "tools" / "red_suite_execution.py"
    pair_test_path = repo_root / PAIR_TEST_SELECTOR.split("::", 1)[0]
    production_dependencies = _production_dependency_manifest(repo_root)
    return {
        "schemaVersion": "RED_SUITE_EXECUTION_RECEIPT_V1",
        "status": "Green",
        "createdAt": "2026-08-01T00:00:00+00:00",
        "matrixPath": str(matrix_path.resolve()),
        "matrixSha256": _sha256(matrix_path),
        "coverageSha256": report["coverageSha256"],
        "fixtureSetSha256": report["fixtureSetSha256"],
        "fixtureImplementationSetSha256": report[
            "fixtureImplementationSetSha256"
        ],
        "pairCaseSetSha256": report["pairCaseSetSha256"],
        "historicalCorpusSha256": report["historicalCorpusSha256"],
        "pairCaseMode": "traceability_only",
        "producerSha256": _sha256(producer_path),
        "pairTestSha256": _sha256(pair_test_path),
        "productionDependencyCount": len(production_dependencies),
        "productionDependencySetSha256": hashlib.sha256(
            json.dumps(
                production_dependencies,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "selectorCount": 60,
        "selectorSetSha256": hashlib.sha256(
            json.dumps(
                selectors,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "selectors": selectors,
        "pairCaseCount": 150,
        "pairNodeIds": pair_nodes,
        "collectedNodeCount": len(collected),
        "collectedNodeSetSha256": hashlib.sha256(
            json.dumps(
                collected,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "collectedNodeIds": collected,
        "passedNodeCount": len(collected),
        "nodeOutcomes": {node_id: "passed" for node_id in collected},
        "collectionErrors": [],
        "executionFailures": [],
        "missingOutcomes": [],
        "missingSelectors": [],
        "unexpectedNodes": [],
        "pytestExitCode": 0,
    }


@pytest.fixture(autouse=True)
def _trusted_execution_producer(monkeypatch: pytest.MonkeyPatch) -> None:
    def _execute(*, matrix_path: Path, root: Path) -> dict[str, object]:
        del matrix_path
        return _synthetic_execution_receipt(root)

    monkeypatch.setattr(bridge_module, "execute_red_suite", _execute)


def _green_evidence(tmp_path: Path, *, repo_root: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for name in (
        "efficiency_design",
        "adversarial_review",
        "route_manifest",
        "red_suite_coverage",
        "static",
        "simulation",
        "isolation",
    ):
        payload: dict[str, object] = {
            "schemaVersion": f"{name.upper()}_V1",
            "status": "Green",
        }
        if name == "red_suite_coverage":
            payload = _install_red_suite_source(repo_root)
        path = _write_json(
            tmp_path / f"{name}.json",
            payload,
        )
        rows.append({"kind": name, "path": str(path), "sha256": _sha256(path)})
    return rows


def _issue(
    tmp_path: Path,
    *,
    repo_root: Path | None = None,
    admission_name: str = "admission.json",
    runner_executable_path: Path | None = None,
) -> tuple[Path, Path]:
    repo = repo_root or tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    runner = repo / "scripts" / "ops" / "news-grasp-runner.ps1"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text("Write-Output 'runner'\n", encoding="utf-8")
    authority_python = tmp_path / "fixture-authority-python.exe"
    authority_python.write_bytes(b"MZ synthetic fixture authority executable\n")
    admission = repo / ".e2e-final-admissions" / admission_name
    ledger = tmp_path / "durable" / "attempts.json"
    issue_admission(
        issue_date="2026-08-01",
        canonical_product_id="News-Grasp",
        repo_root=repo,
        runner_path=runner,
        runner_arguments=["-NoPublish", "-DateStampOverride", "2026-08-01"],
        **(
            {"runner_executable_path": runner_executable_path}
            if runner_executable_path is not None
            else {}
        ),
        authority_python_executable_path=authority_python,
        evidence_bindings=_green_evidence(tmp_path / admission.stem, repo_root=repo),
        output_path=admission,
    )
    return admission, ledger


def test_validate_issued_produces_trusted_transition_receipt_and_ledger(
    tmp_path: Path,
) -> None:
    admission, _ = _issue(tmp_path)
    value = json.loads(admission.read_text(encoding="utf-8"))
    arguments_path = Path(value["expectedRunnerArgumentsPath"])
    arguments_path.parent.mkdir(parents=True, exist_ok=True)
    arguments_path.write_bytes(
        bridge_module._canonical_runner_arguments_bytes(list(value["runnerArguments"]))
    )
    policy_path = admission.parent / "e2e-attempt-policy.json"
    policy = issue_logical_attempt(bind_policy_admission(new_policy(), admission), 1)
    policy_path.write_text(json.dumps(policy, sort_keys=True) + "\n", encoding="utf-8")
    receipt_path = policy_path.with_name("e2e-transition-1.json")
    bridge_module.validate_issued_admission(
        admission_path=admission,
        runner_arguments=list(value["runnerArguments"]),
        runner_arguments_path=arguments_path,
        parent_authority_path=Path(value["expectedParentAuthorityPath"]),
        reservation_output=Path(value["expectedReservationReceiptPath"]),
        claim_output=Path(value["expectedClaimReceiptPath"]),
        claim_witness_output=Path(value["expectedClaimWitnessPath"]),
        actual_runner_executable_path=Path(value["runnerExecutablePath"]),
        actual_authority_python_executable_path=Path(value["authorityPythonExecutablePath"]),
        attempt_policy_path=policy_path,
        transition_receipt_path=receipt_path,
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["producerRouteId"] == "news-grasp-runner"
    assert receipt["outcomeStatus"] == "admission_validated"
    assert receipt["producerExecutableSha256"] == _sha256(Path(receipt["producerExecutablePath"]))
    assert policy_path.with_name("e2e-attempt-policy-ledger.sqlite3").is_file()
    assert validate_policy_ledger(policy, policy_path)["transition"]["sequence"] == 1


def test_validate_issued_reuses_preflight_receipt_across_producer_processes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未起動の事前検証receiptは、別プロセス再実行でPIDだけが変わっても再利用する。"""

    admission, _ = _issue(tmp_path)
    value = json.loads(admission.read_text(encoding="utf-8"))
    arguments_path = Path(value["expectedRunnerArgumentsPath"])
    arguments_path.parent.mkdir(parents=True, exist_ok=True)
    arguments_path.write_bytes(
        bridge_module._canonical_runner_arguments_bytes(list(value["runnerArguments"]))
    )
    policy_path = admission.parent / "e2e-attempt-policy.json"
    policy = issue_logical_attempt(bind_policy_admission(new_policy(), admission), 1)
    policy_path.write_text(json.dumps(policy, sort_keys=True) + "\n", encoding="utf-8")
    receipt_path = policy_path.with_name("e2e-transition-1.json")
    kwargs = dict(
        admission_path=admission,
        runner_arguments=list(value["runnerArguments"]),
        runner_arguments_path=arguments_path,
        parent_authority_path=Path(value["expectedParentAuthorityPath"]),
        reservation_output=Path(value["expectedReservationReceiptPath"]),
        claim_output=Path(value["expectedClaimReceiptPath"]),
        claim_witness_output=Path(value["expectedClaimWitnessPath"]),
        actual_runner_executable_path=Path(value["runnerExecutablePath"]),
        actual_authority_python_executable_path=Path(value["authorityPythonExecutablePath"]),
        attempt_policy_path=policy_path,
        transition_receipt_path=receipt_path,
    )
    monkeypatch.setattr(bridge_module.os, "getpid", lambda: 1001)
    first = bridge_module.validate_issued_admission(**kwargs)
    monkeypatch.setattr(bridge_module.os, "getpid", lambda: 1002)
    second = bridge_module.validate_issued_admission(**kwargs)
    assert second == first
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["producerProcessId"] == 1001


def test_runner_outcome_is_required_for_success_transition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission, _ = _issue(tmp_path)
    value = json.loads(admission.read_text(encoding="utf-8"))
    arguments_path = Path(value["expectedRunnerArgumentsPath"])
    arguments_path.parent.mkdir(parents=True, exist_ok=True)
    arguments_path.write_bytes(
        bridge_module._canonical_runner_arguments_bytes(list(value["runnerArguments"]))
    )
    parent_path = Path(value["expectedParentAuthorityPath"])
    _write_json(parent_path, {"state": "activated"})
    policy_path = admission.parent / "e2e-attempt-policy.json"
    policy = issue_logical_attempt(bind_policy_admission(new_policy(), admission), 1)
    policy_path.write_text(json.dumps(policy, sort_keys=True) + "\n", encoding="utf-8")
    bridge_module.validate_issued_admission(
        admission_path=admission,
        runner_arguments=list(value["runnerArguments"]),
        runner_arguments_path=arguments_path,
        parent_authority_path=parent_path,
        reservation_output=Path(value["expectedReservationReceiptPath"]),
        claim_output=Path(value["expectedClaimReceiptPath"]),
        claim_witness_output=Path(value["expectedClaimWitnessPath"]),
        actual_runner_executable_path=Path(value["runnerExecutablePath"]),
        actual_authority_python_executable_path=Path(value["authorityPythonExecutablePath"]),
        attempt_policy_path=policy_path,
        transition_receipt_path=policy_path.with_name("e2e-transition-1.json"),
    )
    bridge_module.consume_admission(
        admission_path=admission,
        ledger_path=tmp_path / "attempt-ledger.json",
        runner_arguments=list(value["runnerArguments"]),
        parent_authority_path=parent_path,
        runner_arguments_path=arguments_path,
        reservation_output=Path(value["expectedReservationReceiptPath"]),
        actual_runner_executable_path=Path(value["runnerExecutablePath"]),
        actual_authority_python_executable_path=Path(value["authorityPythonExecutablePath"]),
    )
    state_path = admission.parent / "runner-state.json"
    _write_json(
        state_path,
        {
            "status": "publish_dry_run_ok",
            "exit_code": 0,
            "e2eFinalAdmissionPath": str(admission),
            "e2eFinalRunnerArgumentsPath": str(arguments_path),
            "pid": 123,
            "process_creation_time": "fixture",
            "runner_path": str(value["runnerExecutablePath"]),
        },
    )
    test_ledger = tmp_path / "attempt-ledger.json"
    monkeypatch.setattr(bridge_module, "default_attempt_ledger_path", lambda: test_ledger)
    terminal_authority = _write_terminal_authority(
        admission=admission, arguments=arguments_path, state=state_path, policy=policy_path, ledger=test_ledger
    )
    result = bridge_module.record_runner_outcome(
        admission_path=admission,
        attempt_policy_path=policy_path,
        terminal_authority_path=terminal_authority,
    )
    assert result["outcomeStatus"] == "runner_terminal"
    updated = json.loads(policy_path.read_text(encoding="utf-8"))
    assert updated["transition"]["event"] == "success"
    assert updated["terminal"] == "product_completion"


def test_runner_outcome_rejects_self_declared_success_state(
    tmp_path: Path,
) -> None:
    admission, _ = _issue(tmp_path)
    value = json.loads(admission.read_text(encoding="utf-8"))
    arguments_path = Path(value["expectedRunnerArgumentsPath"])
    arguments_path.parent.mkdir(parents=True, exist_ok=True)
    arguments_path.write_bytes(
        bridge_module._canonical_runner_arguments_bytes(list(value["runnerArguments"]))
    )
    parent_path = Path(value["expectedParentAuthorityPath"])
    _write_json(parent_path, {"state": "activated"})
    policy_path = admission.parent / "e2e-attempt-policy.json"
    policy = issue_logical_attempt(bind_policy_admission(new_policy(), admission), 1)
    policy_path.write_text(json.dumps(policy, sort_keys=True) + "\n", encoding="utf-8")
    bridge_module.validate_issued_admission(
        admission_path=admission,
        runner_arguments=list(value["runnerArguments"]),
        runner_arguments_path=arguments_path,
        parent_authority_path=parent_path,
        reservation_output=Path(value["expectedReservationReceiptPath"]),
        claim_output=Path(value["expectedClaimReceiptPath"]),
        claim_witness_output=Path(value["expectedClaimWitnessPath"]),
        actual_runner_executable_path=Path(value["runnerExecutablePath"]),
        actual_authority_python_executable_path=Path(value["authorityPythonExecutablePath"]),
        attempt_policy_path=policy_path,
        transition_receipt_path=policy_path.with_name("e2e-transition-1.json"),
    )
    state_path = admission.parent / "runner-state.json"
    _write_json(state_path, {"status": "running", "exit_code": 0, "e2eFinalAdmissionPath": str(admission), "e2eFinalRunnerArgumentsPath": str(arguments_path)})
    with pytest.raises(E2EFinalAdmissionError, match="TERMINAL_AUTHORITY"):
        bridge_module.record_runner_outcome(
            admission_path=admission,
            attempt_policy_path=policy_path,
            terminal_authority_path=policy_path.with_name("missing-terminal-authority.json"),
        )


def test_logical_attempt_b_has_distinct_admission_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    runner = repo / "scripts" / "ops" / "news-grasp-runner.ps1"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text("Write-Output 'runner'\n", encoding="utf-8")
    monkeypatch.setattr(
        bridge_module,
        "execute_red_suite",
        lambda **_: _synthetic_execution_receipt(repo),
    )
    admission = issue_admission(
        issue_date="2026-08-01",
        canonical_product_id="News-Grasp",
        repo_root=repo,
        runner_path=runner,
        runner_arguments=["-NoPublish", "-DateStampOverride", "2026-08-01"],
        evidence_bindings=_green_evidence(tmp_path / "attempt-b", repo_root=repo),
        output_path=repo / ".e2e-final-admissions" / "attempt-b.json",
        logical_attempt=2,
    )
    assert admission["attemptKey"] == (
        "News-Grasp:2026-08-01:scheduled-equivalent-nopublish:attempt-b"
    )


def test_logical_attempt_three_is_rejected_before_red_suite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    runner = repo / "scripts" / "ops" / "news-grasp-runner.ps1"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text("Write-Output 'runner'\n", encoding="utf-8")
    monkeypatch.setattr(
        bridge_module,
        "execute_red_suite",
        lambda **_: pytest.fail("3回目でRed suiteを実行してはならない"),
    )
    with pytest.raises(E2EFinalAdmissionError, match="E2E_ATTEMPT_LIMIT"):
        issue_admission(
            issue_date="2026-08-01",
            canonical_product_id="News-Grasp",
            repo_root=repo,
            runner_path=runner,
            runner_arguments=["-NoPublish", "-DateStampOverride", "2026-08-01"],
            evidence_bindings=_green_evidence(
                tmp_path / "attempt-c", repo_root=repo
            ),
            output_path=repo / ".e2e-final-admissions" / "attempt-c.json",
            logical_attempt=3,
        )


def test_red_suite_coverage_is_mandatory_upstream_evidence(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = repo / "scripts" / "ops" / "news-grasp-runner.ps1"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text("Write-Output 'runner'\n", encoding="utf-8")
    with pytest.raises(
        E2EFinalAdmissionError,
        match="E2E_UPSTREAM_EVIDENCE_INCOMPLETE",
    ):
        issue_admission(
            issue_date="2026-08-01",
            canonical_product_id="News-Grasp",
            repo_root=repo,
            runner_path=runner,
            runner_arguments=["-NoPublish", "-DateStampOverride", "2026-08-01"],
            evidence_bindings=[
                row
                for row in _green_evidence(
                    tmp_path / "missing-red-suite", repo_root=repo
                )
                if row["kind"] != "red_suite_coverage"
            ],
            output_path=tmp_path / "missing-red-suite-admission.json",
        )


def test_caller_supplied_red_suite_execution_is_rejected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = repo / "scripts" / "ops" / "news-grasp-runner.ps1"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text("Write-Output 'runner'\n", encoding="utf-8")
    evidence = _green_evidence(tmp_path / "caller-execution", repo_root=repo)
    forged_path = _write_json(
        tmp_path / "caller-execution" / "red_suite_execution.json",
        _synthetic_execution_receipt(repo),
    )
    evidence.insert(
        4,
        {
            "kind": "red_suite_execution",
            "path": str(forged_path),
            "sha256": _sha256(forged_path),
        },
    )
    with pytest.raises(
        E2EFinalAdmissionError,
        match="E2E_RED_SUITE_EXECUTION_CALLER_FORBIDDEN",
    ):
        issue_admission(
            issue_date="2026-08-01",
            canonical_product_id="News-Grasp",
            repo_root=repo,
            runner_path=runner,
            runner_arguments=["-NoPublish", "-DateStampOverride", "2026-08-01"],
            evidence_bindings=evidence,
            output_path=tmp_path / "caller-execution-admission.json",
        )


def test_existing_output_rejects_before_red_suite_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    runner = repo / "scripts" / "ops" / "news-grasp-runner.ps1"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text("Write-Output 'runner'\n", encoding="utf-8")
    output = tmp_path / "existing-admission.json"
    output.write_text("{}\n", encoding="utf-8")

    def _must_not_execute(**_: object) -> dict[str, object]:
        raise AssertionError("出力衝突時にRED suiteを起動してはならない")

    monkeypatch.setattr(bridge_module, "execute_red_suite", _must_not_execute)
    with pytest.raises(E2EFinalAdmissionError, match="E2E_ADMISSION_ALREADY_EXISTS"):
        issue_admission(
            issue_date="2026-08-01",
            canonical_product_id="News-Grasp",
            repo_root=repo,
            runner_path=runner,
            runner_arguments=["-NoPublish", "-DateStampOverride", "2026-08-01"],
            evidence_bindings=_green_evidence(
                tmp_path / "existing-evidence", repo_root=repo
            ),
            output_path=output,
        )


def test_parallel_issue_runs_red_suite_only_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    runner = repo / "scripts" / "ops" / "news-grasp-runner.ps1"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text("Write-Output 'runner'\n", encoding="utf-8")
    evidence = _green_evidence(tmp_path / "parallel-evidence", repo_root=repo)
    output = tmp_path / "parallel-admission.json"
    started = Event()
    release = Event()
    calls: list[int] = []

    def _delayed_execute(**_: object) -> dict[str, object]:
        calls.append(1)
        started.set()
        assert release.wait(5)
        return _synthetic_execution_receipt(repo)

    monkeypatch.setattr(bridge_module, "execute_red_suite", _delayed_execute)

    def _issue_once() -> dict[str, object]:
        return issue_admission(
            issue_date="2026-08-01",
            canonical_product_id="News-Grasp",
            repo_root=repo,
            runner_path=runner,
            runner_arguments=["-NoPublish", "-DateStampOverride", "2026-08-01"],
            evidence_bindings=evidence,
            output_path=output,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_issue_once)
        assert started.wait(5)
        try:
            with pytest.raises(
                E2EFinalAdmissionError,
                match="E2E_ADMISSION_ISSUE_BUSY",
            ):
                _issue_once()
        finally:
            release.set()
        assert first.result()["state"] == "issued"
    assert calls == [1]


def test_red_suite_execution_receipt_rejects_missing_pair_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    runner = repo / "scripts" / "ops" / "news-grasp-runner.ps1"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text("Write-Output 'runner'\n", encoding="utf-8")
    evidence = _green_evidence(tmp_path / "missing-pair", repo_root=repo)
    payload = _synthetic_execution_receipt(repo)
    missing = payload["pairNodeIds"].pop()
    payload["collectedNodeIds"].remove(missing)
    payload["collectedNodeCount"] -= 1
    payload["passedNodeCount"] -= 1
    payload["nodeOutcomes"].pop(missing)
    payload["collectedNodeSetSha256"] = hashlib.sha256(
        json.dumps(
            payload["collectedNodeIds"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    monkeypatch.setattr(
        bridge_module,
        "execute_red_suite",
        lambda **_: payload,
    )
    with pytest.raises(
        E2EFinalAdmissionError,
        match="E2E_RED_SUITE_EXECUTION_INVALID",
    ):
        issue_admission(
            issue_date="2026-08-01",
            canonical_product_id="News-Grasp",
            repo_root=repo,
            runner_path=runner,
            runner_arguments=["-NoPublish", "-DateStampOverride", "2026-08-01"],
            evidence_bindings=evidence,
            output_path=tmp_path / "missing-pair-admission.json",
        )


def test_red_suite_execution_receipt_rejects_invalid_outcome_map_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    runner = repo / "scripts" / "ops" / "news-grasp-runner.ps1"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text("Write-Output 'runner'\n", encoding="utf-8")
    evidence = _green_evidence(tmp_path / "invalid-outcomes", repo_root=repo)
    payload = _synthetic_execution_receipt(repo)
    payload["nodeOutcomes"] = 1
    monkeypatch.setattr(
        bridge_module,
        "execute_red_suite",
        lambda **_: payload,
    )
    with pytest.raises(
        E2EFinalAdmissionError,
        match="E2E_RED_SUITE_EXECUTION_INVALID",
    ):
        issue_admission(
            issue_date="2026-08-01",
            canonical_product_id="News-Grasp",
            repo_root=repo,
            runner_path=runner,
            runner_arguments=["-NoPublish", "-DateStampOverride", "2026-08-01"],
            evidence_bindings=evidence,
            output_path=tmp_path / "invalid-outcomes-admission.json",
        )


def test_status_only_red_suite_receipt_cannot_authorize_e2e(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = repo / "scripts" / "ops" / "news-grasp-runner.ps1"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text("Write-Output 'runner'\n", encoding="utf-8")
    evidence = _green_evidence(tmp_path / "status-only", repo_root=repo)
    row = next(item for item in evidence if item["kind"] == "red_suite_coverage")
    path = Path(row["path"])
    _write_json(path, {"schemaVersion": "FAKE_V1", "status": "Green"})
    row["sha256"] = _sha256(path)
    with pytest.raises(
        E2EFinalAdmissionError,
        match="E2E_RED_SUITE_COVERAGE_INVALID",
    ):
        issue_admission(
            issue_date="2026-08-01",
            canonical_product_id="News-Grasp",
            repo_root=repo,
            runner_path=runner,
            runner_arguments=["-NoPublish", "-DateStampOverride", "2026-08-01"],
            evidence_bindings=evidence,
            output_path=tmp_path / "status-only-admission.json",
        )


def test_well_shaped_forged_red_suite_receipt_cannot_authorize_e2e(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    runner = repo / "scripts" / "ops" / "news-grasp-runner.ps1"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text("Write-Output 'runner'\n", encoding="utf-8")
    evidence = _green_evidence(tmp_path / "forged", repo_root=repo)
    row = next(item for item in evidence if item["kind"] == "red_suite_coverage")
    path = Path(row["path"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["coverageSha256"] = "b" * 64
    _write_json(path, payload)
    row["sha256"] = _sha256(path)
    with pytest.raises(
        E2EFinalAdmissionError,
        match="E2E_RED_SUITE_COVERAGE_SOURCE_MISMATCH",
    ):
        issue_admission(
            issue_date="2026-08-01",
            canonical_product_id="News-Grasp",
            repo_root=repo,
            runner_path=runner,
            runner_arguments=["-NoPublish", "-DateStampOverride", "2026-08-01"],
            evidence_bindings=evidence,
            output_path=tmp_path / "forged-admission.json",
        )


def test_red_suite_fixture_source_drift_invalidates_coverage_receipt(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    runner = repo / "scripts" / "ops" / "news-grasp-runner.ps1"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text("Write-Output 'runner'\n", encoding="utf-8")
    evidence = _green_evidence(tmp_path / "fixture-drift", repo_root=repo)
    fixture = repo / "tests" / "red_suite_fixture.py"
    fixture.write_text(
        fixture.read_text(encoding="utf-8") + "\n# source drift\n",
        encoding="utf-8",
    )
    with pytest.raises(
        E2EFinalAdmissionError,
        match="E2E_RED_SUITE_COVERAGE_SOURCE_MISMATCH",
    ):
        issue_admission(
            issue_date="2026-08-01",
            canonical_product_id="News-Grasp",
            repo_root=repo,
            runner_path=runner,
            runner_arguments=["-NoPublish", "-DateStampOverride", "2026-08-01"],
            evidence_bindings=evidence,
            output_path=tmp_path / "fixture-drift-admission.json",
        )


def test_evidence_hash_and_json_are_derived_from_one_byte_read() -> None:
    source = BRIDGE.read_text(encoding="utf-8-sig")
    normalize_start = source.index("def _normalize_evidence")
    normalize_end = source.index("\ndef ", normalize_start + 5)
    normalize_source = source[normalize_start:normalize_end]
    assert "_read_bound_json" in normalize_source
    assert "_file_sha256" not in normalize_source
    assert "_read_json" not in normalize_source


def test_file_existence_is_not_e2e_admission(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    evidence = _green_evidence(tmp_path, repo_root=repo)
    evidence[0]["path"] = str(
        _write_json(
            tmp_path / "false-green.json",
            {"schemaVersion": "EFFICIENCY_DESIGN_V1", "status": "Red"},
        )
    )
    evidence[0]["sha256"] = _sha256(Path(evidence[0]["path"]))
    repo.mkdir(exist_ok=True)
    runner = _write_json(repo / "runner.ps1", {"runner": True})

    with pytest.raises(
        E2EFinalAdmissionError,
        match="E2E_UPSTREAM_NOT_GREEN",
    ):
        issue_admission(
            issue_date="2026-08-01",
            canonical_product_id="News-Grasp",
            repo_root=repo,
            runner_path=runner,
            runner_arguments=["-NoPublish"],
            evidence_bindings=evidence,
            output_path=tmp_path / "admission.json",
        )


def test_admission_requires_isolation_evidence(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = repo / "runner.ps1"
    runner.write_text("runner\n", encoding="utf-8")
    evidence = _green_evidence(tmp_path / "evidence", repo_root=repo)
    evidence = [row for row in evidence if row["kind"] != "isolation"]
    with pytest.raises(E2EFinalAdmissionError, match="E2E_UPSTREAM_EVIDENCE_INCOMPLETE"):
        issue_admission(
            issue_date="2026-08-01",
            canonical_product_id="News-Grasp",
            repo_root=repo,
            runner_path=runner,
            runner_arguments=["-NoPublish"],
            evidence_bindings=evidence,
            output_path=tmp_path / "admission.json",
        )


def test_consumer_rejects_isolation_receipt_drift(tmp_path: Path) -> None:
    admission, ledger = _issue(tmp_path)
    value = json.loads(admission.read_text(encoding="utf-8"))
    isolation = next(
        row for row in value["evidenceBindings"] if row["kind"] == "isolation"
    )
    Path(isolation["path"]).write_text('{"status":"Red"}\n', encoding="utf-8")
    with pytest.raises(E2EFinalAdmissionError, match="E2E_UPSTREAM_EVIDENCE_DRIFT"):
        consume_admission(admission_path=admission, ledger_path=ledger)


def test_valid_admission_is_consumed_once(tmp_path: Path) -> None:
    admission, ledger = _issue(tmp_path)
    issued = json.loads(admission.read_text(encoding="utf-8"))
    execution_binding = next(
        row
        for row in issued["evidenceBindings"]
        if row["kind"] == "red_suite_execution"
    )
    assert Path(execution_binding["path"]).is_file()
    assert Path(execution_binding["path"]).name == (
        f"{admission.stem}.red-suite-execution.json"
    )
    result = consume_admission(admission_path=admission, ledger_path=ledger)
    assert result["state"] == "runner_reserved"
    ledger_value = json.loads(ledger.read_text(encoding="utf-8"))
    assert list(ledger_value["attempts"]) == [
        "News-Grasp:2026-08-01:scheduled-equivalent-nopublish"
    ]


def test_causal_replacement_ledger_preserves_original_row_and_rejects_tamper() -> None:
    original_row = {
        "state": "runner_reserved",
        "admissionId": "a" * 64,
        "reservationReceiptSha256": "b" * 64,
    }
    ledger = {
        "schemaVersion": bridge_module.LEDGER_SCHEMA,
        "attempts": {"News-Grasp:2026-08-10:scheduled-equivalent-nopublish": {
            "state": "runner_reserved",
            "admissionId": "c" * 64,
        }},
        "replacements": {
            "News-Grasp:2026-08-10:scheduled-equivalent-nopublish": {
                "originalAdmissionId": original_row["admissionId"],
                "originalReservationReceiptSha256": original_row["reservationReceiptSha256"],
                "originalRow": original_row,
                "replacementAdmissionId": "c" * 64,
                "proofSha256": "d" * 64,
            }
        },
    }
    bridge_module._validate_attempt_ledger(ledger)
    tampered = json.loads(json.dumps(ledger))
    tampered["replacements"][next(iter(tampered["replacements"]))]["originalRow"]["admissionId"] = "e" * 64
    with pytest.raises(bridge_module.E2EFinalAdmissionError, match="E2E_CAUSAL_REPLACEMENT_LINEAGE_INVALID"):
        bridge_module._validate_attempt_ledger(tampered)


def test_attempt_ledger_accepts_only_explicit_prestart_generation_rebind_marker() -> None:
    """開始前のgeneration更新だけは、履歴へ明示された専用markerを持てる。"""
    attempt_key = "News-Grasp:2026-08-10:scheduled-equivalent-nopublish"
    original_row = {
        "state": "runner_reserved",
        "admissionId": "a" * 64,
        "reservationReceiptSha256": "b" * 64,
    }
    replacement = {
        "originalAdmissionId": original_row["admissionId"],
        "originalReservationReceiptSha256": original_row["reservationReceiptSha256"],
        "originalRow": original_row,
        "replacementAdmissionId": "c" * 64,
        "proofSha256": "d" * 64,
        "prestartGenerationRebind": True,
    }
    ledger = {
        "schemaVersion": bridge_module.LEDGER_SCHEMA,
        "attempts": {attempt_key: {**original_row}},
        "replacements": {attempt_key: replacement},
        "replacementHistory": [{"attemptKey": attempt_key, **replacement}],
    }
    bridge_module._validate_attempt_ledger(ledger)


def test_prestart_generation_rebind_requires_fresh_manifest_and_three_runner_hashes(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    source_runner = repo / "scripts" / "ops" / "news-grasp-runner.ps1"
    runtime_runner = tmp_path / "runtime" / "scripts" / "ops" / "news-grasp-runner.ps1"
    installed_runner = tmp_path / "bin" / "news-grasp-runner.ps1"
    for path in (source_runner, runtime_runner, installed_runner):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"current-generation-runner\n")
    runner_sha = _sha256(source_runner)
    manifest_path = tmp_path / "runtime" / "generations" / "generation.json"
    manifest = {
        "schemaVersion": "PRODUCTION_GENERATION_MANIFEST_V2",
        "generationId": "generation-current",
        "source": {"commit": "commit-current", "remoteHead": "commit-current"},
        "runtime": {
            "commit": "commit-current",
            "root": str(tmp_path / "runtime"),
            "trackedFiles": {
                "scripts/ops/news-grasp-runner.ps1": f"100644:blob:{runner_sha}"
            },
        },
    }
    _write_json(manifest_path, manifest)
    existing = {
        "state": "runner_reserved",
        "admissionId": "a" * 64,
        "admissionPath": str(repo / "old-admission.json"),
        "admissionSha256": "b" * 64,
        "runnerSha256": "c" * 64,
        "reservationReceiptSha256": "d" * 64,
    }
    source = {
        "admissionId": "e" * 64,
        "runnerPath": str(source_runner),
        "runnerSha256": runner_sha,
    }
    proof = {
        "schemaVersion": bridge_module.PRESTART_REBIND_PROOF_SCHEMA,
        "canonicalAttemptKey": "News-Grasp:2026-08-10:scheduled-equivalent-nopublish",
        "prestartGenerationRebind": True,
        "predecessor": {"admissionId": "a" * 64, "runnerSha256": "c" * 64},
        "successor": {
            "admissionId": "e" * 64,
            "runnerSha256": runner_sha,
            "runnerPath": str(source_runner),
        },
        "generation": {
            "manifestPath": str(manifest_path),
            "manifestSha256": _sha256(manifest_path),
            "generationId": "generation-current",
            "sourceCommit": "commit-current",
            "sourceRunnerSha256": runner_sha,
            "runtimeRunnerSha256": runner_sha,
            "installedRunnerPath": str(installed_runner),
            "installedRunnerSha256": runner_sha,
        },
        "originalEvidence": {
            "admissionPath": str(repo / "old-admission.json"),
            "admissionSha256": "b" * 64,
        },
    }
    bridge_module._validate_prestart_generation_rebind_proof(
        proof,
        existing=existing,
        source=source,
        attempt_key=proof["canonicalAttemptKey"],
    )
    installed_runner.write_bytes(b"stale-installed-runner\n")
    with pytest.raises(
        E2EFinalAdmissionError,
        match="E2E_PRESTART_GENERATION_REBIND_INVALID",
    ):
        bridge_module._validate_prestart_generation_rebind_proof(
            proof,
            existing=existing,
            source=source,
            attempt_key=proof["canonicalAttemptKey"],
        )


def test_consumer_rejects_production_dependency_drift(tmp_path: Path) -> None:
    admission, ledger = _issue(tmp_path)
    dependency = tmp_path / "repo" / "tools" / "production_validator.py"
    dependency.write_text("VALUE = 'drift'\n", encoding="utf-8")
    with pytest.raises(
        E2EFinalAdmissionError,
        match="E2E_RED_SUITE_EXECUTION_SOURCE_MISMATCH",
    ):
        consume_admission(admission_path=admission, ledger_path=ledger)


def test_admission_is_consumed_once_across_worktree_and_receipt_paths(
    tmp_path: Path,
) -> None:
    first, ledger = _issue(
        tmp_path,
        repo_root=tmp_path / "worktree-r1",
        admission_name="first.json",
    )
    consume_admission(admission_path=first, ledger_path=ledger)

    second, _ = _issue(
        tmp_path,
        repo_root=tmp_path / "worktree-r2",
        admission_name="second.json",
    )
    with pytest.raises(
        E2EFinalAdmissionError,
        match="E2E_WAL_CROSS_LINEAGE",
    ):
        consume_admission(admission_path=second, ledger_path=ledger)


def test_admission_rejects_evidence_and_runner_drift(tmp_path: Path) -> None:
    admission, ledger = _issue(tmp_path)
    value = json.loads(admission.read_text(encoding="utf-8"))
    evidence_path = Path(value["evidenceBindings"][0]["path"])
    evidence_path.write_text('{"status":"Red"}\n', encoding="utf-8")

    with pytest.raises(
        E2EFinalAdmissionError,
        match="E2E_UPSTREAM_EVIDENCE_DRIFT",
    ):
        consume_admission(admission_path=admission, ledger_path=ledger)

    admission2, ledger2 = _issue(tmp_path / "runner-drift")
    value2 = json.loads(admission2.read_text(encoding="utf-8"))
    Path(value2["runnerPath"]).write_text("changed\n", encoding="utf-8")
    with pytest.raises(
        E2EFinalAdmissionError,
        match="E2E_RUNNER_DRIFT",
    ):
        consume_admission(admission_path=admission2, ledger_path=ledger2)


def test_admission_rejects_evidence_drift(tmp_path: Path) -> None:
    admission, ledger = _issue(tmp_path)
    value = json.loads(admission.read_text(encoding="utf-8"))
    Path(value["evidenceBindings"][0]["path"]).write_text(
        '{"status":"Red"}\n', encoding="utf-8"
    )
    with pytest.raises(E2EFinalAdmissionError, match="E2E_UPSTREAM_EVIDENCE_DRIFT"):
        consume_admission(admission_path=admission, ledger_path=ledger)


def test_admission_rejects_runner_drift(tmp_path: Path) -> None:
    admission, ledger = _issue(tmp_path)
    value = json.loads(admission.read_text(encoding="utf-8"))
    Path(value["runnerPath"]).write_text("changed\n", encoding="utf-8")
    with pytest.raises(E2EFinalAdmissionError, match="E2E_RUNNER_DRIFT"):
        consume_admission(admission_path=admission, ledger_path=ledger)


def test_admission_rejects_resume_and_publish_arguments(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = repo / "runner.ps1"
    runner.write_text("runner\n", encoding="utf-8")
    evidence = _green_evidence(tmp_path / "evidence", repo_root=repo)
    for arguments in (
        ["-NoPublish", "-ResumeFromStage", "deepdive"],
        ["-DateStampOverride", "2026-08-01"],
    ):
        with pytest.raises(E2EFinalAdmissionError, match="E2E_COMMAND_FORBIDDEN"):
            issue_admission(
                issue_date="2026-08-01",
                canonical_product_id="News-Grasp",
                repo_root=repo,
                runner_path=runner,
                runner_arguments=arguments,
                evidence_bindings=evidence,
                output_path=tmp_path / f"{len(arguments)}.json",
            )


def test_admission_rejects_identity_tamper(tmp_path: Path) -> None:
    admission, ledger = _issue(tmp_path)
    value = json.loads(admission.read_text(encoding="utf-8"))
    value["issueDate"] = "2026-08-02"
    admission.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(E2EFinalAdmissionError, match="E2E_ADMISSION_IDENTITY_DRIFT"):
        consume_admission(admission_path=admission, ledger_path=ledger)


def test_consumed_admission_is_typed_replay(tmp_path: Path) -> None:
    admission, ledger = _issue(tmp_path)
    consume_admission(admission_path=admission, ledger_path=ledger)
    with pytest.raises(
        E2EFinalAdmissionError,
        match="E2E_FINAL_ATTEMPT_ALREADY_CONSUMED",
    ):
        consume_admission(admission_path=admission, ledger_path=ledger)


def test_consumer_rejects_actual_runner_argument_drift(tmp_path: Path) -> None:
    admission, ledger = _issue(tmp_path)
    with pytest.raises(E2EFinalAdmissionError, match="E2E_COMMAND_DRIFT"):
        value = json.loads(admission.read_text(encoding="utf-8"))
        arguments_path = Path(value["expectedRunnerArgumentsPath"])
        arguments_path.write_bytes(
            bridge_module._canonical_runner_arguments_bytes(
                list(value["runnerArguments"])
            )
        )
        parent_path = Path(value["expectedParentAuthorityPath"])
        _write_json(parent_path, {"state": "activated", "fixture": "canonical"})
        _consume_admission(
            admission_path=admission,
            ledger_path=ledger,
            runner_arguments=["-NoPublish", "-DateStampOverride", "2026-08-02"],
            parent_authority_path=parent_path,
            runner_arguments_path=arguments_path,
            actual_runner_executable_path=Path(value["runnerExecutablePath"]),
            actual_authority_python_executable_path=Path(
                value["authorityPythonExecutablePath"]
            ),
        )


def test_parallel_consume_has_exactly_one_winner(tmp_path: Path) -> None:
    admission, ledger = _issue(tmp_path)

    def consume() -> str:
        try:
            consume_admission(admission_path=admission, ledger_path=ledger)
            return "runner_reserved"
        except E2EFinalAdmissionError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: consume(), range(2)))
    assert results.count("runner_reserved") == 1
    assert len(results) == 2


def _claim_reserved(
    admission: Path,
    ledger: Path,
    *,
    nonce: str,
    pid: int | None = None,
) -> dict[str, object]:
    value = json.loads(admission.read_text(encoding="utf-8"))
    return bridge_module.claim_runner(
        admission_path=admission,
        ledger_path=ledger,
        runner_arguments=list(value["runnerArguments"]),
        parent_authority_path=Path(value["expectedParentAuthorityPath"]),
        runner_arguments_path=Path(value["expectedRunnerArgumentsPath"]),
        reservation_receipt=Path(value["expectedReservationReceiptPath"]),
        claim_output=Path(value["expectedClaimReceiptPath"]),
        actual_runner_executable_path=Path(value["runnerExecutablePath"]),
        actual_authority_python_executable_path=Path(
            value["authorityPythonExecutablePath"]
        ),
        current_runner_pid=pid or os.getpid(),
        claim_nonce=nonce,
    )


def test_reserved_admission_claims_once_and_seals_runner_identity(
    tmp_path: Path,
) -> None:
    admission, ledger = _issue(tmp_path)
    reserved = consume_admission(admission_path=admission, ledger_path=ledger)
    assert reserved["state"] == "runner_reserved"
    issued_before_claim = admission.read_bytes()
    claimed = _claim_reserved(admission, ledger, nonce="a" * 64)
    assert claimed["state"] == "runner_claimed"
    assert claimed["claimNonce"] == "a" * 64
    assert admission.read_bytes() == issued_before_claim
    ledger_value = json.loads(ledger.read_text(encoding="utf-8"))
    row = ledger_value["attempts"][claimed["attemptKey"]]
    assert row["state"] == "runner_claimed"
    with pytest.raises(E2EFinalAdmissionError, match="E2E_RUNNER_CLAIM_REPLAY"):
        _claim_reserved(admission, ledger, nonce="b" * 64)


def test_claim_failure_is_durable_and_blocks_same_attempt_reentry(
    tmp_path: Path,
) -> None:
    process_image = Path(
        bridge_module._query_process_identity(os.getpid())["imagePath"]
    )
    admission, ledger = _issue(tmp_path, runner_executable_path=process_image)
    reserved = consume_admission(admission_path=admission, ledger_path=ledger)
    value = json.loads(admission.read_text(encoding="utf-8"))

    failure = bridge_module.record_claim_failure(
        admission_path=admission,
        ledger_path=ledger,
        reservation_receipt=Path(value["expectedReservationReceiptPath"]),
        failure_code="E2E_PARENT_AUTHORITY_INVALID",
        failure_fingerprint="f" * 64,
        runner_executable_path=process_image,
        authority_python_executable_path=Path(value["authorityPythonExecutablePath"]),
        current_runner_pid=os.getpid(),
    )

    assert reserved["state"] == "runner_reserved"
    assert failure["state"] == "claim_failure_recorded"
    row = json.loads(ledger.read_text(encoding="utf-8"))["attempts"][
        value["attemptKey"]
    ]
    assert row["state"] == "runner_reserved"
    assert row["claimFailure"]["failureFingerprint"] == "f" * 64
    status = bridge_module.claim_failure_status(
        admission_path=admission,
        ledger_path=ledger,
        reservation_receipt=Path(value["expectedReservationReceiptPath"]),
    )
    assert status["state"] == "claim_failure_recorded"
    with pytest.raises(
        E2EFinalAdmissionError,
        match="E2E_FINAL_ATTEMPT_CLAIM_TERMINAL",
    ):
        _claim_reserved(admission, ledger, nonce="a" * 64)

    # 同じ原因は再記録せず、既存のimmutable markerを返す。
    replay = bridge_module.record_claim_failure(
        admission_path=admission,
        ledger_path=ledger,
        reservation_receipt=Path(value["expectedReservationReceiptPath"]),
        failure_code="E2E_PARENT_AUTHORITY_INVALID",
        failure_fingerprint="f" * 64,
        runner_executable_path=process_image,
        authority_python_executable_path=Path(value["authorityPythonExecutablePath"]),
        current_runner_pid=os.getpid(),
    )
    assert replay["receiptSha256"] == failure["receiptSha256"]


def test_claim_failure_rejects_unrelated_pid_even_when_image_matches(
    tmp_path: Path,
) -> None:
    process_image = Path(
        bridge_module._query_process_identity(os.getpid())["imagePath"]
    )
    admission, ledger = _issue(tmp_path, runner_executable_path=process_image)
    consume_admission(admission_path=admission, ledger_path=ledger)
    value = json.loads(admission.read_text(encoding="utf-8"))
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        with pytest.raises(
            E2EFinalAdmissionError, match="E2E_CLAIM_FAILURE_CALLER_INVALID"
        ):
            bridge_module.record_claim_failure(
                admission_path=admission,
                ledger_path=ledger,
                reservation_receipt=Path(value["expectedReservationReceiptPath"]),
                failure_code="E2E_PARENT_AUTHORITY_INVALID",
                failure_fingerprint="c" * 64,
                runner_executable_path=process_image,
                authority_python_executable_path=Path(sys.executable),
                current_runner_pid=unrelated.pid,
            )
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)


def test_claim_failure_rejects_executable_binding_drift(
    tmp_path: Path,
) -> None:
    admission, ledger = _issue(tmp_path)
    consume_admission(admission_path=admission, ledger_path=ledger)
    value = json.loads(admission.read_text(encoding="utf-8"))
    process_image = Path(
        bridge_module._query_process_identity(os.getpid())["imagePath"]
    )
    with pytest.raises(
        E2EFinalAdmissionError, match="E2E_CLAIM_FAILURE_EXECUTABLE_BINDING_INVALID"
    ):
        bridge_module.record_claim_failure(
            admission_path=admission,
            ledger_path=ledger,
            reservation_receipt=Path(value["expectedReservationReceiptPath"]),
            failure_code="E2E_PARENT_AUTHORITY_INVALID",
            failure_fingerprint="b" * 64,
            runner_executable_path=process_image,
            authority_python_executable_path=Path(sys.executable),
            current_runner_pid=os.getpid(),
        )


def test_claim_failure_rejects_authority_python_binding_drift(
    tmp_path: Path,
) -> None:
    process_image = Path(
        bridge_module._query_process_identity(os.getpid())["imagePath"]
    )
    admission, ledger = _issue(tmp_path, runner_executable_path=process_image)
    consume_admission(admission_path=admission, ledger_path=ledger)
    value = json.loads(admission.read_text(encoding="utf-8"))
    alternate_python = Path(sys.executable).with_name("pythonw.exe")
    if not alternate_python.exists():
        pytest.skip("alternate authority python executable unavailable")
    with pytest.raises(
        E2EFinalAdmissionError, match="E2E_CLAIM_FAILURE_EXECUTABLE_BINDING_INVALID"
    ):
        bridge_module.record_claim_failure(
            admission_path=admission,
            ledger_path=ledger,
            reservation_receipt=Path(value["expectedReservationReceiptPath"]),
            failure_code="E2E_PARENT_AUTHORITY_INVALID",
            failure_fingerprint="a" * 64,
            runner_executable_path=process_image,
            authority_python_executable_path=alternate_python,
            current_runner_pid=os.getpid(),
        )


@pytest.mark.parametrize("crash_after", [1, 2])
def test_claim_failure_wal_recovers_after_each_replace_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_after: int,
) -> None:
    process_image = Path(
        bridge_module._query_process_identity(os.getpid())["imagePath"]
    )
    admission, ledger = _issue(tmp_path, runner_executable_path=process_image)
    consume_admission(admission_path=admission, ledger_path=ledger)
    value = json.loads(admission.read_text(encoding="utf-8"))
    original_replace = bridge_module._replace_json
    calls = 0

    def crash(path: Path, replacement: dict[str, object]) -> None:
        nonlocal calls
        calls += 1
        original_replace(path, replacement)
        if calls == crash_after:
            raise RuntimeError("simulated claim failure crash")

    monkeypatch.setattr(bridge_module, "_replace_json", crash)
    with pytest.raises(RuntimeError, match="simulated claim failure crash"):
        bridge_module.record_claim_failure(
            admission_path=admission,
            ledger_path=ledger,
            reservation_receipt=Path(value["expectedReservationReceiptPath"]),
            failure_code="E2E_PARENT_AUTHORITY_INVALID",
            failure_fingerprint="e" * 64,
            runner_executable_path=process_image,
            authority_python_executable_path=Path(value["authorityPythonExecutablePath"]),
            current_runner_pid=os.getpid(),
        )
    monkeypatch.setattr(bridge_module, "_replace_json", original_replace)
    recovered = bridge_module.record_claim_failure(
        admission_path=admission,
        ledger_path=ledger,
        reservation_receipt=Path(value["expectedReservationReceiptPath"]),
        failure_code="E2E_PARENT_AUTHORITY_INVALID",
        failure_fingerprint="e" * 64,
        runner_executable_path=process_image,
        authority_python_executable_path=Path(value["authorityPythonExecutablePath"]),
        current_runner_pid=os.getpid(),
    )
    assert recovered["state"] == "claim_failure_recorded"
    assert not list(ledger.parent.glob(f".{ledger.name}.*.claim_failure.wal.json"))


def test_concurrent_claims_have_exactly_one_winner(tmp_path: Path) -> None:
    admission, ledger = _issue(tmp_path)
    consume_admission(admission_path=admission, ledger_path=ledger)

    def claim(nonce: str) -> str:
        try:
            _claim_reserved(admission, ledger, nonce=nonce)
            return "claimed"
        except E2EFinalAdmissionError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ("c" * 64, "d" * 64)))
    assert results.count("claimed") == 1
    assert len(results) == 2


def test_stale_lock_file_does_not_block_handle_lock_recovery(tmp_path: Path) -> None:
    admission, ledger = _issue(tmp_path)
    value = json.loads(admission.read_text(encoding="utf-8"))
    lock_root = (
        bridge_module._managed_authority_root()
        / "news-grasp-e2e-final-admission-locks"
    )
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_identity = {
        "ledgerPath": bridge_module._path_key(ledger.resolve()),
        "admissionPath": bridge_module._path_key(admission.resolve()),
        "attemptKey": value["attemptKey"],
    }
    lock_path = lock_root / f"{bridge_module._canonical_sha256(lock_identity)}.lock"
    lock_path.write_bytes(b"stale")
    result = consume_admission(admission_path=admission, ledger_path=ledger)
    assert result["state"] == "runner_reserved"


@pytest.mark.parametrize("crash_after", [1, 2])
def test_wal_recovers_after_each_replace_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_after: int,
) -> None:
    admission, ledger = _issue(tmp_path)
    original_replace = bridge_module._replace_json
    calls = 0

    def crash(path: Path, value: dict[str, object]) -> None:
        nonlocal calls
        calls += 1
        original_replace(path, value)
        if calls == crash_after:
            raise RuntimeError("simulated crash")

    monkeypatch.setattr(bridge_module, "_replace_json", crash)
    with pytest.raises(RuntimeError, match="simulated crash"):
        consume_admission(admission_path=admission, ledger_path=ledger)
    monkeypatch.setattr(bridge_module, "_replace_json", original_replace)
    recovered = consume_admission(admission_path=admission, ledger_path=ledger)
    assert recovered["state"] == "runner_reserved"
    assert not list(ledger.parent.glob(f".{ledger.name}.*.reserve.wal.json"))


@pytest.mark.parametrize("crash_after", [1, 2])
def test_claim_wal_recovers_after_each_replace_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_after: int,
) -> None:
    admission, ledger = _issue(tmp_path)
    consume_admission(admission_path=admission, ledger_path=ledger)
    issued_hash = _sha256(admission)
    original_replace = bridge_module._replace_json
    calls = 0

    def crash(path: Path, value: dict[str, object]) -> None:
        nonlocal calls
        calls += 1
        original_replace(path, value)
        if calls == crash_after:
            raise RuntimeError("simulated claim crash")

    monkeypatch.setattr(bridge_module, "_replace_json", crash)
    with pytest.raises(RuntimeError, match="simulated claim crash"):
        _claim_reserved(admission, ledger, nonce="e" * 64)
    monkeypatch.setattr(bridge_module, "_replace_json", original_replace)
    recovered = _claim_reserved(admission, ledger, nonce="e" * 64)
    assert recovered["state"] == "runner_claimed"
    assert _sha256(admission) == issued_hash
    value = json.loads(admission.read_text(encoding="utf-8"))
    assert Path(value["expectedClaimReceiptPath"]).is_file()
    assert not list(ledger.parent.glob(f".{ledger.name}.*.claim.wal.json"))


def test_divergent_ledger_row_is_rejected_before_forward_recovery(
    tmp_path: Path,
) -> None:
    admission, ledger = _issue(tmp_path)
    value = json.loads(admission.read_text(encoding="utf-8"))
    _write_json(
        ledger,
        {
            "schemaVersion": "NEWS_GRASP_E2E_FINAL_ATTEMPT_LEDGER_V1",
            "attempts": {
                value["attemptKey"]: {
                    "admissionId": "f" * 64,
                    "state": "runner_claimed",
                }
            },
        },
    )
    with pytest.raises(E2EFinalAdmissionError, match="E2E_WAL_CROSS_LINEAGE"):
        consume_admission(admission_path=admission, ledger_path=ledger)


def test_parent_remains_canonically_valid_after_bridge_reservation(
    tmp_path: Path,
) -> None:
    admission, ledger = _issue(tmp_path)
    value = json.loads(admission.read_text(encoding="utf-8"))
    issued_hash = _sha256(admission)
    reserved = consume_admission(admission_path=admission, ledger_path=ledger)
    parent = Path(value["expectedParentAuthorityPath"])
    arguments = Path(value["expectedRunnerArgumentsPath"])
    assert reserved["state"] == "runner_reserved"
    assert _sha256(admission) == issued_hash
    bridge_module.validate_issued_admission(
        admission_path=admission,
        runner_arguments=list(value["runnerArguments"]),
        expected_parent_authority_path=parent,
        runner_arguments_path=arguments,
        reservation_output=Path(value["expectedReservationReceiptPath"]),
        claim_output=Path(value["expectedClaimReceiptPath"]),
        claim_witness_output=Path(value["expectedClaimWitnessPath"]),
        actual_runner_executable_path=Path(value["runnerExecutablePath"]),
        actual_authority_python_executable_path=Path(
            value["authorityPythonExecutablePath"]
        ),
    )
    reservation_path = Path(value["expectedReservationReceiptPath"])
    reservation = bridge_module._read_json(
        reservation_path, "E2E_RESERVATION_RECEIPT_INVALID"
    )
    bridge_module._validate_reservation_receipt(reservation)
    assert reservation["parentAuthorityPath"] == str(parent.resolve())
    assert reservation_path.is_file()
    assert parent.is_file()


def test_existing_canonical_row_from_other_admission_is_cross_lineage(
    tmp_path: Path,
) -> None:
    first, ledger = _issue(
        tmp_path,
        repo_root=tmp_path / "first-worktree",
        admission_name="first.json",
    )
    consume_admission(admission_path=first, ledger_path=ledger)
    second, _ = _issue(
        tmp_path,
        repo_root=tmp_path / "second-worktree",
        admission_name="second.json",
    )
    with pytest.raises(E2EFinalAdmissionError, match="E2E_WAL_CROSS_LINEAGE"):
        consume_admission(admission_path=second, ledger_path=ledger)


def test_expected_parent_path_composition_is_red_before_authorize_fix(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    runner = repo / "scripts" / "ops" / "news-grasp-runner.ps1"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text("Write-Output 'runner'\n", encoding="utf-8")
    arguments_path = repo / "receipt.json.runner-arguments.json"
    arguments = ["-NoPublish", "-DateStampOverride", "2026-08-01"]
    expected_parent = repo / "receipt.json.high-cost-parent-authority.json"
    admission = repo / "receipt.json"
    value = _issue_admission(
        issue_date="2026-08-01",
        canonical_product_id="News-Grasp",
        repo_root=repo,
        runner_path=runner,
        runner_arguments=arguments,
        expected_parent_authority_path=expected_parent,
        runner_arguments_path=arguments_path,
        runner_executable_path=runner,
        authority_python_executable_path=Path(sys.executable),
        evidence_bindings=_green_evidence(tmp_path / "red", repo_root=repo),
        output_path=admission,
    )
    assert value["state"] == "issued"
    assert value["expectedParentAuthorityPath"] == str(expected_parent.resolve())
    assert not expected_parent.exists()
    assert not arguments_path.exists()
    assert not Path(value["expectedReservationReceiptPath"]).exists()
    assert not Path(value["expectedClaimReceiptPath"]).exists()
    arguments_path.write_bytes(bridge_module._canonical_runner_arguments_bytes(arguments))


def test_issue_rejects_authority_python_when_workspace_anchor_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """workspace正本を発見できないbridge直呼出しはauthority Pythonを受理しない。"""
    repo = tmp_path / "repo"
    runner = repo / "scripts" / "ops" / "news-grasp-runner.ps1"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text("Write-Output 'runner'\n", encoding="utf-8")
    monkeypatch.setattr(
        bridge_module,
        "_require_trusted_workspace_root",
        lambda _repo: (_ for _ in ()).throw(
            E2EFinalAdmissionError("E2E_AUTHORITY_PYTHON_INVALID")
        ),
    )
    with pytest.raises(E2EFinalAdmissionError, match="E2E_AUTHORITY_PYTHON_INVALID"):
        _issue_admission(
            issue_date="2026-08-01",
            canonical_product_id="News-Grasp",
            repo_root=repo,
            runner_path=runner,
            runner_arguments=["-NoPublish", "-DateStampOverride", "2026-08-01"],
            expected_parent_authority_path=repo / "admission.json.high-cost-parent-authority.json",
            runner_arguments_path=repo / "admission.json.runner-arguments.json",
            runner_executable_path=runner,
            authority_python_executable_path=Path(sys.executable),
            evidence_bindings=_green_evidence(tmp_path / "untrusted", repo_root=repo),
            output_path=repo / "admission.json",
        )
def test_immutable_admission_reservation_and_claim_composition_is_red(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    runner = repo / "scripts" / "ops" / "news-grasp-runner.ps1"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text("Write-Output 'runner'\n", encoding="utf-8")
    arguments_path = repo / "receipt.json.runner-arguments.json"
    arguments = ["-NoPublish", "-DateStampOverride", "2026-08-01"]
    expected_parent = repo / "receipt.json.high-cost-parent-authority.json"
    admission = repo / "receipt.json"
    reservation = repo / "receipt.json.e2e-final-reservation.json"
    claim = repo / "receipt.json.e2e-final-claim.json"
    claim_witness = repo / "receipt.json.e2e-final-claim-witness.json"
    _issue_admission(
        issue_date="2026-08-01",
        canonical_product_id="News-Grasp",
        repo_root=repo,
        runner_path=runner,
        runner_arguments=arguments,
        expected_parent_authority_path=expected_parent,
        runner_arguments_path=arguments_path,
        runner_executable_path=runner,
        authority_python_executable_path=Path(sys.executable),
        expected_reservation_receipt_path=reservation,
        expected_claim_receipt_path=claim,
        expected_claim_witness_path=claim_witness,
        evidence_bindings=_green_evidence(tmp_path / "red-immutable", repo_root=repo),
        output_path=admission,
    )
    before = admission.read_bytes()
    arguments_path.write_bytes(bridge_module._canonical_runner_arguments_bytes(arguments))
    bridge_module.validate_issued_admission(
        admission_path=admission,
        runner_arguments=arguments,
        expected_parent_authority_path=expected_parent,
        runner_arguments_path=arguments_path,
        reservation_output=reservation,
        claim_output=claim,
        claim_witness_output=claim_witness,
        actual_runner_executable_path=runner,
        actual_authority_python_executable_path=Path(sys.executable),
    )
    _write_json(
        expected_parent,
        {
            "schemaVersion": "HIGH_COST_OPERATION_ADMISSION_V1",
            "state": "activated",
            "taskIdentity": "fixture-task",
            "threadId": "fixture-thread",
            "taskRootUserEventHash": "a" * 64,
            "latestActualUserEventHash": "b" * 64,
            "authorizationId": "fixture-authorization",
            "lineageEpoch": 1,
        },
    )
    reserved = bridge_module.consume_admission(
        admission_path=admission,
        ledger_path=tmp_path / "durable" / "attempts.json",
        runner_arguments=arguments,
        parent_authority_path=expected_parent,
        runner_arguments_path=arguments_path,
        reservation_output=reservation,
        actual_runner_executable_path=runner,
        actual_authority_python_executable_path=Path(sys.executable),
    )
    assert reserved["state"] == "runner_reserved"
    assert admission.read_bytes() == before
    claimed = bridge_module.claim_runner(
        admission_path=admission,
        ledger_path=tmp_path / "durable" / "attempts.json",
        runner_arguments=arguments,
        parent_authority_path=expected_parent,
        runner_arguments_path=arguments_path,
        reservation_receipt=reservation,
        claim_output=claim,
        actual_runner_executable_path=runner,
        actual_authority_python_executable_path=Path(sys.executable),
        current_runner_pid=os.getpid(),
        claim_nonce="a" * 64,
    )
    assert claimed["state"] == "runner_claimed"
    assert admission.read_bytes() == before
    assert expected_parent.exists()
    assert reservation.exists()
    assert claim.exists()


def test_cli_does_not_accept_caller_selected_ledger() -> None:
    bridge_source = BRIDGE.read_text(encoding="utf-8-sig")
    wrapper_source = WRAPPER.read_text(encoding="utf-8-sig")
    assert 'add_argument("--ledger"' not in bridge_source
    assert 'add_argument("--runner-arguments-file", type=Path, required=True)' in bridge_source
    assert "'--ledger'" not in wrapper_source
    assert "'--runner-arguments-file'" in wrapper_source
    assert "news-grasp-e2e-final-attempts.json" in bridge_source


def test_product_identity_cannot_be_aliased(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = repo / "runner.ps1"
    runner.write_text("runner\n", encoding="utf-8")
    with pytest.raises(E2EFinalAdmissionError, match="E2E_PRODUCT_ID_INVALID"):
        issue_admission(
            issue_date="2026-08-01",
            canonical_product_id="News-Grasp-copy",
            repo_root=repo,
            runner_path=runner,
            runner_arguments=["-NoPublish"],
            evidence_bindings=_green_evidence(
                tmp_path / "evidence", repo_root=repo
            ),
            output_path=tmp_path / "admission.json",
        )


def test_final_e2e_wrapper_consumes_before_runner_and_forbids_resume() -> None:
    source = WRAPPER.read_text(encoding="utf-8-sig")
    consume = source.index("e2e_final_admission_bridge.py")
    launch = source.index("& $installedTaskPythonPath @installedLauncherArguments")
    assert consume < launch
    assert "E2EAdmissionPath" in source
    assert "$runnerArguments | ConvertTo-Json" in source
    assert source.index("$runnerArguments = @(") < source.index("'consume'") < launch
    assert "-ResumeFromStage" not in source
    assert "resume_model" not in source


def test_issue_and_consume_seal_all_executable_and_parent_argument_identities() -> None:
    import inspect

    issue_parameters = inspect.signature(bridge_module.issue_admission).parameters
    consume_parameters = inspect.signature(bridge_module.consume_admission).parameters
    for name in (
        "expected_parent_authority_path",
        "runner_executable_path",
        "authority_python_executable_path",
    ):
        assert name in issue_parameters
    for name in (
        "parent_authority_path",
        "runner_arguments_path",
        "runner_executable_path",
        "authority_python_executable_path",
    ):
        assert name in consume_parameters
    assert "actual_runner_executable_path" in consume_parameters
    assert "actual_authority_python_executable_path" in consume_parameters


def test_bridge_has_read_only_validate_issued_and_one_shot_claim_runner() -> None:
    source = BRIDGE.read_text(encoding="utf-8-sig")
    assert callable(getattr(bridge_module, "validate_issued_admission", None))
    assert callable(getattr(bridge_module, "claim_runner", None))
    assert "validate-issued" in source
    assert "claim-runner" in source
    assert "os.O_EXCL" not in source


def test_validate_issued_binds_all_future_paths_read_only_and_typed() -> None:
    admission, _ = _issue(Path(tempfile.mkdtemp()) / "p1-future-paths")
    value, arguments_path = _materialize_runner_arguments_only(admission)
    parent = Path(value["expectedParentAuthorityPath"])
    reservation = Path(value["expectedReservationReceiptPath"])
    claim = Path(value["expectedClaimReceiptPath"])
    claim_witness = Path(value["expectedClaimWitnessPath"])
    common = dict(
        admission_path=admission,
        runner_arguments=list(value["runnerArguments"]),
        runner_arguments_path=arguments_path,
        expected_parent_authority_path=parent,
        reservation_output=reservation,
        claim_output=claim,
        claim_witness_output=claim_witness,
        actual_runner_executable_path=Path(value["runnerExecutablePath"]),
        actual_authority_python_executable_path=Path(
            value["authorityPythonExecutablePath"]
        ),
    )
    result = bridge_module.validate_issued_admission(**common)
    assert result["state"] == "issued"
    assert not parent.exists()
    assert not reservation.exists()
    assert not claim.exists()
    assert not claim_witness.exists()

    drift_cases = (
        ("E2E_PARENT_AUTHORITY_DRIFT", "bad.high-cost-parent-authority.json", "expected_parent_authority_path"),
        ("E2E_RESERVATION_RECEIPT_PATH_DRIFT", "bad.e2e-final-reservation.json", "reservation_output"),
        ("E2E_CLAIM_RECEIPT_PATH_DRIFT", "bad.e2e-final-claim.json", "claim_output"),
        ("E2E_CLAIM_WITNESS_PATH_DRIFT", "bad.e2e-final-claim-witness.json", "claim_witness_output"),
    )
    for expected_code, filename, key in drift_cases:
        bad = dict(common)
        bad[key] = admission.parent / filename
        with pytest.raises(E2EFinalAdmissionError, match=expected_code):
            bridge_module.validate_issued_admission(**bad)
        assert not parent.exists()
        assert not reservation.exists()
        assert not claim.exists()
        assert not claim_witness.exists()


def test_managed_lock_is_environment_independent_across_processes() -> None:
    _configure_spawn_executable()
    context = multiprocessing.get_context("spawn")
    with tempfile.TemporaryDirectory() as root_text:
        root = Path(root_text)
        barrier = context.Barrier(2)
        result_queue = context.Queue()
        lock_target = root / "canonical-attempt.json"
        processes = [
            context.Process(
                target=_lock_probe_worker,
                args=(
                    str(root / "local-a"),
                    str(root / "temp-a"),
                    str(root),
                    str(lock_target),
                    barrier,
                    result_queue,
                ),
            ),
            context.Process(
                target=_lock_probe_worker,
                args=(
                    str(root / "local-b"),
                    str(root / "temp-b"),
                    str(root),
                    str(lock_target),
                    barrier,
                    result_queue,
                ),
            ),
        ]
        for process in processes:
            process.start()
        results = [result_queue.get(timeout=30) for _ in processes]
        for process in processes:
            process.join(timeout=30)
        assert all(process.exitcode == 0 for process in processes), results
        assert all("error" not in result for result in results), results
        assert len({result["ledger"] for result in results}) == 1
        first, second = sorted(results, key=lambda result: result["entered"])
        assert first["exited"] <= second["entered"]


def test_cross_process_reserve_claim_is_one_shot_under_environment_drift(
    tmp_path: Path,
) -> None:
    _configure_spawn_executable()
    admission, ledger = _issue(tmp_path / "cross-process")
    value, arguments = _materialize_runner_arguments_only(admission)
    parent = Path(value["expectedParentAuthorityPath"])
    _write_json(
        parent,
        {
            "schemaVersion": "HIGH_COST_OPERATION_ADMISSION_V1",
            "state": "activated",
            "taskIdentity": "fixture-task",
            "threadId": "fixture-thread",
            "taskRootUserEventHash": "a" * 64,
            "latestActualUserEventHash": "b" * 64,
            "authorizationId": "fixture-authorization",
            "lineageEpoch": 1,
        },
    )
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    result_queue = context.Queue()
    with tempfile.TemporaryDirectory() as root_text:
        root = Path(root_text)
        processes = [
            context.Process(
                target=_consume_claim_process_worker,
                args=(
                    str(admission),
                    str(ledger),
                    str(parent),
                    str(arguments),
                    str(root / "local-a"),
                    str(root / "temp-a"),
                    barrier,
                    result_queue,
                    "1" * 64,
                ),
            ),
            context.Process(
                target=_consume_claim_process_worker,
                args=(
                    str(admission),
                    str(ledger),
                    str(parent),
                    str(arguments),
                    str(root / "local-b"),
                    str(root / "temp-b"),
                    barrier,
                    result_queue,
                    "2" * 64,
                ),
            ),
        ]
        for process in processes:
            process.start()
        results = [result_queue.get(timeout=45) for _ in processes]
        for process in processes:
            process.join(timeout=45)
        assert all(process.exitcode == 0 for process in processes), results
    assert sum(result["claimed"] == "runner_claimed" for result in results) == 1
    assert sum(result["reserved"] == "runner_reserved" for result in results) == 1
    assert len({result["ledger"] for result in results}) == 1
    claim_path = Path(value["expectedClaimReceiptPath"])
    claim_bytes = claim_path.read_bytes()
    assert claim_bytes == claim_path.read_bytes()
    ledger_value = json.loads(ledger.read_text(encoding="utf-8"))
    assert ledger_value["attempts"][value["attemptKey"]]["state"] == "runner_claimed"


def test_claim_seals_and_read_only_validates_os_process_identity() -> None:
    admission, ledger = _issue(Path(tempfile.mkdtemp()) / "p3-owner")
    support_value, arguments_path = _materialize_runner_arguments_only(admission)
    parent = Path(support_value["expectedParentAuthorityPath"])
    _write_json(
        parent,
        {
            "schemaVersion": "HIGH_COST_OPERATION_ADMISSION_V1",
            "state": "activated",
            "taskIdentity": "fixture-task",
            "threadId": "fixture-thread",
            "taskRootUserEventHash": "a" * 64,
            "latestActualUserEventHash": "b" * 64,
            "authorizationId": "fixture-authorization",
            "lineageEpoch": 1,
        },
    )
    reservation = bridge_module.consume_admission(
        admission_path=admission,
        ledger_path=ledger,
        runner_arguments=list(support_value["runnerArguments"]),
        parent_authority_path=parent,
        runner_arguments_path=arguments_path,
        reservation_output=Path(support_value["expectedReservationReceiptPath"]),
        actual_runner_executable_path=Path(support_value["runnerExecutablePath"]),
        actual_authority_python_executable_path=Path(
            support_value["authorityPythonExecutablePath"]
        ),
    )
    identity = {
        "pid": 4321,
        "parentPid": 1234,
        "creationFileTimeUtc": "2026-08-10T00:00:00.0000000Z",
        "imagePath": str(Path(support_value["runnerExecutablePath"]).resolve()),
        "imageSha256": _sha256(Path(support_value["runnerExecutablePath"]).resolve()),
    }
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(bridge_module, "_query_process_identity", lambda pid: identity)
    try:
        claimed = bridge_module.claim_runner(
            admission_path=admission,
            ledger_path=ledger,
            runner_arguments=list(support_value["runnerArguments"]),
            parent_authority_path=parent,
            runner_arguments_path=arguments_path,
            reservation_receipt=Path(support_value["expectedReservationReceiptPath"]),
            claim_output=Path(support_value["expectedClaimReceiptPath"]),
            actual_runner_executable_path=Path(support_value["runnerExecutablePath"]),
            actual_authority_python_executable_path=Path(
                support_value["authorityPythonExecutablePath"]
            ),
            current_runner_pid=identity["pid"],
            claim_nonce="f" * 64,
        )
        assert claimed["ownerProcessIdentity"] == identity
        witness = bridge_module.validate_runner_claim(
            admission_path=admission,
            ledger_path=ledger,
            runner_arguments=list(support_value["runnerArguments"]),
            parent_authority_path=parent,
            runner_arguments_path=arguments_path,
            reservation_receipt=Path(support_value["expectedReservationReceiptPath"]),
            claim_receipt=Path(support_value["expectedClaimReceiptPath"]),
            actual_runner_executable_path=Path(support_value["runnerExecutablePath"]),
            actual_authority_python_executable_path=Path(
                support_value["authorityPythonExecutablePath"]
            ),
            expected_owner_pid=identity["pid"],
        )
        assert witness["claimReceiptPath"] == str(
            Path(support_value["expectedClaimReceiptPath"]).resolve()
        )
        monkeypatch.setattr(
            bridge_module,
            "_query_process_identity",
            lambda pid: {**identity, "creationFileTimeUtc": "different"},
        )
        with pytest.raises(E2EFinalAdmissionError, match="E2E_RUNNER_PROCESS_IDENTITY_DRIFT"):
            bridge_module.validate_runner_claim(
                admission_path=admission,
                ledger_path=ledger,
                runner_arguments=list(support_value["runnerArguments"]),
                parent_authority_path=parent,
                runner_arguments_path=arguments_path,
                reservation_receipt=Path(support_value["expectedReservationReceiptPath"]),
                claim_receipt=Path(support_value["expectedClaimReceiptPath"]),
                actual_runner_executable_path=Path(support_value["runnerExecutablePath"]),
                actual_authority_python_executable_path=Path(
                    support_value["authorityPythonExecutablePath"]
                ),
                expected_owner_pid=identity["pid"],
            )
        monkeypatch.setattr(
            bridge_module,
            "_query_process_identity",
            lambda pid: {
                **identity,
                "imagePath": str(
                    Path(support_value["authorityPythonExecutablePath"]).resolve()
                ),
                "imageSha256": _sha256(
                    Path(support_value["authorityPythonExecutablePath"]).resolve()
                ),
            },
        )
        with pytest.raises(E2EFinalAdmissionError, match="E2E_RUNNER_PROCESS_IDENTITY_DRIFT"):
            bridge_module.validate_runner_claim(
                admission_path=admission,
                ledger_path=ledger,
                runner_arguments=list(support_value["runnerArguments"]),
                parent_authority_path=parent,
                runner_arguments_path=arguments_path,
                reservation_receipt=Path(support_value["expectedReservationReceiptPath"]),
                claim_receipt=Path(support_value["expectedClaimReceiptPath"]),
                actual_runner_executable_path=Path(support_value["runnerExecutablePath"]),
                actual_authority_python_executable_path=Path(
                    support_value["authorityPythonExecutablePath"]
                ),
                expected_owner_pid=identity["pid"],
            )
        monkeypatch.setattr(
            bridge_module,
            "_query_process_identity",
            lambda pid: {**identity, "pid": identity["pid"] + 1},
        )
        with pytest.raises(E2EFinalAdmissionError, match="E2E_RUNNER_PROCESS_IDENTITY_DRIFT"):
            bridge_module.validate_runner_claim(
                admission_path=admission,
                ledger_path=ledger,
                runner_arguments=list(support_value["runnerArguments"]),
                parent_authority_path=parent,
                runner_arguments_path=arguments_path,
                reservation_receipt=Path(support_value["expectedReservationReceiptPath"]),
                claim_receipt=Path(support_value["expectedClaimReceiptPath"]),
                actual_runner_executable_path=Path(support_value["runnerExecutablePath"]),
                actual_authority_python_executable_path=Path(
                    support_value["authorityPythonExecutablePath"]
                ),
                expected_owner_pid=identity["pid"],
            )
    finally:
        monkeypatch.undo()


def test_claim_accepts_causal_replacement_metadata_kept_in_ledger_row(
    tmp_path: Path,
) -> None:
    """causal replacementのledger専用metadataで正規claimをcross-lineageにしない。"""

    admission, ledger = _issue(tmp_path / "replacement-claim")
    value, arguments_path = _materialize_runner_arguments_only(admission)
    parent = Path(value["expectedParentAuthorityPath"])
    _write_json(
        parent,
        {
            "schemaVersion": "HIGH_COST_OPERATION_ADMISSION_V1",
            "state": "activated",
            "taskIdentity": "fixture-task",
            "threadId": "fixture-thread",
            "taskRootUserEventHash": "a" * 64,
            "latestActualUserEventHash": "b" * 64,
            "authorizationId": "fixture-authorization",
            "lineageEpoch": 1,
        },
    )
    bridge_module.consume_admission(
        admission_path=admission,
        ledger_path=ledger,
        runner_arguments=list(value["runnerArguments"]),
        parent_authority_path=parent,
        runner_arguments_path=arguments_path,
        reservation_output=Path(value["expectedReservationReceiptPath"]),
        actual_runner_executable_path=Path(value["runnerExecutablePath"]),
        actual_authority_python_executable_path=Path(
            value["authorityPythonExecutablePath"]
        ),
    )
    ledger_value = json.loads(ledger.read_text(encoding="utf-8"))
    attempt_key = str(value["attemptKey"])
    row = ledger_value["attempts"][attempt_key]
    original_row = dict(row)
    original_row["admissionId"] = "b" * 64
    original_row["reservationReceiptSha256"] = "c" * 64
    proof_sha256 = "a" * 64
    row["causalReplacementProofSha256"] = proof_sha256
    row["replacesAdmissionId"] = original_row["admissionId"]
    ledger_value["replacements"][attempt_key] = {
        "originalAdmissionId": original_row["admissionId"],
        "originalReservationReceiptSha256": original_row[
            "reservationReceiptSha256"
        ],
        "originalRow": original_row,
        "replacementAdmissionId": row["admissionId"],
        "proofSha256": proof_sha256,
    }
    _write_json(ledger, ledger_value)
    identity = {
        "pid": 4321,
        "parentPid": 1234,
        "creationFileTimeUtc": "2026-08-10T00:00:00.0000000Z",
        "imagePath": str(Path(value["runnerExecutablePath"]).resolve()),
        "imageSha256": _sha256(Path(value["runnerExecutablePath"]).resolve()),
    }
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(bridge_module, "_query_process_identity", lambda pid: identity)
    try:
        claimed = bridge_module.claim_runner(
            admission_path=admission,
            ledger_path=ledger,
            runner_arguments=list(value["runnerArguments"]),
            parent_authority_path=parent,
            runner_arguments_path=arguments_path,
            reservation_receipt=Path(value["expectedReservationReceiptPath"]),
            claim_output=Path(value["expectedClaimReceiptPath"]),
            actual_runner_executable_path=Path(value["runnerExecutablePath"]),
            actual_authority_python_executable_path=Path(
                value["authorityPythonExecutablePath"]
            ),
            current_runner_pid=identity["pid"],
            claim_nonce="f" * 64,
        )
    finally:
        monkeypatch.undo()
    assert claimed["state"] == "runner_claimed"


@pytest.mark.skipif(os.name != "nt", reason="Windows process identity contract")
def test_windows_process_identity_is_observed_from_os() -> None:
    identity = bridge_module._query_process_identity(os.getpid())
    assert identity["pid"] == os.getpid()
    assert set(identity) == {
        "pid",
        "parentPid",
        "creationFileTimeUtc",
        "imagePath",
        "imageSha256",
    }


def test_issue_allows_safe_missing_output_ancestors_after_preflight(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = repo / "scripts" / "ops" / "news-grasp-runner.ps1"
    runner.parent.mkdir(parents=True)
    runner.write_text("Write-Output 'runner'\n", encoding="utf-8")
    output = repo / "missing" / "descendant" / "admission.json"
    output_parent = output.parent
    bridge_module.issue_admission(
        issue_date="2026-08-01",
        canonical_product_id="News-Grasp",
        repo_root=repo,
        runner_path=runner,
        runner_arguments=["-NoPublish", "-DateStampOverride", "2026-08-01"],
        expected_parent_authority_path=output_parent / "admission.high-cost-parent-authority.json",
        runner_arguments_path=output_parent / "admission.runner-arguments.json",
        expected_reservation_receipt_path=output_parent / "admission.e2e-final-reservation.json",
        expected_claim_receipt_path=output_parent / "admission.e2e-final-claim.json",
        runner_executable_path=runner,
        authority_python_executable_path=Path(sys.executable),
        evidence_bindings=_green_evidence(tmp_path / "evidence", repo_root=repo),
        output_path=output,
    )
    assert output.is_file()
    assert not (output_parent / "admission.high-cost-parent-authority.json").exists()
