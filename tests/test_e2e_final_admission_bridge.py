from __future__ import annotations

import hashlib
import json
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
    issue_admission,
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


def _write_json(path: Path, value: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def consume_admission(*, admission_path: Path, ledger_path: Path) -> dict[str, object]:
    """既存テストも実起動引数との照合を必ず通す。"""
    value = json.loads(admission_path.read_text(encoding="utf-8"))
    return _consume_admission(
        admission_path=admission_path,
        ledger_path=ledger_path,
        runner_arguments=list(value["runnerArguments"]),
    )


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
) -> tuple[Path, Path]:
    repo = repo_root or tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    runner = repo / "scripts" / "ops" / "news-grasp-runner.ps1"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text("Write-Output 'runner'\n", encoding="utf-8")
    admission = tmp_path / admission_name
    ledger = tmp_path / "durable" / "attempts.json"
    issue_admission(
        issue_date="2026-08-01",
        canonical_product_id="News-Grasp",
        repo_root=repo,
        runner_path=runner,
        runner_arguments=["-NoPublish", "-DateStampOverride", "2026-08-01"],
        evidence_bindings=_green_evidence(tmp_path / admission.stem, repo_root=repo),
        output_path=admission,
    )
    return admission, ledger


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
    assert result["state"] == "consumed"
    ledger_value = json.loads(ledger.read_text(encoding="utf-8"))
    assert list(ledger_value["attempts"]) == [
        "News-Grasp:2026-08-01:scheduled-equivalent-nopublish"
    ]


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
        match="E2E_FINAL_ATTEMPT_ALREADY_CONSUMED",
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
    with pytest.raises(E2EFinalAdmissionError, match="E2E_ADMISSION_REPLAY"):
        consume_admission(admission_path=admission, ledger_path=ledger)


def test_consumer_rejects_actual_runner_argument_drift(tmp_path: Path) -> None:
    admission, ledger = _issue(tmp_path)
    with pytest.raises(E2EFinalAdmissionError, match="E2E_COMMAND_DRIFT"):
        _consume_admission(
            admission_path=admission,
            ledger_path=ledger,
            runner_arguments=["-NoPublish", "-DateStampOverride", "2026-08-02"],
        )


def test_parallel_consume_has_exactly_one_winner(tmp_path: Path) -> None:
    admission, ledger = _issue(tmp_path)

    def consume() -> str:
        try:
            consume_admission(admission_path=admission, ledger_path=ledger)
            return "consumed"
        except E2EFinalAdmissionError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: consume(), range(2)))
    assert results.count("consumed") == 1
    assert len(results) == 2


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
    launch = source.index("& $PowerShellExe @runnerArguments")
    assert consume < launch
    assert "E2EAdmissionPath" in source
    assert "$runnerArguments | ConvertTo-Json" in source
    assert source.index("$runnerArguments = @(") < source.index("'consume'") < launch
    assert "-ResumeFromStage" not in source
    assert "resume_model" not in source
