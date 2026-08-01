"""News-Grasp final E2Eを一日一回だけ許可するadmission境界。"""

from __future__ import annotations

import argparse
import hashlib
import json
import msvcrt
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from typing import Any


MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from tools.deepdive_red_suite_coverage import (
    build_requirement_viewpoint_pair_cases,
    validate_red_suite_coverage,
)
from tools.red_suite_execution import (
    PAIR_TEST_SELECTOR,
    SCHEMA as RED_SUITE_EXECUTION_SCHEMA,
    _fixture_selectors,
    _production_dependency_manifest,
    execute_red_suite,
)


SCHEMA = "NEWS_GRASP_E2E_FINAL_ADMISSION_V1"
LEDGER_SCHEMA = "NEWS_GRASP_E2E_FINAL_ATTEMPT_LEDGER_V1"
REQUIRED_EVIDENCE_KINDS = (
    "efficiency_design",
    "adversarial_review",
    "route_manifest",
    "red_suite_coverage",
    "red_suite_execution",
    "static",
    "simulation",
    "isolation",
)
CALLER_EVIDENCE_KINDS = tuple(
    kind for kind in REQUIRED_EVIDENCE_KINDS if kind != "red_suite_execution"
)
DATE_RE = re.compile(r"^20\d{2}-\d{2}-\d{2}$")
HEX_64_RE = re.compile(r"^[a-f0-9]{64}$")
CANONICAL_PRODUCT_ID = "News-Grasp"


class E2EFinalAdmissionError(RuntimeError):
    """final E2E admissionを安全に発行または消費できない。"""


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise E2EFinalAdmissionError(code) from error
    if not isinstance(value, dict):
        raise E2EFinalAdmissionError(code)
    return value


def _read_bound_json(
    path: Path,
    expected_hash: str,
    code: str,
) -> dict[str, Any]:
    """同一bytesからhash検証とJSON parseを行い、TOCTOU差を作らない。"""
    try:
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != expected_hash:
            raise E2EFinalAdmissionError("E2E_UPSTREAM_EVIDENCE_DRIFT")
        value = json.loads(payload.decode("utf-8-sig"))
    except E2EFinalAdmissionError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise E2EFinalAdmissionError(code) from error
    if not isinstance(value, dict):
        raise E2EFinalAdmissionError(code)
    return value


def _recompute_red_suite_coverage(repo_root: Path) -> dict[str, Any]:
    matrix_path = repo_root / "fixtures" / "deepdive_quality" / "tdd_acceptance_matrix.json"
    routes_path = repo_root / "config" / "deepdive_quality_routes.json"
    matrix = _read_json(matrix_path, "E2E_RED_SUITE_COVERAGE_SOURCE_INVALID")
    routes = _read_json(routes_path, "E2E_RED_SUITE_COVERAGE_SOURCE_INVALID")
    report = validate_red_suite_coverage(
        matrix,
        root=repo_root,
        route_registry=routes,
    )
    if report.get("status") != "Green" or report.get("findings") != []:
        raise E2EFinalAdmissionError("E2E_RED_SUITE_COVERAGE_SOURCE_INVALID")
    return report


def _selector_owns_node(selector: str, node_id: str) -> bool:
    return node_id == selector or node_id.startswith(f"{selector}[")


