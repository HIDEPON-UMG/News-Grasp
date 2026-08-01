"""News-Grasp final E2Eを一日一回だけ許可するadmission境界。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from tools.deepdive_red_suite_coverage import validate_red_suite_coverage


SCHEMA = "NEWS_GRASP_E2E_FINAL_ADMISSION_V1"
LEDGER_SCHEMA = "NEWS_GRASP_E2E_FINAL_ATTEMPT_LEDGER_V1"
REQUIRED_EVIDENCE_KINDS = (
    "efficiency_design",
    "adversarial_review",
    "route_manifest",
    "red_suite_coverage",
    "static",
    "simulation",
    "isolation",
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
            and value.get("requirementCount") == 3
            and value.get("viewpointCount") == 10
            and value.get("routeCount") == 5
            and value.get("coverageCellCount") == 90
            and HEX_64_RE.fullmatch(str(value.get("coverageSha256") or ""))
        ):
            raise E2EFinalAdmissionError("E2E_RED_SUITE_COVERAGE_INVALID")
        if kind == "red_suite_coverage":
            recomputed = _recompute_red_suite_coverage(repo_root)
            if value != recomputed:
                raise E2EFinalAdmissionError(
                    "E2E_RED_SUITE_COVERAGE_SOURCE_MISMATCH"
                )
        if value.get("status") != "Green":
            raise E2EFinalAdmissionError("E2E_UPSTREAM_NOT_GREEN")
        normalized.append(
            {"kind": kind, "path": str(path), "sha256": expected_hash}
        )
    if tuple(row["kind"] for row in normalized) != REQUIRED_EVIDENCE_KINDS:
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
    evidence = _normalize_evidence(evidence_bindings, repo_root=repo)
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
    _write_exclusive(Path(output_path).resolve(), value)
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
    normalized = _normalize_evidence(
        value["evidenceBindings"],
        repo_root=repo_root,
    )
    if normalized != value["evidenceBindings"]:
        raise E2EFinalAdmissionError("E2E_UPSTREAM_EVIDENCE_DRIFT")
    runner = Path(value["runnerPath"])
    if not runner.is_file() or _file_sha256(runner) != value["runnerSha256"]:
        raise E2EFinalAdmissionError("E2E_RUNNER_DRIFT")

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