def _validate_red_suite_execution_receipt(
    value: dict[str, Any], *, repo_root: Path
) -> None:
    required_keys = {
        "schemaVersion",
        "status",
        "createdAt",
        "matrixPath",
        "matrixSha256",
        "coverageSha256",
        "fixtureSetSha256",
        "fixtureImplementationSetSha256",
        "pairCaseSetSha256",
        "historicalCorpusSha256",
        "pairCaseMode",
        "producerSha256",
        "pairTestSha256",
        "productionDependencyCount",
        "productionDependencySetSha256",
        "selectorCount",
        "selectorSetSha256",
        "selectors",
        "pairCaseCount",
        "pairNodeIds",
        "collectedNodeCount",
        "collectedNodeSetSha256",
        "collectedNodeIds",
        "passedNodeCount",
        "nodeOutcomes",
        "collectionErrors",
        "executionFailures",
        "missingOutcomes",
        "missingSelectors",
        "unexpectedNodes",
        "pytestExitCode",
    }
    if set(value) != required_keys:
        raise E2EFinalAdmissionError("E2E_RED_SUITE_EXECUTION_INVALID")
    matrix_path = (
        repo_root
        / "fixtures"
        / "deepdive_quality"
        / "tdd_acceptance_matrix.json"
    ).resolve()
    producer_path = (repo_root / "tools" / "red_suite_execution.py").resolve()
    pair_test_path = (
        repo_root / PAIR_TEST_SELECTOR.split("::", 1)[0]
    ).resolve()
    try:
        matrix = _read_json(
            matrix_path, "E2E_RED_SUITE_EXECUTION_SOURCE_INVALID"
        )
        coverage_report = _recompute_red_suite_coverage(repo_root)
        selectors = _fixture_selectors(matrix["redSuiteCoverage"])
        pair_cases = build_requirement_viewpoint_pair_cases(matrix)
        expected_pair_nodes = sorted(
            f"{PAIR_TEST_SELECTOR}[{case['caseId']}]" for case in pair_cases
        )
        collected = value["collectedNodeIds"]
        pair_nodes = value["pairNodeIds"]
        production_dependencies = _production_dependency_manifest(repo_root)
        if not all(
            isinstance(item, str) and item for item in [*collected, *pair_nodes]
        ):
            raise TypeError("node ID invalid")
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise E2EFinalAdmissionError(
            "E2E_RED_SUITE_EXECUTION_SOURCE_INVALID"
        ) from error
    if not isinstance(value["nodeOutcomes"], dict):
        raise E2EFinalAdmissionError("E2E_RED_SUITE_EXECUTION_INVALID")
    non_pair_nodes = [node for node in collected if node not in set(pair_nodes)]
    source_bindings_match = all(
        (
            value["matrixPath"] == str(matrix_path),
            value["matrixSha256"] == _file_sha256(matrix_path),
            value["coverageSha256"] == coverage_report["coverageSha256"],
            value["fixtureSetSha256"] == coverage_report["fixtureSetSha256"],
            value["fixtureImplementationSetSha256"]
            == coverage_report["fixtureImplementationSetSha256"],
            value["pairCaseSetSha256"] == coverage_report["pairCaseSetSha256"],
            value["historicalCorpusSha256"]
            == coverage_report["historicalCorpusSha256"],
            value["producerSha256"] == _file_sha256(producer_path),
            value["pairTestSha256"] == _file_sha256(pair_test_path),
            value["productionDependencyCount"]
            == len(production_dependencies),
            value["productionDependencySetSha256"]
            == _canonical_sha256(production_dependencies),
        )
    )
    execution_shape_green = all(
        (
            value["schemaVersion"] == RED_SUITE_EXECUTION_SCHEMA,
            value["status"] == "Green",
            value["pairCaseMode"] == "traceability_only",
            isinstance(value["createdAt"], str) and bool(value["createdAt"]),
            value["selectorCount"] == 49,
            value["selectors"] == selectors,
            value["selectorSetSha256"] == _canonical_sha256(selectors),
            value["pairCaseCount"] == 140,
            pair_nodes == expected_pair_nodes,
            len(pair_nodes) == len(set(pair_nodes)) == 140,
            collected == sorted(set(collected)),
            value["collectedNodeCount"] == len(collected) == 190,
            value["collectedNodeSetSha256"]
            == _canonical_sha256(collected),
            value["passedNodeCount"] == len(collected) == 190,
            set(value["nodeOutcomes"]) == set(collected),
            all(
                outcome == "passed"
                for outcome in value["nodeOutcomes"].values()
            ),
            value["collectionErrors"] == [],
            value["executionFailures"] == [],
            value["missingOutcomes"] == [],
            value["missingSelectors"] == [],
            value["unexpectedNodes"] == [],
            value["pytestExitCode"] == 0,
            all(
                any(_selector_owns_node(selector, node) for node in collected)
                for selector in selectors
            ),
            all(
                any(_selector_owns_node(selector, node) for selector in selectors)
                for node in non_pair_nodes
            ),
        )
    )
    if not source_bindings_match:
        raise E2EFinalAdmissionError(
            "E2E_RED_SUITE_EXECUTION_SOURCE_MISMATCH"
        )
    if not execution_shape_green:
        raise E2EFinalAdmissionError("E2E_RED_SUITE_EXECUTION_INVALID")


def _write_exclusive(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise E2EFinalAdmissionError("E2E_ADMISSION_ALREADY_EXISTS") from error


@contextmanager
def _issue_execution_lock(output_path: Path) -> Iterator[None]:
    lock_root = Path(tempfile.gettempdir()) / "news-grasp-e2e-final-admission-locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_name = f"{_canonical_sha256(str(output_path.resolve()))}.lock"
    lock_path = lock_root / lock_name
    with lock_path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
            os.fsync(stream.fileno())
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise E2EFinalAdmissionError("E2E_ADMISSION_ISSUE_BUSY") from error
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def _replace_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _normalize_evidence(
    rows: list[dict[str, str]],
    *,
    repo_root: Path,
    expected_kinds: tuple[str, ...] = REQUIRED_EVIDENCE_KINDS,
) -> list[dict[str, str]]:
    if not isinstance(rows, list):
        raise E2EFinalAdmissionError("E2E_UPSTREAM_EVIDENCE_INVALID")
    normalized: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"kind", "path", "sha256"}:
            raise E2EFinalAdmissionError("E2E_UPSTREAM_EVIDENCE_INVALID")
        kind = str(row["kind"])
        try:
            path = Path(str(row["path"])).resolve(strict=True)
        except OSError as error:
            raise E2EFinalAdmissionError("E2E_UPSTREAM_EVIDENCE_INVALID") from error
        expected_hash = str(row["sha256"]).casefold()
        if not path.is_file() or HEX_64_RE.fullmatch(expected_hash) is None:
            raise E2EFinalAdmissionError("E2E_UPSTREAM_EVIDENCE_DRIFT")
        value = _read_bound_json(
            path,
            expected_hash,
            "E2E_UPSTREAM_EVIDENCE_INVALID",
        )
        if kind == "red_suite_coverage" and not (
            value.get("schemaVersion") == "RED_SUITE_COVERAGE_REPORT_V1"
            and value.get("status") == "Green"
            and value.get("findings") == []
            and value.get("requirementCount") == 14
            and value.get("viewpointCount") == 10
            and value.get("routeCount") == 5
            and value.get("coverageCellCount") == 200
            and value.get("fixtureCount") == 49
            and HEX_64_RE.fullmatch(str(value.get("fixtureSetSha256") or ""))
            and HEX_64_RE.fullmatch(
                str(value.get("fixtureImplementationSetSha256") or "")
            )
            and HEX_64_RE.fullmatch(
                str(value.get("historicalCorpusSha256") or "")
            )
            and value.get("pairCaseCount") == 140
            and value.get("pairCaseMode") == "traceability_only"
            and HEX_64_RE.fullmatch(
                str(value.get("pairCaseSetSha256") or "")
            )
            and HEX_64_RE.fullmatch(str(value.get("coverageSha256") or ""))
        ):
            raise E2EFinalAdmissionError("E2E_RED_SUITE_COVERAGE_INVALID")
        if kind == "red_suite_coverage":
            recomputed = _recompute_red_suite_coverage(repo_root)
            if value != recomputed:
                raise E2EFinalAdmissionError(
                    "E2E_RED_SUITE_COVERAGE_SOURCE_MISMATCH"
                )
        if kind == "red_suite_execution":
            _validate_red_suite_execution_receipt(value, repo_root=repo_root)
        if value.get("status") != "Green":
            raise E2EFinalAdmissionError("E2E_UPSTREAM_NOT_GREEN")
        normalized.append(
            {"kind": kind, "path": str(path), "sha256": expected_hash}
        )
    if tuple(row["kind"] for row in normalized) != expected_kinds:
        raise E2EFinalAdmissionError("E2E_UPSTREAM_EVIDENCE_INCOMPLETE")
    return normalized


def _admission_projection(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "admissionId"}


def _validate_admission(value: dict[str, Any]) -> None:
    required = {
        "schemaVersion",
        "state",
        "purpose",
        "singleUse",
        "resumePolicy",
        "issueDate",
        "canonicalProductId",
        "attemptKey",
        "repoRoot",
        "runnerPath",
        "runnerSha256",
        "runnerArguments",
        "commandSha256",
        "evidenceBindings",
        "evidenceSetSha256",
        "admissionId",
    }
    if set(value) != required:
        raise E2EFinalAdmissionError("E2E_ADMISSION_INVALID")
    if (
        value.get("schemaVersion") != SCHEMA
        or value.get("state") != "issued"
        or value.get("purpose") != "final_confirmation_only"
        or value.get("singleUse") is not True
        or value.get("resumePolicy") != "forbidden"
        or not DATE_RE.fullmatch(str(value.get("issueDate") or ""))
        or value.get("attemptKey")
        != f"{value.get('canonicalProductId')}:{value.get('issueDate')}:scheduled-equivalent-nopublish"
        or value.get("commandSha256")
        != _canonical_sha256(value.get("runnerArguments"))
        or value.get("evidenceSetSha256")
        != _canonical_sha256(value.get("evidenceBindings"))
        or value.get("admissionId")
        != _canonical_sha256(_admission_projection(value))
    ):
        raise E2EFinalAdmissionError("E2E_ADMISSION_IDENTITY_DRIFT")


def _validate_consumed_admission(value: dict[str, Any]) -> None:
    if set(value) != {
        "schemaVersion",
        "state",
        "purpose",
        "singleUse",
        "resumePolicy",
        "issueDate",
        "canonicalProductId",
        "attemptKey",
        "repoRoot",
        "runnerPath",
        "runnerSha256",
        "runnerArguments",
        "commandSha256",
        "evidenceBindings",
        "evidenceSetSha256",
        "admissionId",
        "consumptionSha256",
    }:
        raise E2EFinalAdmissionError("E2E_ADMISSION_INVALID")
    issued = dict(value)
    issued.pop("consumptionSha256")
    issued["state"] = "issued"
    _validate_admission(issued)
    expected = _canonical_sha256(
        {
            "attemptKey": value["attemptKey"],
            "admissionId": value["admissionId"],
        }
    )
    if value.get("state") != "consumed" or value.get("consumptionSha256") != expected:
        raise E2EFinalAdmissionError("E2E_ADMISSION_IDENTITY_DRIFT")


def issue_admission(
    *,
    issue_date: str,
    canonical_product_id: str,
    repo_root: Path,
    runner_path: Path,
    runner_arguments: list[str],
    evidence_bindings: list[dict[str, str]],
    output_path: Path,
) -> dict[str, Any]:
    """全上流証拠を実読込し、未消費のfinal admissionを発行する。"""

    if not DATE_RE.fullmatch(issue_date):
        raise E2EFinalAdmissionError("E2E_ISSUE_DATE_INVALID")
    if canonical_product_id != CANONICAL_PRODUCT_ID:
        raise E2EFinalAdmissionError("E2E_PRODUCT_ID_INVALID")
    try:
        repo = Path(repo_root).resolve(strict=True)
        runner = Path(runner_path).resolve(strict=True)
    except OSError as error:
        raise E2EFinalAdmissionError("E2E_RUNNER_INVALID") from error
    if not repo.is_dir() or not runner.is_file():
        raise E2EFinalAdmissionError("E2E_RUNNER_INVALID")
    try:
        runner.relative_to(repo)
    except ValueError as error:
        raise E2EFinalAdmissionError("E2E_RUNNER_OUTSIDE_REPO") from error
    if (
        not isinstance(runner_arguments, list)
        or not runner_arguments
        or any(not isinstance(item, str) or not item for item in runner_arguments)
        or "-ResumeFromStage" in runner_arguments
        or "-NoPublish" not in runner_arguments
    ):
        raise E2EFinalAdmissionError("E2E_COMMAND_FORBIDDEN")
    if isinstance(evidence_bindings, list) and any(
        isinstance(row, dict) and row.get("kind") == "red_suite_execution"
        for row in evidence_bindings
    ):
        raise E2EFinalAdmissionError(
            "E2E_RED_SUITE_EXECUTION_CALLER_FORBIDDEN"
        )
    caller_evidence = _normalize_evidence(
        evidence_bindings,
        repo_root=repo,
        expected_kinds=CALLER_EVIDENCE_KINDS,
    )
    resolved_output = Path(output_path).resolve()
    execution_path = resolved_output.with_name(
        f"{resolved_output.stem}.red-suite-execution.json"
    )
    with _issue_execution_lock(resolved_output):
        if resolved_output.exists() or execution_path.exists():
            raise E2EFinalAdmissionError("E2E_ADMISSION_ALREADY_EXISTS")
        try:
            execution_receipt = execute_red_suite(
                matrix_path=(
                    repo
                    / "fixtures"
                    / "deepdive_quality"
                    / "tdd_acceptance_matrix.json"
                ),
                root=repo,
            )
        except (KeyError, OSError, TypeError, ValueError) as error:
            raise E2EFinalAdmissionError(
                "E2E_RED_SUITE_EXECUTION_INVALID"
            ) from error
        _validate_red_suite_execution_receipt(execution_receipt, repo_root=repo)
        _write_exclusive(execution_path, execution_receipt)
        execution_binding = {
            "kind": "red_suite_execution",
            "path": str(execution_path),
            "sha256": _file_sha256(execution_path),
        }
        combined_evidence: list[dict[str, str]] = []
        for row in caller_evidence:
            combined_evidence.append(row)
            if row["kind"] == "red_suite_coverage":
                combined_evidence.append(execution_binding)
        evidence = _normalize_evidence(combined_evidence, repo_root=repo)
        value: dict[str, Any] = {
            "schemaVersion": SCHEMA,
            "state": "issued",
            "purpose": "final_confirmation_only",
            "singleUse": True,
            "resumePolicy": "forbidden",
            "issueDate": issue_date,
            "canonicalProductId": canonical_product_id,
            "attemptKey": (
                f"{canonical_product_id}:{issue_date}:"
                "scheduled-equivalent-nopublish"
            ),
            "repoRoot": str(repo),
            "runnerPath": str(runner),
            "runnerSha256": _file_sha256(runner),
            "runnerArguments": runner_arguments,
            "commandSha256": _canonical_sha256(runner_arguments),
            "evidenceBindings": evidence,
            "evidenceSetSha256": _canonical_sha256(evidence),
        }
        value["admissionId"] = _canonical_sha256(value)
        _write_exclusive(resolved_output, value)
        return value


def consume_admission(
    *,
    admission_path: Path,
    ledger_path: Path,
    runner_arguments: list[str],
) -> dict[str, Any]:
    """証拠鮮度を再検証し、日付単位のattemptを原子的に消費する。"""

    try:
        admission = Path(admission_path).resolve(strict=True)
    except OSError as error:
        raise E2EFinalAdmissionError("E2E_ADMISSION_INVALID") from error
    value = _read_json(admission, "E2E_ADMISSION_INVALID")
    if value.get("schemaVersion") == SCHEMA and value.get("state") == "consumed":
        _validate_consumed_admission(value)
        raise E2EFinalAdmissionError("E2E_ADMISSION_REPLAY")
    _validate_admission(value)
    if (
        not isinstance(runner_arguments, list)
        or not runner_arguments
        or not all(isinstance(item, str) and item for item in runner_arguments)
        or runner_arguments != value["runnerArguments"]
    ):
        raise E2EFinalAdmissionError("E2E_COMMAND_DRIFT")
    try:
        repo_root = Path(value["repoRoot"]).resolve(strict=True)
    except OSError as error:
        raise E2EFinalAdmissionError("E2E_ADMISSION_INVALID") from error
    runner = Path(value["runnerPath"])
    if not runner.is_file() or _file_sha256(runner) != value["runnerSha256"]:
        raise E2EFinalAdmissionError("E2E_RUNNER_DRIFT")
    normalized = _normalize_evidence(
        value["evidenceBindings"],
        repo_root=repo_root,
    )
    if normalized != value["evidenceBindings"]:
        raise E2EFinalAdmissionError("E2E_UPSTREAM_EVIDENCE_DRIFT")
    ledger = Path(ledger_path).resolve()
    lock = ledger.with_suffix(ledger.suffix + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise E2EFinalAdmissionError("E2E_ATTEMPT_LEDGER_BUSY") from error
    try:
        os.close(descriptor)
        if ledger.exists():
            ledger_value = _read_json(ledger, "E2E_ATTEMPT_LEDGER_INVALID")
            if (
                ledger_value.get("schemaVersion") != LEDGER_SCHEMA
                or not isinstance(ledger_value.get("attempts"), dict)
            ):
                raise E2EFinalAdmissionError("E2E_ATTEMPT_LEDGER_INVALID")
        else:
            ledger_value = {"schemaVersion": LEDGER_SCHEMA, "attempts": {}}
        attempt_key = value["attemptKey"]
        if attempt_key in ledger_value["attempts"]:
            raise E2EFinalAdmissionError("E2E_FINAL_ATTEMPT_ALREADY_CONSUMED")
        ledger_value["attempts"][attempt_key] = {
            "admissionId": value["admissionId"],
            "runnerSha256": value["runnerSha256"],
            "commandSha256": value["commandSha256"],
            "evidenceSetSha256": value["evidenceSetSha256"],
        }
        _replace_json(ledger, ledger_value)
    finally:
        lock.unlink(missing_ok=True)

    value["state"] = "consumed"
    value["consumptionSha256"] = _canonical_sha256(
        {
            "attemptKey": value["attemptKey"],
            "admissionId": value["admissionId"],
        }
    )
    _replace_json(admission, value)
    return value


def _issue_from_manifest(manifest_path: Path, output_path: Path) -> dict[str, Any]:
    manifest = _read_json(manifest_path, "E2E_ISSUE_MANIFEST_INVALID")
    return issue_admission(
        issue_date=str(manifest.get("issueDate") or ""),
        canonical_product_id=str(manifest.get("canonicalProductId") or ""),
        repo_root=Path(str(manifest.get("repoRoot") or "")),
        runner_path=Path(str(manifest.get("runnerPath") or "")),
        runner_arguments=manifest.get("runnerArguments"),
        evidence_bindings=manifest.get("evidenceBindings"),
        output_path=output_path,
    )


def default_attempt_ledger_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise E2EFinalAdmissionError("E2E_ATTEMPT_LEDGER_ROOT_MISSING")
    return (
        Path(local_app_data)
        / "AIHarness"
        / "news-grasp-e2e-final-attempts.json"
    )


def _read_runner_arguments(path: Path) -> list[str]:
    try:
        value = json.loads(Path(path).resolve(strict=True).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise E2EFinalAdmissionError("E2E_RUNNER_ARGUMENTS_INVALID") from error
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise E2EFinalAdmissionError("E2E_RUNNER_ARGUMENTS_INVALID")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    issue_parser = subparsers.add_parser("issue")
    issue_parser.add_argument("--manifest", type=Path, required=True)
    issue_parser.add_argument("--output", type=Path, required=True)
    consume_parser = subparsers.add_parser("consume")
    consume_parser.add_argument("--admission", type=Path, required=True)
    consume_parser.add_argument("--runner-arguments-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "issue":
            result = _issue_from_manifest(args.manifest, args.output)
        else:
            result = consume_admission(
                admission_path=args.admission,
                ledger_path=default_attempt_ledger_path(),
                runner_arguments=_read_runner_arguments(args.runner_arguments_file),
            )
    except E2EFinalAdmissionError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
