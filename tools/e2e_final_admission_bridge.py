"""News-Grasp final E2Eを一日一回だけ許可するadmission境界。"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
import hashlib
import json
import msvcrt
import os
import re
import stat
import sys
import tempfile
import time
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
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_ADMISSION_BYTES = 64 * 1024
MAX_RUNNER_ARGUMENTS_BYTES = 64 * 1024
MAX_EXECUTABLE_BYTES = 64 * 1024 * 1024
EXPECTED_PARENT_AUTHORITY_SUFFIX = ".high-cost-parent-authority.json"
EXPECTED_RUNNER_ARGUMENTS_SUFFIX = ".runner-arguments.json"
EXPECTED_RESERVATION_RECEIPT_SUFFIX = ".e2e-final-reservation.json"
EXPECTED_CLAIM_RECEIPT_SUFFIX = ".e2e-final-claim.json"
EXPECTED_CLAIM_WITNESS_SUFFIX = ".e2e-final-claim-witness.json"
RESERVATION_SCHEMA = "E2E_FINAL_ADMISSION_CONSUMPTION_V1"
CLAIM_SCHEMA = "E2E_FINAL_RUNNER_CLAIM_V1"
CLAIM_WITNESS_SCHEMA = "E2E_FINAL_RUNNER_CLAIM_WITNESS_V1"
PROCESS_IDENTITY_FIELDS = {
    "pid",
    "parentPid",
    "creationFileTimeUtc",
    "imagePath",
    "imageSha256",
}
KNOWN_LOCAL_APP_DATA_GUID = (
    0xF1B32785,
    0x6FBA,
    0x4FCF,
    (0x9D, 0x55, 0x7B, 0x8E, 0x7F, 0x15, 0x70, 0x91),
)


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


def _has_reparse_component(path: Path, *, stop_at: Path | None = None) -> bool:
    cursor = Path(os.path.abspath(os.fspath(path)))
    stop_key = (
        os.path.normcase(os.path.normpath(os.path.abspath(os.fspath(stop_at))))
        if stop_at is not None
        else None
    )
    try:
        while True:
            item = cursor.lstat()
            if stat.S_ISLNK(item.st_mode) or (
                int(getattr(item, "st_file_attributes", 0))
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            ):
                return True
            if stop_key is not None and os.path.normcase(
                os.path.normpath(os.path.abspath(os.fspath(cursor)))
            ) == stop_key:
                return False
            parent = cursor.parent
            if parent == cursor:
                return False
            cursor = parent
    except OSError:
        return True


def _read_stable_bytes(
    path: Path,
    *,
    max_bytes: int,
    code: str,
    trusted_root: Path | None = None,
) -> bytes:
    candidate = Path(os.path.abspath(os.fspath(path)))
    try:
        before = candidate.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or _has_reparse_component(candidate.parent, stop_at=trusted_root)
            or before.st_size > max_bytes
        ):
            raise OSError("non-regular or oversized file")
        with candidate.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if opened.st_size > max_bytes:
                raise OSError("oversized opened file")
            raw = stream.read(opened.st_size)
            opened_after = os.fstat(stream.fileno())
        after = candidate.lstat()
    except OSError as error:
        raise E2EFinalAdmissionError(code) from error
    if (
        len(raw) != opened.st_size
        or len(
            {
                (item.st_dev, item.st_ino, item.st_size)
                for item in (before, opened, opened_after, after)
            }
        )
        != 1
    ):
        raise E2EFinalAdmissionError(code)
    return raw


def _file_sha256(path: Path, *, max_bytes: int = MAX_EXECUTABLE_BYTES) -> str:
    return hashlib.sha256(
        _read_stable_bytes(
            path,
            max_bytes=max_bytes,
            code="E2E_FILE_IDENTITY_INVALID",
        )
    ).hexdigest()


def _read_json(path: Path, code: str) -> dict[str, Any]:
    try:
        max_bytes = MAX_ADMISSION_BYTES if "ADMISSION" in code else MAX_JSON_BYTES
        value = json.loads(
            _read_stable_bytes(path, max_bytes=max_bytes, code=code).decode(
                "utf-8-sig"
            )
        )
    except E2EFinalAdmissionError:
        raise
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
        payload = _read_stable_bytes(
            path,
            max_bytes=MAX_JSON_BYTES,
            code=code,
        )
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
            value["selectorCount"] == 60,
            value["selectors"] == selectors,
            value["selectorSetSha256"] == _canonical_sha256(selectors),
            value["pairCaseCount"] == 150,
            pair_nodes == expected_pair_nodes,
            len(pair_nodes) == len(set(pair_nodes)) == 150,
            collected == sorted(set(collected)),
            value["collectedNodeCount"] == len(collected) == 211,
            value["collectedNodeSetSha256"]
            == _canonical_sha256(collected),
            value["passedNodeCount"] == len(collected) == 211,
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


def _canonical_file(
    path: Path,
    *,
    code: str,
    root: Path | None = None,
    trusted_root: Path | None = None,
    max_bytes: int = MAX_EXECUTABLE_BYTES,
) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute() or candidate.is_symlink():
        raise E2EFinalAdmissionError(code)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise E2EFinalAdmissionError(code) from error
    if trusted_root is not None:
        trusted_root = Path(trusted_root).resolve(strict=True)
        try:
            resolved.relative_to(trusted_root)
        except ValueError as error:
            raise E2EFinalAdmissionError(code) from error
    if not resolved.is_file() or _has_reparse_component(
        candidate, stop_at=trusted_root
    ):
        raise E2EFinalAdmissionError(code)
    if root is not None:
        try:
            resolved.relative_to(Path(root).resolve(strict=True))
        except (OSError, ValueError) as error:
            raise E2EFinalAdmissionError(code) from error
    _read_stable_bytes(
        resolved,
        max_bytes=max_bytes,
        code=code,
        trusted_root=trusted_root,
    )
    return resolved


def _trusted_workspace_root(repo_root: Path) -> Path | None:
    """OneDrive上でもworkspace境界内のreparseだけを拒否する。"""

    for candidate in (repo_root, *repo_root.parents):
        if (
            (candidate / "tools" / "harness" / "high_cost_operation_budget.py").is_file()
            and (candidate / "News-Grasp").is_dir()
        ):
            return candidate.resolve()
    return None


def _canonical_directory(path: Path, *, code: str) -> Path:
    try:
        resolved = Path(path).resolve(strict=True)
    except OSError as error:
        raise E2EFinalAdmissionError(code) from error
    if not resolved.is_dir() or _has_reparse_component(resolved):
        raise E2EFinalAdmissionError(code)
    return resolved


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(os.fspath(path))))


def _path_is_within(path: Path, root: Path) -> bool:
    candidate_key = _path_key(path)
    root_key = _path_key(root).rstrip("\\/")
    return candidate_key == root_key or candidate_key.startswith(root_key + os.sep)


def _is_reparse_stat(item: os.stat_result) -> bool:
    return bool(
        stat.S_ISLNK(item.st_mode)
        or (
            int(getattr(item, "st_file_attributes", 0))
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        )
    )


def _validate_existing_ancestor_chain(
    candidate: Path,
    *,
    root: Path,
    code: str,
) -> None:
    """候補のnearest-existing-ancestorからrootまでを再parse拒否で検査する。"""

    cursor = Path(os.path.abspath(os.fspath(candidate)))
    root_abs = Path(os.path.abspath(os.fspath(root)))
    while True:
        try:
            item = cursor.lstat()
        except FileNotFoundError:
            parent = cursor.parent
            if parent == cursor:
                raise E2EFinalAdmissionError(code)
            cursor = parent
            continue
        except OSError as error:
            raise E2EFinalAdmissionError(code) from error
        if not stat.S_ISDIR(item.st_mode) or _is_reparse_stat(item):
            raise E2EFinalAdmissionError(code)
        if _path_key(cursor) == _path_key(root_abs):
            return
        parent = cursor.parent
        if parent == cursor or not _path_is_within(parent, root_abs):
            raise E2EFinalAdmissionError(code)
        cursor = parent


def _known_local_app_data_root() -> Path:
    if os.name != "nt":
        return Path.home() / ".local" / "share"

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", wintypes.BYTE * 8),
        ]

    data1, data2, data3, data4 = KNOWN_LOCAL_APP_DATA_GUID
    guid = _GUID(data1, data2, data3, (wintypes.BYTE * 8)(*data4))
    result = ctypes.c_wchar_p()
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    shell32.SHGetKnownFolderPath.argtypes = [
        ctypes.POINTER(_GUID),
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_wchar_p),
    ]
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    status = shell32.SHGetKnownFolderPath(
        ctypes.byref(guid), 0, wintypes.HANDLE(0), ctypes.byref(result)
    )
    if status != 0 or not result.value:
        raise E2EFinalAdmissionError("E2E_ATTEMPT_LEDGER_ROOT_MISSING")
    try:
        return Path(result.value)
    finally:
        ole32.CoTaskMemFree(result)


def _managed_authority_root() -> Path:
    base = _known_local_app_data_root()
    root = Path(os.path.abspath(os.fspath(base / "AIHarness")))
    try:
        _validate_existing_ancestor_chain(root, root=base, code="E2E_ATTEMPT_LEDGER_ROOT_INVALID")
        root.mkdir(parents=False, exist_ok=True)
        root = _canonical_directory(root, code="E2E_ATTEMPT_LEDGER_ROOT_INVALID")
        _validate_existing_ancestor_chain(root, root=base, code="E2E_ATTEMPT_LEDGER_ROOT_INVALID")
    except E2EFinalAdmissionError:
        raise
    except OSError as error:
        raise E2EFinalAdmissionError("E2E_ATTEMPT_LEDGER_ROOT_INVALID") from error
    return root


def _canonical_process_creation_time(filetime: "_FILETIME") -> str:
    ticks = (int(filetime.dwHighDateTime) << 32) | int(filetime.dwLowDateTime)
    value = datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(
        microseconds=ticks / 10
    )
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _query_process_identity(process_id: int) -> dict[str, Any]:
    """caller申告ではなくOSからrunner process identityを取得する。"""

    if type(process_id) is not int or process_id <= 0:
        raise E2EFinalAdmissionError("E2E_RUNNER_PROCESS_IDENTITY_UNAVAILABLE")
    if os.name != "nt":
        try:
            image = Path(os.readlink(f"/proc/{process_id}/exe"))
            parent_pid = os.getppid() if process_id == os.getpid() else 0
            creation = datetime.fromtimestamp(
                image.stat().st_ctime, tz=timezone.utc
            ).isoformat(timespec="microseconds").replace("+00:00", "Z")
        except (OSError, ValueError) as error:
            raise E2EFinalAdmissionError(
                "E2E_RUNNER_PROCESS_IDENTITY_UNAVAILABLE"
            ) from error
    else:
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            process = kernel32.OpenProcess(0x1000 | 0x0400, False, process_id)
            if not process:
                raise OSError(ctypes.get_last_error())
            try:
                image_buffer = ctypes.create_unicode_buffer(32768)
                image_length = wintypes.DWORD(len(image_buffer))
                if not kernel32.QueryFullProcessImageNameW(
                    process, 0, image_buffer, ctypes.byref(image_length)
                ):
                    raise OSError(ctypes.get_last_error())

                class _FILETIME(ctypes.Structure):
                    _fields_ = [
                        ("dwLowDateTime", wintypes.DWORD),
                        ("dwHighDateTime", wintypes.DWORD),
                    ]

                creation_time = _FILETIME()
                exit_time = _FILETIME()
                kernel_time = _FILETIME()
                user_time = _FILETIME()
                if not kernel32.GetProcessTimes(
                    process,
                    ctypes.byref(creation_time),
                    ctypes.byref(exit_time),
                    ctypes.byref(kernel_time),
                    ctypes.byref(user_time),
                ):
                    raise OSError(ctypes.get_last_error())
                image = Path(image_buffer.value)
                creation = _canonical_process_creation_time(creation_time)
            finally:
                kernel32.CloseHandle(process)

            class _PROCESSENTRY32W(ctypes.Structure):
                _fields_ = [
                    ("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260),
                ]

            snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
            if snapshot == ctypes.c_void_p(-1).value:
                raise OSError(ctypes.get_last_error())
            parent_pid = 0
            try:
                entry = _PROCESSENTRY32W()
                entry.dwSize = ctypes.sizeof(entry)
                first = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
                while first:
                    if int(entry.th32ProcessID) == process_id:
                        parent_pid = int(entry.th32ParentProcessID)
                        break
                    first = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
            finally:
                kernel32.CloseHandle(snapshot)
            if parent_pid <= 0:
                raise OSError("parent process unavailable")
        except (OSError, AttributeError, TypeError, ValueError) as error:
            raise E2EFinalAdmissionError(
                "E2E_RUNNER_PROCESS_IDENTITY_UNAVAILABLE"
            ) from error
    canonical_image = _canonical_file(
        image,
        code="E2E_RUNNER_PROCESS_IDENTITY_UNAVAILABLE",
        max_bytes=MAX_EXECUTABLE_BYTES,
    )
    return {
        "pid": process_id,
        "parentPid": parent_pid,
        "creationFileTimeUtc": creation,
        "imagePath": str(canonical_image),
        "imageSha256": _file_sha256(canonical_image),
    }


def _validate_process_identity(
    value: object,
    *,
    expected_pid: int | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != PROCESS_IDENTITY_FIELDS:
        raise E2EFinalAdmissionError("E2E_RUNNER_PROCESS_IDENTITY_INVALID")
    if (
        type(value.get("pid")) is not int
        or value["pid"] <= 0
        or type(value.get("parentPid")) is not int
        or value["parentPid"] < 0
        or not isinstance(value.get("creationFileTimeUtc"), str)
        or not value["creationFileTimeUtc"]
        or not isinstance(value.get("imagePath"), str)
        or not Path(value["imagePath"]).is_absolute()
        or HEX_64_RE.fullmatch(str(value.get("imageSha256") or "")) is None
    ):
        raise E2EFinalAdmissionError("E2E_RUNNER_PROCESS_IDENTITY_INVALID")
    if expected_pid is not None and value["pid"] != expected_pid:
        raise E2EFinalAdmissionError("E2E_RUNNER_PROCESS_IDENTITY_DRIFT")
    image = _canonical_file(
        Path(value["imagePath"]),
        code="E2E_RUNNER_PROCESS_IDENTITY_INVALID",
        max_bytes=MAX_EXECUTABLE_BYTES,
    )
    if _path_key(image) != _path_key(Path(value["imagePath"])):
        raise E2EFinalAdmissionError("E2E_RUNNER_PROCESS_IDENTITY_DRIFT")
    if _file_sha256(image) != value["imageSha256"]:
        raise E2EFinalAdmissionError("E2E_RUNNER_PROCESS_IDENTITY_DRIFT")
    return dict(value)


def _canonical_expected_path(
    path: Path,
    *,
    repo_root: Path,
    suffix: str,
    code: str = "E2E_PARENT_AUTHORITY_PATH_INVALID",
    exists_code: str = "E2E_PARENT_AUTHORITY_OUTPUT_EXISTS",
    require_absent: bool = True,
) -> Path:
    """未生成の成果物候補を、既存ancestorだけで検証する。"""

    candidate = Path(os.path.abspath(os.fspath(path)))
    if not candidate.is_absolute() or not candidate.name.endswith(suffix):
        raise E2EFinalAdmissionError(code)
    try:
        root = _canonical_directory(repo_root, code=code)
        candidate.relative_to(root)
    except (E2EFinalAdmissionError, ValueError) as error:
        raise E2EFinalAdmissionError(code) from error
    _validate_existing_ancestor_chain(candidate.parent, root=root, code=code)
    try:
        candidate_item = candidate.lstat()
    except FileNotFoundError:
        candidate_item = None
    except OSError as error:
        raise E2EFinalAdmissionError(code) from error
    if candidate_item is not None:
        if require_absent:
            raise E2EFinalAdmissionError(exists_code)
        if stat.S_ISLNK(candidate_item.st_mode) or not stat.S_ISREG(
            candidate_item.st_mode
        ):
            raise E2EFinalAdmissionError(code)
        if (
            int(getattr(candidate_item, "st_file_attributes", 0))
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise E2EFinalAdmissionError(code)
    return candidate


def _canonical_future_output_path(
    path: Path,
    *,
    repo_root: Path,
    code: str,
    exists_code: str,
) -> Path:
    """repo内のfuture leafを、missing descendant許可・reparse拒否で固定する。"""

    candidate = Path(os.path.abspath(os.fspath(path)))
    root = _canonical_directory(repo_root, code=code)
    if not _path_is_within(candidate, root):
        raise E2EFinalAdmissionError(code)
    _validate_existing_ancestor_chain(candidate.parent, root=root, code=code)
    try:
        item = candidate.lstat()
    except FileNotFoundError:
        return candidate
    except OSError as error:
        raise E2EFinalAdmissionError(code) from error
    if _is_reparse_stat(item) or not stat.S_ISREG(item.st_mode):
        raise E2EFinalAdmissionError(code)
    raise E2EFinalAdmissionError(exists_code)


def _canonical_expected_parent_authority_path(
    path: Path,
    *,
    repo_root: Path,
    code: str = "E2E_PARENT_AUTHORITY_PATH_INVALID",
    require_absent: bool = True,
) -> Path:
    return _canonical_expected_path(
        path,
        repo_root=repo_root,
        suffix=EXPECTED_PARENT_AUTHORITY_SUFFIX,
        code=code,
        exists_code="E2E_PARENT_AUTHORITY_OUTPUT_EXISTS",
        require_absent=require_absent,
    )


def _canonical_expected_runner_arguments_path(
    path: Path,
    *,
    repo_root: Path,
    require_absent: bool = True,
) -> Path:
    return _canonical_expected_path(
        path,
        repo_root=repo_root,
        suffix=EXPECTED_RUNNER_ARGUMENTS_SUFFIX,
        code="E2E_RUNNER_ARGUMENTS_PATH_INVALID",
        exists_code="E2E_RUNNER_ARGUMENTS_OUTPUT_EXISTS",
        require_absent=require_absent,
    )


def _canonical_expected_receipt_path(
    path: Path,
    *,
    repo_root: Path,
    suffix: str,
    code: str,
    exists_code: str,
    require_absent: bool = True,
) -> Path:
    return _canonical_expected_path(
        path,
        repo_root=repo_root,
        suffix=suffix,
        code=code,
        exists_code=exists_code,
        require_absent=require_absent,
    )


@contextmanager
def _issue_execution_lock(
    output_path: Path,
    *,
    admission_path: Path | None = None,
    attempt_key: str | None = None,
    wait_for_lock: bool = True,
) -> Iterator[None]:
    managed_root = _managed_authority_root()
    lock_root = managed_root / "news-grasp-e2e-final-admission-locks"
    _validate_existing_ancestor_chain(
        lock_root,
        root=managed_root,
        code="E2E_ATTEMPT_LEDGER_ROOT_INVALID",
    )
    lock_root.mkdir(parents=True, exist_ok=True)
    _canonical_directory(lock_root, code="E2E_ATTEMPT_LEDGER_ROOT_INVALID")
    _validate_existing_ancestor_chain(
        lock_root,
        root=managed_root,
        code="E2E_ATTEMPT_LEDGER_ROOT_INVALID",
    )
    lock_identity = {
        "ledgerPath": _path_key(output_path),
        "admissionPath": _path_key(admission_path) if admission_path else "",
        "attemptKey": attempt_key or "",
    }
    lock_name = f"{_canonical_sha256(lock_identity)}.lock"
    lock_path = lock_root / lock_name
    try:
        existing = lock_path.lstat()
    except FileNotFoundError:
        existing = None
    except OSError as error:
        raise E2EFinalAdmissionError("E2E_ATTEMPT_LEDGER_LOCK_INVALID") from error
    if existing is not None and (_is_reparse_stat(existing) or not stat.S_ISREG(existing.st_mode)):
        raise E2EFinalAdmissionError("E2E_ATTEMPT_LEDGER_LOCK_INVALID")
    with lock_path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
            os.fsync(stream.fileno())
        stream.seek(0)
        acquired = False
        deadline = time.monotonic() + 30.0 if wait_for_lock else time.monotonic()
        while not acquired:
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError as error:
                if time.monotonic() >= deadline:
                    raise E2EFinalAdmissionError("E2E_ADMISSION_ISSUE_BUSY") from error
                time.sleep(0.02)
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
            and value.get("requirementCount") == 15
            and value.get("viewpointCount") == 10
            and value.get("routeCount") == 5
            and value.get("coverageCellCount") == 240
            and value.get("fixtureCount") == 60
            and HEX_64_RE.fullmatch(str(value.get("fixtureSetSha256") or ""))
            and HEX_64_RE.fullmatch(
                str(value.get("fixtureImplementationSetSha256") or "")
            )
            and HEX_64_RE.fullmatch(
                str(value.get("historicalCorpusSha256") or "")
            )
            and value.get("pairCaseCount") == 150
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
        "expectedParentAuthorityPath",
        "expectedRunnerArgumentsPath",
        "expectedRunnerArgumentsSha256",
        "expectedReservationReceiptPath",
        "expectedClaimReceiptPath",
        "expectedClaimWitnessPath",
        "runnerExecutablePath",
        "runnerExecutableSha256",
        "authorityPythonExecutablePath",
        "authorityPythonExecutableSha256",
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
        or value.get("canonicalProductId") != CANONICAL_PRODUCT_ID
        or not DATE_RE.fullmatch(str(value.get("issueDate") or ""))
        or value.get("attemptKey")
        != f"{value.get('canonicalProductId')}:{value.get('issueDate')}:scheduled-equivalent-nopublish"
        or value.get("commandSha256")
        != _canonical_sha256(value.get("runnerArguments"))
        or not isinstance(value.get("runnerArguments"), list)
        or not value.get("runnerArguments")
        or any(
            not isinstance(item, str) or not item
            for item in value.get("runnerArguments", [])
        )
        or value.get("evidenceSetSha256")
        != _canonical_sha256(value.get("evidenceBindings"))
        or value.get("admissionId")
        != _canonical_sha256(_admission_projection(value))
        or not isinstance(value.get("evidenceBindings"), list)
        or not Path(str(value.get("repoRoot") or "")).is_absolute()
        or not Path(str(value.get("runnerPath") or "")).is_absolute()
        or not Path(
            str(value.get("expectedParentAuthorityPath") or "")
        ).is_absolute()
        or not str(value.get("expectedParentAuthorityPath") or "").endswith(
            EXPECTED_PARENT_AUTHORITY_SUFFIX
        )
        or not Path(
            str(value.get("expectedRunnerArgumentsPath") or "")
        ).is_absolute()
        or not str(value.get("expectedRunnerArgumentsPath") or "").endswith(
            EXPECTED_RUNNER_ARGUMENTS_SUFFIX
        )
        or not Path(
            str(value.get("expectedReservationReceiptPath") or "")
        ).is_absolute()
        or not str(value.get("expectedReservationReceiptPath") or "").endswith(
            EXPECTED_RESERVATION_RECEIPT_SUFFIX
        )
        or not Path(
            str(value.get("expectedClaimReceiptPath") or "")
        ).is_absolute()
        or not str(value.get("expectedClaimReceiptPath") or "").endswith(
            EXPECTED_CLAIM_RECEIPT_SUFFIX
        )
        or not Path(
            str(value.get("expectedClaimWitnessPath") or "")
        ).is_absolute()
        or not str(value.get("expectedClaimWitnessPath") or "").endswith(
            EXPECTED_CLAIM_WITNESS_SUFFIX
        )
        or not HEX_64_RE.fullmatch(
            str(value.get("expectedRunnerArgumentsSha256") or "")
        )
        or not HEX_64_RE.fullmatch(str(value.get("runnerSha256") or ""))
        or not HEX_64_RE.fullmatch(str(value.get("runnerExecutableSha256") or ""))
        or not HEX_64_RE.fullmatch(
            str(value.get("authorityPythonExecutableSha256") or "")
        )
    ):
        raise E2EFinalAdmissionError("E2E_ADMISSION_IDENTITY_DRIFT")


def _admission_fields() -> set[str]:
    return {
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
        "expectedParentAuthorityPath",
        "expectedRunnerArgumentsPath",
        "expectedRunnerArgumentsSha256",
        "expectedReservationReceiptPath",
        "expectedClaimReceiptPath",
        "expectedClaimWitnessPath",
        "runnerExecutablePath",
        "runnerExecutableSha256",
        "authorityPythonExecutablePath",
        "authorityPythonExecutableSha256",
        "evidenceBindings",
        "evidenceSetSha256",
        "admissionId",
    }


RESERVATION_FIELDS = {
    "schemaVersion",
    "state",
    "attemptKey",
    "admissionId",
    "admissionPath",
    "admissionSha256",
    "parentAuthorityPath",
    "parentAuthoritySha256",
    "runnerArgumentsPath",
    "runnerArgumentsSha256",
    "runnerExecutablePath",
    "runnerExecutableSha256",
    "authorityPythonExecutablePath",
    "authorityPythonExecutableSha256",
    "runnerSha256",
    "commandSha256",
    "evidenceSetSha256",
    "reservationSha256",
    "receiptSha256",
}

CLAIM_FIELDS = RESERVATION_FIELDS | {
    "reservationReceiptPath",
    "reservationReceiptSha256",
    "claimNonce",
    "runnerPid",
    "ownerProcessIdentity",
    "claimSha256",
}


def _receipt_projection(value: dict[str, Any], *excluded: str) -> dict[str, Any]:
    excluded_set = {"receiptSha256", *excluded}
    return {key: item for key, item in value.items() if key not in excluded_set}


def _validate_receipt_hashes(value: dict[str, Any], *, seal_field: str) -> None:
    if value.get(seal_field) != _canonical_sha256(
        _receipt_projection(value, seal_field)
    ):
        raise E2EFinalAdmissionError("E2E_RECEIPT_IDENTITY_DRIFT")
    if value.get("receiptSha256") != _canonical_sha256(
        _receipt_projection(value)
    ):
        raise E2EFinalAdmissionError("E2E_RECEIPT_IDENTITY_DRIFT")


def _validate_reservation_receipt(value: dict[str, Any]) -> None:
    if set(value) != RESERVATION_FIELDS:
        raise E2EFinalAdmissionError("E2E_RESERVATION_RECEIPT_INVALID")
    if value.get("schemaVersion") != RESERVATION_SCHEMA or value.get(
        "state"
    ) != "runner_reserved":
        raise E2EFinalAdmissionError("E2E_RESERVATION_RECEIPT_INVALID")
    for field in (
        "admissionSha256",
        "parentAuthoritySha256",
        "runnerArgumentsSha256",
        "runnerExecutableSha256",
        "authorityPythonExecutableSha256",
        "runnerSha256",
        "commandSha256",
        "evidenceSetSha256",
        "reservationSha256",
        "receiptSha256",
    ):
        if HEX_64_RE.fullmatch(str(value.get(field) or "")) is None:
            raise E2EFinalAdmissionError("E2E_RESERVATION_RECEIPT_INVALID")
    for field in (
        "admissionPath",
        "parentAuthorityPath",
        "runnerArgumentsPath",
        "runnerExecutablePath",
        "authorityPythonExecutablePath",
    ):
        if not isinstance(value.get(field), str) or not Path(value[field]).is_absolute():
            raise E2EFinalAdmissionError("E2E_RESERVATION_RECEIPT_INVALID")
    _validate_receipt_hashes(value, seal_field="reservationSha256")


def _validate_claim_receipt(value: dict[str, Any]) -> None:
    if set(value) != CLAIM_FIELDS:
        raise E2EFinalAdmissionError("E2E_CLAIM_RECEIPT_INVALID")
    if value.get("schemaVersion") != CLAIM_SCHEMA or value.get("state") != "runner_claimed":
        raise E2EFinalAdmissionError("E2E_CLAIM_RECEIPT_INVALID")
    if (
        not isinstance(value.get("reservationReceiptPath"), str)
        or not Path(value["reservationReceiptPath"]).is_absolute()
        or not isinstance(value.get("admissionPath"), str)
        or not Path(value["admissionPath"]).is_absolute()
        or HEX_64_RE.fullmatch(str(value.get("reservationReceiptSha256") or "")) is None
        or HEX_64_RE.fullmatch(str(value.get("claimNonce") or "")) is None
        or type(value.get("runnerPid")) is not int
        or value.get("runnerPid", 0) <= 0
        or HEX_64_RE.fullmatch(str(value.get("claimSha256") or "")) is None
    ):
        raise E2EFinalAdmissionError("E2E_CLAIM_RECEIPT_INVALID")
    try:
        owner_identity = _validate_process_identity(
            value.get("ownerProcessIdentity"),
            expected_pid=value.get("runnerPid"),
        )
    except E2EFinalAdmissionError as error:
        raise E2EFinalAdmissionError("E2E_CLAIM_RECEIPT_INVALID") from error
    for field in (
        "admissionSha256",
        "parentAuthoritySha256",
        "runnerArgumentsSha256",
        "runnerExecutableSha256",
        "authorityPythonExecutableSha256",
        "runnerSha256",
        "commandSha256",
        "evidenceSetSha256",
        "reservationSha256",
        "receiptSha256",
    ):
        if HEX_64_RE.fullmatch(str(value.get(field) or "")) is None:
            raise E2EFinalAdmissionError("E2E_CLAIM_RECEIPT_INVALID")
    for field in (
        "parentAuthorityPath",
        "runnerArgumentsPath",
        "runnerExecutablePath",
        "authorityPythonExecutablePath",
        "reservationReceiptPath",
    ):
        if not Path(str(value.get(field) or "")).is_absolute():
            raise E2EFinalAdmissionError("E2E_CLAIM_RECEIPT_INVALID")
    expected_claim = _canonical_sha256(
        {
            "attemptKey": value["attemptKey"],
            "admissionId": value["admissionId"],
            "reservationReceiptSha256": value["reservationReceiptSha256"],
            "claimNonce": value["claimNonce"],
            "runnerPid": value["runnerPid"],
            "ownerProcessIdentity": owner_identity,
        }
    )
    if value["claimSha256"] != expected_claim:
        raise E2EFinalAdmissionError("E2E_CLAIM_RECEIPT_INVALID")
    if value.get("receiptSha256") != _canonical_sha256(
        _receipt_projection(value)
    ):
        raise E2EFinalAdmissionError("E2E_CLAIM_RECEIPT_INVALID")


def issue_admission(
    *,
    issue_date: str,
    canonical_product_id: str,
    repo_root: Path,
    runner_path: Path,
    runner_arguments: list[str],
    expected_parent_authority_path: Path,
    runner_arguments_path: Path,
    runner_executable_path: Path,
    authority_python_executable_path: Path,
    evidence_bindings: list[dict[str, str]],
    output_path: Path,
    expected_reservation_receipt_path: Path | None = None,
    expected_claim_receipt_path: Path | None = None,
    expected_claim_witness_path: Path | None = None,
) -> dict[str, Any]:
    """全上流証拠を実読込し、未消費のfinal admissionを発行する。"""

    if not DATE_RE.fullmatch(issue_date):
        raise E2EFinalAdmissionError("E2E_ISSUE_DATE_INVALID")
    if canonical_product_id != CANONICAL_PRODUCT_ID:
        raise E2EFinalAdmissionError("E2E_PRODUCT_ID_INVALID")
    try:
        repo = _canonical_directory(repo_root, code="E2E_RUNNER_INVALID")
        trusted_workspace = _trusted_workspace_root(repo)
        runner = _canonical_file(
            runner_path,
            code="E2E_RUNNER_INVALID",
            root=repo,
        )
        expected_parent_authority = _canonical_expected_parent_authority_path(
            expected_parent_authority_path,
            repo_root=repo,
        )
        runner_arguments_file = _canonical_expected_runner_arguments_path(
            runner_arguments_path,
            repo_root=repo,
        )
        output_name = Path(output_path).resolve().stem
        expected_reservation = _canonical_expected_receipt_path(
            expected_reservation_receipt_path
            or repo / f"{output_name}{EXPECTED_RESERVATION_RECEIPT_SUFFIX}",
            repo_root=repo,
            suffix=EXPECTED_RESERVATION_RECEIPT_SUFFIX,
            code="E2E_RESERVATION_RECEIPT_PATH_INVALID",
            exists_code="E2E_RESERVATION_RECEIPT_OUTPUT_EXISTS",
        )
        expected_claim = _canonical_expected_receipt_path(
            expected_claim_receipt_path
            or repo / f"{output_name}{EXPECTED_CLAIM_RECEIPT_SUFFIX}",
            repo_root=repo,
            suffix=EXPECTED_CLAIM_RECEIPT_SUFFIX,
            code="E2E_CLAIM_RECEIPT_PATH_INVALID",
            exists_code="E2E_CLAIM_RECEIPT_OUTPUT_EXISTS",
        )
        expected_witness = _canonical_expected_receipt_path(
            expected_claim_witness_path
            or repo / f"{output_name}{EXPECTED_CLAIM_WITNESS_SUFFIX}",
            repo_root=repo,
            suffix=EXPECTED_CLAIM_WITNESS_SUFFIX,
            code="E2E_CLAIM_WITNESS_PATH_INVALID",
            exists_code="E2E_CLAIM_WITNESS_OUTPUT_EXISTS",
        )
        runner_executable = _canonical_file(
            runner_executable_path,
            code="E2E_RUNNER_EXECUTABLE_INVALID",
        )
        authority_python_executable = _canonical_file(
            authority_python_executable_path,
            code="E2E_AUTHORITY_PYTHON_INVALID",
            trusted_root=trusted_workspace,
            max_bytes=MAX_EXECUTABLE_BYTES,
        )
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
    resolved_output = _canonical_future_output_path(
        output_path,
        repo_root=repo,
        code="E2E_ADMISSION_OUTPUT_PATH_INVALID",
        exists_code="E2E_ADMISSION_ALREADY_EXISTS",
    )
    if resolved_output in {
        expected_parent_authority,
        runner_arguments_file,
        expected_reservation,
        expected_claim,
        expected_witness,
    }:
        raise E2EFinalAdmissionError("E2E_EXPECTED_OUTPUT_PATH_INVALID")
    execution_path = _canonical_future_output_path(
        resolved_output.with_name(f"{resolved_output.stem}.red-suite-execution.json"),
        repo_root=repo,
        code="E2E_RED_SUITE_EXECUTION_PATH_INVALID",
        exists_code="E2E_ADMISSION_ALREADY_EXISTS",
    )
    with _issue_execution_lock(
        resolved_output,
        attempt_key=(
            f"{canonical_product_id}:{issue_date}:scheduled-equivalent-nopublish"
        ),
        wait_for_lock=False,
    ):
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
            "expectedParentAuthorityPath": str(expected_parent_authority),
            "expectedRunnerArgumentsPath": str(runner_arguments_file),
            "expectedRunnerArgumentsSha256": hashlib.sha256(
                _canonical_runner_arguments_bytes(runner_arguments)
            ).hexdigest(),
            "expectedReservationReceiptPath": str(expected_reservation),
            "expectedClaimReceiptPath": str(expected_claim),
            "expectedClaimWitnessPath": str(expected_witness),
            "runnerExecutablePath": str(runner_executable),
            "runnerExecutableSha256": _file_sha256(runner_executable),
            "authorityPythonExecutablePath": str(authority_python_executable),
            "authorityPythonExecutableSha256": _file_sha256(
                authority_python_executable
            ),
            "evidenceBindings": evidence,
            "evidenceSetSha256": _canonical_sha256(evidence),
        }
        value["admissionId"] = _canonical_sha256(value)
        _write_exclusive(resolved_output, value)
        return value


def _canonical_runner_arguments_bytes(arguments: list[str]) -> bytes:
    return (
        json.dumps(arguments, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _json_file_snapshot(
    path: Path,
    code: str,
    *,
    allow_missing: bool = False,
) -> tuple[dict[str, Any] | None, str | None]:
    candidate = Path(path).resolve()
    if allow_missing and not candidate.exists():
        return None, None
    max_bytes = MAX_ADMISSION_BYTES if "ADMISSION" in code else MAX_JSON_BYTES
    raw = _read_stable_bytes(candidate, max_bytes=max_bytes, code=code)
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise E2EFinalAdmissionError(code) from error
    if not isinstance(value, dict):
        raise E2EFinalAdmissionError(code)
    return value, hashlib.sha256(raw).hexdigest()


def _validate_attempt_ledger(value: dict[str, Any]) -> None:
    if (
        value.get("schemaVersion") != LEDGER_SCHEMA
        or not isinstance(value.get("attempts"), dict)
    ):
        raise E2EFinalAdmissionError("E2E_ATTEMPT_LEDGER_INVALID")


def _ledger_snapshot(path: Path) -> tuple[dict[str, Any], str | None]:
    value, digest = _json_file_snapshot(
        path,
        "E2E_ATTEMPT_LEDGER_INVALID",
        allow_missing=True,
    )
    if value is None:
        value = {"schemaVersion": LEDGER_SCHEMA, "attempts": {}}
    else:
        _validate_attempt_ledger(value)
    return value, digest


def _wal_path(
    ledger_path: Path,
    admission_path: Path,
    attempt_key: str,
    operation: str,
) -> Path:
    identity = _canonical_sha256(
        {
            "admissionPath": str(admission_path.resolve()),
            "attemptKey": attempt_key,
            "operation": operation,
        }
    )[:32]
    return ledger_path.with_name(f".{ledger_path.name}.{identity}.{operation}.wal.json")


def _validate_wal_shape(wal: dict[str, Any]) -> None:
    required = {
        "schemaVersion",
        "operation",
        "admissionPath",
        "ledgerPath",
        "admissionId",
        "attemptKey",
        "sourceAdmissionSha256",
        "sourceLedgerSha256",
        "targetLedgerSha256",
        "targetLedger",
        "ledgerRow",
        "targetReceiptPath",
        "targetReceiptSha256",
        "targetReceipt",
    }
    if set(wal) != required or wal.get("schemaVersion") != "NEWS_GRASP_E2E_ADMISSION_WAL_V1":
        raise E2EFinalAdmissionError("E2E_WAL_INVALID")
    if wal.get("operation") not in {"reserve", "claim"}:
        raise E2EFinalAdmissionError("E2E_WAL_INVALID")
    if not isinstance(wal.get("targetReceipt"), dict) or not isinstance(
        wal.get("targetLedger"), dict
    ) or not isinstance(wal.get("ledgerRow"), dict):
        raise E2EFinalAdmissionError("E2E_WAL_INVALID")
    _validate_attempt_ledger(wal["targetLedger"])
    if wal["operation"] == "reserve":
        _validate_reservation_receipt(wal["targetReceipt"])
    else:
        _validate_claim_receipt(wal["targetReceipt"])


def _validate_runtime_bindings(
    value: dict[str, Any],
    *,
    runner_arguments: list[str],
    parent_authority_path: Path | None,
    runner_arguments_path: Path,
    actual_runner_executable_path: Path,
    actual_authority_python_executable_path: Path,
    require_parent: bool = True,
) -> dict[str, Any]:
    """issued intentとmaterialize済みruntime bindingを同じbytesで再検証する。"""

    _validate_admission(value)
    try:
        repo_root = _canonical_directory(
            Path(str(value["repoRoot"])), code="E2E_ADMISSION_INVALID"
        )
        trusted_workspace = _trusted_workspace_root(repo_root)
        runner = _canonical_file(
            Path(str(value["runnerPath"])),
            code="E2E_RUNNER_INVALID",
            root=repo_root,
        )
        expected_parent = _canonical_expected_parent_authority_path(
            Path(str(value["expectedParentAuthorityPath"])),
            repo_root=repo_root,
            require_absent=False,
        )
        expected_args = _canonical_expected_runner_arguments_path(
            Path(str(value["expectedRunnerArgumentsPath"])),
            repo_root=repo_root,
            require_absent=False,
        )
        _canonical_expected_receipt_path(
            Path(str(value["expectedReservationReceiptPath"])),
            repo_root=repo_root,
            suffix=EXPECTED_RESERVATION_RECEIPT_SUFFIX,
            code="E2E_RESERVATION_RECEIPT_PATH_INVALID",
            exists_code="E2E_RESERVATION_RECEIPT_OUTPUT_EXISTS",
            require_absent=False,
        )
        _canonical_expected_receipt_path(
            Path(str(value["expectedClaimReceiptPath"])),
            repo_root=repo_root,
            suffix=EXPECTED_CLAIM_RECEIPT_SUFFIX,
            code="E2E_CLAIM_RECEIPT_PATH_INVALID",
            exists_code="E2E_CLAIM_RECEIPT_OUTPUT_EXISTS",
            require_absent=False,
        )
        _canonical_expected_receipt_path(
            Path(str(value["expectedClaimWitnessPath"])),
            repo_root=repo_root,
            suffix=EXPECTED_CLAIM_WITNESS_SUFFIX,
            code="E2E_CLAIM_WITNESS_PATH_INVALID",
            exists_code="E2E_CLAIM_WITNESS_OUTPUT_EXISTS",
            require_absent=False,
        )
        args_file = _canonical_file(
            runner_arguments_path,
            code="E2E_RUNNER_ARGUMENTS_INVALID",
            root=repo_root,
            max_bytes=MAX_RUNNER_ARGUMENTS_BYTES,
        )
        runner_executable = _canonical_file(
            actual_runner_executable_path,
            code="E2E_RUNNER_EXECUTABLE_INVALID",
        )
        authority_python = _canonical_file(
            actual_authority_python_executable_path,
            code="E2E_AUTHORITY_PYTHON_INVALID",
            trusted_root=trusted_workspace,
            max_bytes=MAX_EXECUTABLE_BYTES,
        )
        parent: Path | None = None
        if parent_authority_path is not None:
            parent = _canonical_file(
                parent_authority_path,
                code="E2E_PARENT_AUTHORITY_INVALID",
                root=repo_root,
                max_bytes=MAX_ADMISSION_BYTES,
            )
        elif require_parent:
            raise E2EFinalAdmissionError("E2E_PARENT_AUTHORITY_REQUIRED")
    except E2EFinalAdmissionError:
        raise
    except OSError as error:
        raise E2EFinalAdmissionError("E2E_ADMISSION_INVALID") from error
    if runner != Path(str(value["runnerPath"])).resolve():
        raise E2EFinalAdmissionError("E2E_RUNNER_IDENTITY_DRIFT")
    if parent is not None and parent != expected_parent:
        raise E2EFinalAdmissionError("E2E_PARENT_AUTHORITY_DRIFT")
    if args_file != expected_args:
        raise E2EFinalAdmissionError("E2E_RUNNER_ARGUMENTS_DRIFT")
    if runner_executable != Path(str(value["runnerExecutablePath"])).resolve():
        raise E2EFinalAdmissionError("E2E_RUNNER_EXECUTABLE_DRIFT")
    if authority_python != Path(
        str(value["authorityPythonExecutablePath"])
    ).resolve():
        raise E2EFinalAdmissionError("E2E_AUTHORITY_PYTHON_DRIFT")
    args_hash = _file_sha256(args_file, max_bytes=MAX_RUNNER_ARGUMENTS_BYTES)
    if args_hash != value["expectedRunnerArgumentsSha256"]:
        raise E2EFinalAdmissionError("E2E_RUNNER_ARGUMENTS_DRIFT")
    if _file_sha256(runner) != value["runnerSha256"]:
        raise E2EFinalAdmissionError("E2E_RUNNER_DRIFT")
    runner_executable_hash = _file_sha256(runner_executable)
    if runner_executable_hash != value["runnerExecutableSha256"]:
        raise E2EFinalAdmissionError("E2E_RUNNER_EXECUTABLE_DRIFT")
    authority_python_hash = _file_sha256(authority_python)
    if authority_python_hash != value["authorityPythonExecutableSha256"]:
        raise E2EFinalAdmissionError("E2E_AUTHORITY_PYTHON_DRIFT")
    if _read_runner_arguments(args_file) != runner_arguments:
        raise E2EFinalAdmissionError("E2E_COMMAND_DRIFT")
    if runner_arguments != value["runnerArguments"]:
        raise E2EFinalAdmissionError("E2E_COMMAND_DRIFT")
    if hashlib.sha256(_canonical_runner_arguments_bytes(runner_arguments)).hexdigest() != args_hash:
        raise E2EFinalAdmissionError("E2E_RUNNER_ARGUMENTS_DRIFT")
    parent_hash: str | None = None
    if parent is not None:
        parent_hash = _file_sha256(parent, max_bytes=MAX_ADMISSION_BYTES)
    normalized = _normalize_evidence(
        value["evidenceBindings"],
        repo_root=repo_root,
    )
    if normalized != value["evidenceBindings"]:
        raise E2EFinalAdmissionError("E2E_UPSTREAM_EVIDENCE_DRIFT")
    return {
        "repoRoot": repo_root,
        "parentAuthorityPath": str(parent) if parent is not None else str(expected_parent),
        "parentAuthoritySha256": parent_hash,
        "runnerArgumentsPath": str(args_file),
        "runnerArgumentsSha256": args_hash,
        "runnerExecutablePath": str(runner_executable),
        "runnerExecutableSha256": runner_executable_hash,
        "authorityPythonExecutablePath": str(authority_python),
        "authorityPythonExecutableSha256": authority_python_hash,
        "runnerPath": str(runner),
        "runnerSha256": value["runnerSha256"],
        "commandSha256": value["commandSha256"],
        "evidenceSetSha256": value["evidenceSetSha256"],
    }


def _validate_wal_identity(
    wal: dict[str, Any],
    *,
    operation: str,
    admission: Path,
    ledger: Path,
    value: dict[str, Any],
) -> None:
    _validate_wal_shape(wal)
    target_ledger = wal["targetLedger"]
    target_row = wal["ledgerRow"]
    if (
        wal["targetLedgerSha256"]
        != hashlib.sha256(_json_bytes(target_ledger)).hexdigest()
        or target_ledger.get("attempts", {}).get(wal["attemptKey"])
        != target_row
        or target_row.get("admissionId") != wal["admissionId"]
        or wal["targetReceiptSha256"]
        != hashlib.sha256(_json_bytes(wal["targetReceipt"])).hexdigest()
        or len(_json_bytes(wal["targetReceipt"])) > MAX_JSON_BYTES
        or len(_json_bytes(target_ledger)) > MAX_JSON_BYTES
    ):
        raise E2EFinalAdmissionError("E2E_WAL_INVALID")
    if wal["operation"] == "reserve":
        _validate_reservation_receipt(wal["targetReceipt"])
    else:
        _validate_claim_receipt(wal["targetReceipt"])
    if (
        wal["operation"] != operation
        or wal["admissionPath"] != str(admission)
        or wal["ledgerPath"] != str(ledger)
        or wal["admissionId"] != value.get("admissionId")
        or wal["attemptKey"] != value.get("attemptKey")
        or wal["targetReceipt"].get("admissionId") != value.get("admissionId")
        or wal["targetReceipt"].get("attemptKey") != value.get("attemptKey")
        or wal["targetReceiptPath"]
        != (
            value.get("expectedReservationReceiptPath")
            if operation == "reserve"
            else value.get("expectedClaimReceiptPath")
        )
        or not HEX_64_RE.fullmatch(str(wal["sourceAdmissionSha256"] or ""))
        or (
            wal["sourceLedgerSha256"] is not None
            and HEX_64_RE.fullmatch(str(wal["sourceLedgerSha256"] or ""))
            is None
        )
    ):
        raise E2EFinalAdmissionError("E2E_WAL_CROSS_LINEAGE")


def _recover_wal(
    *,
    wal_path: Path,
    operation: str,
    admission: Path,
    ledger: Path,
    value: dict[str, Any],
) -> dict[str, Any] | None:
    if not wal_path.exists():
        return None
    wal = _read_json(wal_path, "E2E_WAL_INVALID")
    _validate_wal_identity(
        wal,
        operation=operation,
        admission=admission,
        ledger=ledger,
        value=value,
    )
    _, current_admission_hash = _json_file_snapshot(
        admission, "E2E_ADMISSION_INVALID"
    )
    current_ledger, current_ledger_hash = _ledger_snapshot(ledger)
    receipt_path = Path(wal["targetReceiptPath"])
    current_receipt, current_receipt_hash = _json_file_snapshot(
        receipt_path,
        "E2E_RECEIPT_INVALID",
        allow_missing=True,
    )
    if current_admission_hash != wal["sourceAdmissionSha256"]:
        raise E2EFinalAdmissionError("E2E_WAL_DIVERGENCE")
    if current_ledger_hash not in {
        wal["sourceLedgerSha256"],
        wal["targetLedgerSha256"],
    }:
        raise E2EFinalAdmissionError("E2E_WAL_DIVERGENCE")
    if current_receipt_hash not in {None, wal["targetReceiptSha256"]}:
        raise E2EFinalAdmissionError("E2E_WAL_DIVERGENCE")
    if current_receipt_hash == wal["targetReceiptSha256"]:
        if current_receipt != wal["targetReceipt"]:
            raise E2EFinalAdmissionError("E2E_WAL_DIVERGENCE")
        if current_ledger_hash != wal["targetLedgerSha256"]:
            _replace_json(ledger, wal["targetLedger"])
    elif current_ledger_hash == wal["targetLedgerSha256"]:
        _replace_json(receipt_path, wal["targetReceipt"])
    else:
        _replace_json(ledger, wal["targetLedger"])
        _replace_json(receipt_path, wal["targetReceipt"])
    wal_path.unlink(missing_ok=True)
    recovered, _ = _json_file_snapshot(receipt_path, "E2E_RECEIPT_INVALID")
    if recovered is None:
        raise E2EFinalAdmissionError("E2E_WAL_DIVERGENCE")
    return recovered


def _apply_wal(
    *,
    wal_path: Path,
    admission: Path,
    ledger: Path,
    source_admission: dict[str, Any],
    source_admission_hash: str,
    source_ledger: dict[str, Any],
    source_ledger_hash: str | None,
    target_ledger: dict[str, Any],
    ledger_row: dict[str, Any],
    target_receipt_path: Path,
    target_receipt: dict[str, Any],
    operation: str,
) -> dict[str, Any]:
    wal = {
        "schemaVersion": "NEWS_GRASP_E2E_ADMISSION_WAL_V1",
        "operation": operation,
        "admissionPath": str(admission),
        "ledgerPath": str(ledger),
        "admissionId": source_admission["admissionId"],
        "attemptKey": source_admission["attemptKey"],
        "sourceAdmissionSha256": source_admission_hash,
        "sourceLedgerSha256": source_ledger_hash,
        "targetLedgerSha256": hashlib.sha256(_json_bytes(target_ledger)).hexdigest(),
        "targetLedger": target_ledger,
        "ledgerRow": ledger_row,
        "targetReceiptPath": str(target_receipt_path),
        "targetReceiptSha256": hashlib.sha256(_json_bytes(target_receipt)).hexdigest(),
        "targetReceipt": target_receipt,
    }
    _validate_wal_shape(wal)
    _write_exclusive(wal_path, wal)
    _replace_json(ledger, target_ledger)
    _replace_json(target_receipt_path, target_receipt)
    wal_path.unlink(missing_ok=True)
    return target_receipt


def validate_issued_admission(
    *,
    admission_path: Path,
    runner_arguments: list[str],
    runner_arguments_path: Path,
    parent_authority_path: Path | None = None,
    expected_parent_authority_path: Path | None = None,
    reservation_output: Path | None = None,
    claim_output: Path | None = None,
    claim_witness_output: Path | None = None,
    actual_runner_executable_path: Path | None = None,
    actual_authority_python_executable_path: Path | None = None,
    runner_executable_path: Path | None = None,
    authority_python_executable_path: Path | None = None,
) -> dict[str, Any]:
    """issued admissionをread-onlyで実行物・証拠ごと検証する。"""

    if actual_runner_executable_path is None:
        actual_runner_executable_path = runner_executable_path
    if actual_authority_python_executable_path is None:
        actual_authority_python_executable_path = authority_python_executable_path
    if actual_runner_executable_path is None or actual_authority_python_executable_path is None:
        raise E2EFinalAdmissionError("E2E_EXECUTABLE_IDENTITY_REQUIRED")
    if runner_executable_path is not None and Path(runner_executable_path).resolve() != Path(actual_runner_executable_path).resolve():
        raise E2EFinalAdmissionError("E2E_EXECUTABLE_IDENTITY_DRIFT")
    if authority_python_executable_path is not None and Path(authority_python_executable_path).resolve() != Path(actual_authority_python_executable_path).resolve():
        raise E2EFinalAdmissionError("E2E_EXECUTABLE_IDENTITY_DRIFT")
    admission = Path(admission_path).resolve(strict=True)
    value = _read_json(admission, "E2E_ADMISSION_INVALID")
    if value.get("state") != "issued":
        raise E2EFinalAdmissionError("E2E_ADMISSION_STATE_INVALID")
    repo_root = _canonical_directory(
        Path(str(value.get("repoRoot") or "")), code="E2E_ADMISSION_INVALID"
    )
    expected_parent = _canonical_expected_parent_authority_path(
        Path(str(value.get("expectedParentAuthorityPath") or "")),
        repo_root=repo_root,
        require_absent=False,
    )
    caller_parent = expected_parent_authority_path or parent_authority_path
    if caller_parent is None:
        raise E2EFinalAdmissionError("E2E_PARENT_AUTHORITY_REQUIRED")
    caller_parent_candidate = Path(os.path.abspath(os.fspath(caller_parent)))
    if _path_key(caller_parent_candidate) != _path_key(expected_parent):
        raise E2EFinalAdmissionError("E2E_PARENT_AUTHORITY_DRIFT")

    if parent_authority_path is not None and expected_parent_authority_path is not None:
        if _path_key(parent_authority_path) != _path_key(expected_parent_authority_path):
            raise E2EFinalAdmissionError("E2E_PARENT_AUTHORITY_DRIFT")

    if reservation_output is None:
        raise E2EFinalAdmissionError("E2E_RESERVATION_RECEIPT_PATH_REQUIRED")
    if claim_output is None:
        raise E2EFinalAdmissionError("E2E_CLAIM_RECEIPT_PATH_REQUIRED")
    if claim_witness_output is None:
        raise E2EFinalAdmissionError("E2E_CLAIM_WITNESS_PATH_REQUIRED")
    expected_reservation = _canonical_expected_receipt_path(
        Path(str(value.get("expectedReservationReceiptPath") or "")),
        repo_root=repo_root,
        suffix=EXPECTED_RESERVATION_RECEIPT_SUFFIX,
        code="E2E_RESERVATION_RECEIPT_PATH_INVALID",
        exists_code="E2E_RESERVATION_RECEIPT_OUTPUT_EXISTS",
        require_absent=False,
    )
    expected_claim = _canonical_expected_receipt_path(
        Path(str(value.get("expectedClaimReceiptPath") or "")),
        repo_root=repo_root,
        suffix=EXPECTED_CLAIM_RECEIPT_SUFFIX,
        code="E2E_CLAIM_RECEIPT_PATH_INVALID",
        exists_code="E2E_CLAIM_RECEIPT_OUTPUT_EXISTS",
        require_absent=False,
    )
    expected_witness = _canonical_expected_receipt_path(
        Path(str(value.get("expectedClaimWitnessPath") or "")),
        repo_root=repo_root,
        suffix=EXPECTED_CLAIM_WITNESS_SUFFIX,
        code="E2E_CLAIM_WITNESS_PATH_INVALID",
        exists_code="E2E_CLAIM_WITNESS_OUTPUT_EXISTS",
        require_absent=False,
    )
    reservation_candidate = Path(os.path.abspath(os.fspath(reservation_output)))
    claim_candidate = Path(os.path.abspath(os.fspath(claim_output)))
    witness_candidate = Path(os.path.abspath(os.fspath(claim_witness_output)))
    if _path_key(reservation_candidate) != _path_key(expected_reservation):
        raise E2EFinalAdmissionError("E2E_RESERVATION_RECEIPT_PATH_DRIFT")
    if _path_key(claim_candidate) != _path_key(expected_claim):
        raise E2EFinalAdmissionError("E2E_CLAIM_RECEIPT_PATH_DRIFT")
    if _path_key(witness_candidate) != _path_key(expected_witness):
        raise E2EFinalAdmissionError("E2E_CLAIM_WITNESS_PATH_DRIFT")
    _validate_runtime_bindings(
        value,
        runner_arguments=runner_arguments,
        parent_authority_path=(
            caller_parent_candidate if caller_parent_candidate.exists() else None
        ),
        runner_arguments_path=runner_arguments_path,
        actual_runner_executable_path=actual_runner_executable_path,
        actual_authority_python_executable_path=actual_authority_python_executable_path,
        require_parent=False,
    )
    return value

def _resolve_executable_aliases(
    *,
    actual_runner_executable_path: Path | None,
    actual_authority_python_executable_path: Path | None,
    runner_executable_path: Path | None,
    authority_python_executable_path: Path | None,
) -> tuple[Path, Path]:
    actual_runner = actual_runner_executable_path or runner_executable_path
    actual_python = (
        actual_authority_python_executable_path
        or authority_python_executable_path
    )
    if actual_runner is None or actual_python is None:
        raise E2EFinalAdmissionError("E2E_EXECUTABLE_IDENTITY_REQUIRED")
    if (
        runner_executable_path is not None
        and Path(runner_executable_path).resolve() != Path(actual_runner).resolve()
    ):
        raise E2EFinalAdmissionError("E2E_EXECUTABLE_IDENTITY_DRIFT")
    if (
        authority_python_executable_path is not None
        and Path(authority_python_executable_path).resolve()
        != Path(actual_python).resolve()
    ):
        raise E2EFinalAdmissionError("E2E_EXECUTABLE_IDENTITY_DRIFT")
    return Path(actual_runner), Path(actual_python)


def _exact_expected_output(
    requested: Path | None,
    *,
    value: dict[str, Any],
    field: str,
    repo_root: Path,
    suffix: str,
    code: str,
    exists_code: str,
) -> Path:
    expected = Path(os.path.abspath(str(value[field])))
    candidate = (
        expected
        if requested is None
        else Path(os.path.abspath(os.fspath(requested)))
    )
    if candidate != expected:
        raise E2EFinalAdmissionError(code)
    return _canonical_expected_receipt_path(
        candidate,
        repo_root=repo_root,
        suffix=suffix,
        code=code,
        exists_code=exists_code,
        require_absent=False,
    )


def _reservation_receipt(
    *,
    admission: Path,
    admission_hash: str,
    source: dict[str, Any],
    bindings: dict[str, Any],
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schemaVersion": RESERVATION_SCHEMA,
        "state": "runner_reserved",
        "attemptKey": source["attemptKey"],
        "admissionId": source["admissionId"],
        "admissionPath": str(admission),
        "admissionSha256": admission_hash,
        "parentAuthorityPath": bindings["parentAuthorityPath"],
        "parentAuthoritySha256": bindings["parentAuthoritySha256"],
        "runnerArgumentsPath": bindings["runnerArgumentsPath"],
        "runnerArgumentsSha256": bindings["runnerArgumentsSha256"],
        "runnerExecutablePath": bindings["runnerExecutablePath"],
        "runnerExecutableSha256": bindings["runnerExecutableSha256"],
        "authorityPythonExecutablePath": bindings[
            "authorityPythonExecutablePath"
        ],
        "authorityPythonExecutableSha256": bindings[
            "authorityPythonExecutableSha256"
        ],
        "runnerSha256": bindings["runnerSha256"],
        "commandSha256": bindings["commandSha256"],
        "evidenceSetSha256": bindings["evidenceSetSha256"],
        "reservationSha256": "",
        "receiptSha256": "",
    }
    value["reservationSha256"] = _canonical_sha256(
        _receipt_projection(value, "reservationSha256")
    )
    value["receiptSha256"] = _canonical_sha256(_receipt_projection(value))
    _validate_reservation_receipt(value)
    return value


def _reservation_row(receipt: dict[str, Any], receipt_path: Path) -> dict[str, Any]:
    row = {
        key: item
        for key, item in receipt.items()
        if key not in {"schemaVersion", "receiptSha256"}
    }
    row.update(
        {
            "reservationReceiptPath": str(receipt_path),
            "reservationReceiptSha256": receipt["receiptSha256"],
        }
    )
    return row


def _same_reservation_lineage(
    row: object,
    *,
    source: dict[str, Any],
    admission: Path,
    receipt_path: Path,
    bindings: dict[str, Any],
) -> bool:
    if not isinstance(row, dict):
        return False
    expected = {
        "admissionId": source["admissionId"],
        "admissionPath": str(admission),
        "parentAuthorityPath": bindings["parentAuthorityPath"],
        "parentAuthoritySha256": bindings["parentAuthoritySha256"],
        "runnerArgumentsPath": bindings["runnerArgumentsPath"],
        "runnerArgumentsSha256": bindings["runnerArgumentsSha256"],
        "runnerExecutablePath": bindings["runnerExecutablePath"],
        "runnerExecutableSha256": bindings["runnerExecutableSha256"],
        "authorityPythonExecutablePath": bindings[
            "authorityPythonExecutablePath"
        ],
        "authorityPythonExecutableSha256": bindings[
            "authorityPythonExecutableSha256"
        ],
        "runnerSha256": bindings["runnerSha256"],
        "commandSha256": bindings["commandSha256"],
        "evidenceSetSha256": bindings["evidenceSetSha256"],
        "reservationReceiptPath": str(receipt_path),
    }
    return all(row.get(key) == item for key, item in expected.items())


def _claim_receipt(
    *,
    reservation: dict[str, Any],
    reservation_path: Path,
    claim_nonce: str,
    runner_pid: int,
    owner_process_identity: dict[str, Any],
) -> dict[str, Any]:
    value = dict(reservation)
    value.update(
        {
            "schemaVersion": CLAIM_SCHEMA,
            "state": "runner_claimed",
            "reservationReceiptPath": str(reservation_path),
            "reservationReceiptSha256": reservation["receiptSha256"],
            "claimNonce": claim_nonce,
            "runnerPid": runner_pid,
            "ownerProcessIdentity": owner_process_identity,
            "claimSha256": "",
            "receiptSha256": "",
        }
    )
    value["claimSha256"] = _canonical_sha256(
        {
            "attemptKey": value["attemptKey"],
            "admissionId": value["admissionId"],
            "reservationReceiptSha256": value["reservationReceiptSha256"],
            "claimNonce": claim_nonce,
            "runnerPid": runner_pid,
            "ownerProcessIdentity": owner_process_identity,
        }
    )
    value["receiptSha256"] = _canonical_sha256(_receipt_projection(value))
    _validate_claim_receipt(value)
    return value


def _claim_row(
    reservation_row: dict[str, Any],
    receipt: dict[str, Any],
    receipt_path: Path,
) -> dict[str, Any]:
    row = dict(reservation_row)
    row.update(
        {
            "state": "runner_claimed",
            "claimReceiptPath": str(receipt_path),
            "claimReceiptSha256": receipt["receiptSha256"],
            "claimNonce": receipt["claimNonce"],
            "runnerPid": receipt["runnerPid"],
            "ownerProcessIdentity": receipt["ownerProcessIdentity"],
            "claimSha256": receipt["claimSha256"],
        }
    )
    return row


def _immutable_consume_admission(
    *,
    admission_path: Path,
    ledger_path: Path,
    runner_arguments: list[str],
    parent_authority_path: Path | None,
    runner_arguments_path: Path,
    reservation_output: Path | None,
    actual_runner_executable_path: Path | None,
    actual_authority_python_executable_path: Path | None,
    runner_executable_path: Path | None,
    authority_python_executable_path: Path | None,
) -> dict[str, Any]:
    actual_runner, actual_python = _resolve_executable_aliases(
        actual_runner_executable_path=actual_runner_executable_path,
        actual_authority_python_executable_path=actual_authority_python_executable_path,
        runner_executable_path=runner_executable_path,
        authority_python_executable_path=authority_python_executable_path,
    )
    admission = Path(admission_path).resolve(strict=True)
    ledger = Path(ledger_path).resolve()
    lock_source = _read_json(admission, "E2E_ADMISSION_INVALID")
    lock_attempt_key = str(lock_source.get("attemptKey") or "")
    with _issue_execution_lock(
        ledger,
        admission_path=admission,
        attempt_key=lock_attempt_key,
    ):
        source, source_hash = _json_file_snapshot(admission, "E2E_ADMISSION_INVALID")
        if source is None or source_hash is None:
            raise E2EFinalAdmissionError("E2E_ADMISSION_INVALID")
        _validate_admission(source)
        if source.get("attemptKey") != lock_attempt_key:
            raise E2EFinalAdmissionError("E2E_WAL_CROSS_LINEAGE")
        repo_root = _canonical_directory(
            Path(source["repoRoot"]), code="E2E_ADMISSION_INVALID"
        )
        reservation_path = _exact_expected_output(
            reservation_output,
            value=source,
            field="expectedReservationReceiptPath",
            repo_root=repo_root,
            suffix=EXPECTED_RESERVATION_RECEIPT_SUFFIX,
            code="E2E_RESERVATION_RECEIPT_PATH_DRIFT",
            exists_code="E2E_RESERVATION_RECEIPT_OUTPUT_EXISTS",
        )
        wal_path = _wal_path(ledger, admission, source["attemptKey"], "reserve")
        recovered = _recover_wal(
            wal_path=wal_path,
            operation="reserve",
            admission=admission,
            ledger=ledger,
            value=source,
        )
        if recovered is not None:
            return recovered
        if source.get("state") != "issued":
            raise E2EFinalAdmissionError("E2E_ADMISSION_STATE_INVALID")
        bindings = _validate_runtime_bindings(
            source,
            runner_arguments=runner_arguments,
            parent_authority_path=parent_authority_path,
            runner_arguments_path=runner_arguments_path,
            actual_runner_executable_path=actual_runner,
            actual_authority_python_executable_path=actual_python,
            require_parent=True,
        )
        ledger_value, ledger_hash = _ledger_snapshot(ledger)
        attempt_key = source["attemptKey"]
        existing = ledger_value["attempts"].get(attempt_key)
        if existing is not None:
            replay_lineage = _same_reservation_lineage(
                existing,
                source=source,
                admission=admission,
                receipt_path=reservation_path,
                bindings=bindings,
            ) and existing.get("state") in {"runner_reserved", "runner_claimed"}
            if replay_lineage:
                existing_receipt, _ = _json_file_snapshot(
                    reservation_path,
                    "E2E_RESERVATION_RECEIPT_INVALID",
                    allow_missing=True,
                )
                if existing_receipt is not None:
                    try:
                        _validate_reservation_receipt(existing_receipt)
                    except E2EFinalAdmissionError:
                        pass
                    else:
                        if (
                            existing_receipt.get("receiptSha256")
                            == existing.get("reservationReceiptSha256")
                        ):
                            raise E2EFinalAdmissionError(
                                "E2E_FINAL_ATTEMPT_ALREADY_CONSUMED"
                            )
            raise E2EFinalAdmissionError("E2E_WAL_CROSS_LINEAGE")
        if reservation_path.exists():
            raise E2EFinalAdmissionError("E2E_WAL_CROSS_LINEAGE")
        receipt = _reservation_receipt(
            admission=admission,
            admission_hash=source_hash,
            source=source,
            bindings=bindings,
        )
        row = _reservation_row(receipt, reservation_path)
        target_ledger = {
            "schemaVersion": LEDGER_SCHEMA,
            "attempts": dict(ledger_value["attempts"]),
        }
        target_ledger["attempts"][attempt_key] = row
        return _apply_wal(
            wal_path=wal_path,
            admission=admission,
            ledger=ledger,
            source_admission=source,
            source_admission_hash=source_hash,
            source_ledger=ledger_value,
            source_ledger_hash=ledger_hash,
            target_ledger=target_ledger,
            ledger_row=row,
            target_receipt_path=reservation_path,
            target_receipt=receipt,
            operation="reserve",
        )


def consume_admission(
    *,
    admission_path: Path,
    ledger_path: Path,
    runner_arguments: list[str],
    runner_arguments_path: Path,
    parent_authority_path: Path | None = None,
    reservation_output: Path | None = None,
    actual_runner_executable_path: Path | None = None,
    actual_authority_python_executable_path: Path | None = None,
    runner_executable_path: Path | None = None,
    authority_python_executable_path: Path | None = None,
) -> dict[str, Any]:
    """immutable issued intentからappend-only reservation receiptだけを発行する。"""

    return _immutable_consume_admission(
        admission_path=admission_path,
        ledger_path=ledger_path,
        runner_arguments=runner_arguments,
        parent_authority_path=parent_authority_path,
        runner_arguments_path=runner_arguments_path,
        reservation_output=reservation_output,
        actual_runner_executable_path=actual_runner_executable_path,
        actual_authority_python_executable_path=actual_authority_python_executable_path,
        runner_executable_path=runner_executable_path,
        authority_python_executable_path=authority_python_executable_path,
    )


def claim_runner(
    *,
    admission_path: Path,
    ledger_path: Path,
    runner_arguments: list[str],
    runner_arguments_path: Path,
    parent_authority_path: Path | None = None,
    reservation_receipt: Path | None = None,
    claim_output: Path | None = None,
    actual_runner_executable_path: Path | None = None,
    actual_authority_python_executable_path: Path | None = None,
    runner_executable_path: Path | None = None,
    authority_python_executable_path: Path | None = None,
    current_runner_pid: int,
    claim_nonce: str,
) -> dict[str, Any]:
    """immutable issued intentとreservation receiptからclaimを一度だけ発行する。"""

    actual_runner, actual_python = _resolve_executable_aliases(
        actual_runner_executable_path=actual_runner_executable_path,
        actual_authority_python_executable_path=actual_authority_python_executable_path,
        runner_executable_path=runner_executable_path,
        authority_python_executable_path=authority_python_executable_path,
    )
    if (
        type(current_runner_pid) is not int
        or current_runner_pid <= 0
        or HEX_64_RE.fullmatch(claim_nonce or "") is None
    ):
        raise E2EFinalAdmissionError("E2E_RUNNER_CLAIM_INVALID")
    owner_process_identity = _validate_process_identity(
        _query_process_identity(current_runner_pid),
        expected_pid=current_runner_pid,
    )
    admission = Path(admission_path).resolve(strict=True)
    ledger = Path(ledger_path).resolve()
    with _issue_execution_lock(
        ledger,
        admission_path=admission,
        attempt_key=str(_read_json(admission, "E2E_ADMISSION_INVALID").get("attemptKey") or ""),
    ):
        source, source_hash = _json_file_snapshot(admission, "E2E_ADMISSION_INVALID")
        if source is None or source_hash is None:
            raise E2EFinalAdmissionError("E2E_ADMISSION_INVALID")
        _validate_admission(source)
        if source.get("state") != "issued":
            raise E2EFinalAdmissionError("E2E_ADMISSION_STATE_INVALID")
        repo_root = _canonical_directory(
            Path(source["repoRoot"]), code="E2E_ADMISSION_INVALID"
        )
        reservation_path = _exact_expected_output(
            reservation_receipt,
            value=source,
            field="expectedReservationReceiptPath",
            repo_root=repo_root,
            suffix=EXPECTED_RESERVATION_RECEIPT_SUFFIX,
            code="E2E_RESERVATION_RECEIPT_PATH_DRIFT",
            exists_code="E2E_RESERVATION_RECEIPT_OUTPUT_EXISTS",
        )
        claim_path = _exact_expected_output(
            claim_output,
            value=source,
            field="expectedClaimReceiptPath",
            repo_root=repo_root,
            suffix=EXPECTED_CLAIM_RECEIPT_SUFFIX,
            code="E2E_CLAIM_RECEIPT_PATH_DRIFT",
            exists_code="E2E_CLAIM_RECEIPT_OUTPUT_EXISTS",
        )
        wal_path = _wal_path(ledger, admission, source["attemptKey"], "claim")
        recovered = _recover_wal(
            wal_path=wal_path,
            operation="claim",
            admission=admission,
            ledger=ledger,
            value=source,
        )
        if recovered is not None:
            return recovered
        bindings = _validate_runtime_bindings(
            source,
            runner_arguments=runner_arguments,
            parent_authority_path=parent_authority_path,
            runner_arguments_path=runner_arguments_path,
            actual_runner_executable_path=actual_runner,
            actual_authority_python_executable_path=actual_python,
            require_parent=True,
        )
        reservation, reservation_hash = _json_file_snapshot(
            reservation_path,
            "E2E_RESERVATION_RECEIPT_INVALID",
            allow_missing=True,
        )
        if reservation is None or reservation_hash is None:
            raise E2EFinalAdmissionError("E2E_RESERVATION_RECEIPT_REQUIRED")
        _validate_reservation_receipt(reservation)
        if (
            reservation["admissionId"] != source["admissionId"]
            or reservation["admissionPath"] != str(admission)
            or reservation["admissionSha256"] != source_hash
            or reservation["parentAuthorityPath"] != bindings["parentAuthorityPath"]
            or reservation["parentAuthoritySha256"]
            != bindings["parentAuthoritySha256"]
            or reservation["runnerArgumentsPath"] != bindings["runnerArgumentsPath"]
            or reservation["runnerArgumentsSha256"]
            != bindings["runnerArgumentsSha256"]
            or reservation["runnerExecutablePath"]
            != bindings["runnerExecutablePath"]
            or reservation["runnerExecutableSha256"]
            != bindings["runnerExecutableSha256"]
            or reservation["authorityPythonExecutablePath"]
            != bindings["authorityPythonExecutablePath"]
            or reservation["authorityPythonExecutableSha256"]
            != bindings["authorityPythonExecutableSha256"]
        ):
            raise E2EFinalAdmissionError("E2E_WAL_CROSS_LINEAGE")
        ledger_value, ledger_hash = _ledger_snapshot(ledger)
        attempt_key = source["attemptKey"]
        expected_reserved_row = _reservation_row(reservation, reservation_path)
        row = ledger_value["attempts"].get(attempt_key)
        if row != expected_reserved_row:
            if isinstance(row, dict) and _same_reservation_lineage(
                row,
                source=source,
                admission=admission,
                receipt_path=reservation_path,
                bindings=bindings,
            ) and row.get("state") == "runner_claimed":
                claim_receipt_path = Path(
                    str(row.get("claimReceiptPath") or "")
                )
                existing_claim, _ = _json_file_snapshot(
                    claim_receipt_path,
                    "E2E_CLAIM_RECEIPT_INVALID",
                    allow_missing=True,
                )
                if existing_claim is not None:
                    try:
                        _validate_claim_receipt(existing_claim)
                    except E2EFinalAdmissionError:
                        pass
                    else:
                        if (
                            existing_claim.get("receiptSha256")
                            == row.get("claimReceiptSha256")
                        ):
                            raise E2EFinalAdmissionError(
                                "E2E_RUNNER_CLAIM_REPLAY"
                            )
            raise E2EFinalAdmissionError("E2E_WAL_CROSS_LINEAGE")
        if claim_path.exists():
            raise E2EFinalAdmissionError("E2E_WAL_CROSS_LINEAGE")
        claim = _claim_receipt(
            reservation=reservation,
            reservation_path=reservation_path,
            claim_nonce=claim_nonce,
            runner_pid=current_runner_pid,
            owner_process_identity=owner_process_identity,
        )
        target_row = _claim_row(row, claim, claim_path)
        target_ledger = {
            "schemaVersion": LEDGER_SCHEMA,
            "attempts": dict(ledger_value["attempts"]),
        }
        target_ledger["attempts"][attempt_key] = target_row
        return _apply_wal(
            wal_path=wal_path,
            admission=admission,
            ledger=ledger,
            source_admission=source,
            source_admission_hash=source_hash,
            source_ledger=ledger_value,
            source_ledger_hash=ledger_hash,
            target_ledger=target_ledger,
            ledger_row=target_row,
            target_receipt_path=claim_path,
            target_receipt=claim,
            operation="claim",
        )


def validate_runner_claim(
    *,
    admission_path: Path,
    ledger_path: Path,
    runner_arguments: list[str],
    parent_authority_path: Path,
    runner_arguments_path: Path,
    reservation_receipt: Path,
    claim_receipt: Path,
    actual_runner_executable_path: Path | None = None,
    actual_authority_python_executable_path: Path | None = None,
    runner_executable_path: Path | None = None,
    authority_python_executable_path: Path | None = None,
    expected_owner_pid: int,
) -> dict[str, Any]:
    """claim receiptとOS process identityをread-onlyで再束縛する。"""

    actual_runner, actual_python = _resolve_executable_aliases(
        actual_runner_executable_path=actual_runner_executable_path,
        actual_authority_python_executable_path=actual_authority_python_executable_path,
        runner_executable_path=runner_executable_path,
        authority_python_executable_path=authority_python_executable_path,
    )
    if type(expected_owner_pid) is not int or expected_owner_pid <= 0:
        raise E2EFinalAdmissionError("E2E_RUNNER_PROCESS_IDENTITY_INVALID")
    observed_identity = _validate_process_identity(
        _query_process_identity(expected_owner_pid),
        expected_pid=expected_owner_pid,
    )
    admission = Path(admission_path).resolve(strict=True)
    ledger = Path(ledger_path).resolve()
    lock_source = _read_json(admission, "E2E_ADMISSION_INVALID")
    lock_attempt_key = str(lock_source.get("attemptKey") or "")
    with _issue_execution_lock(
        ledger,
        admission_path=admission,
        attempt_key=lock_attempt_key,
    ):
        source, source_hash = _json_file_snapshot(admission, "E2E_ADMISSION_INVALID")
        if source is None or source_hash is None:
            raise E2EFinalAdmissionError("E2E_ADMISSION_INVALID")
        _validate_admission(source)
        if source.get("attemptKey") != lock_attempt_key:
            raise E2EFinalAdmissionError("E2E_WAL_CROSS_LINEAGE")
        if source.get("state") != "issued":
            raise E2EFinalAdmissionError("E2E_ADMISSION_STATE_INVALID")
        repo_root = _canonical_directory(
            Path(source["repoRoot"]), code="E2E_ADMISSION_INVALID"
        )
        reservation_path = _exact_expected_output(
            reservation_receipt,
            value=source,
            field="expectedReservationReceiptPath",
            repo_root=repo_root,
            suffix=EXPECTED_RESERVATION_RECEIPT_SUFFIX,
            code="E2E_RESERVATION_RECEIPT_PATH_DRIFT",
            exists_code="E2E_RESERVATION_RECEIPT_OUTPUT_EXISTS",
        )
        claim_path = _exact_expected_output(
            claim_receipt,
            value=source,
            field="expectedClaimReceiptPath",
            repo_root=repo_root,
            suffix=EXPECTED_CLAIM_RECEIPT_SUFFIX,
            code="E2E_CLAIM_RECEIPT_PATH_DRIFT",
            exists_code="E2E_CLAIM_RECEIPT_OUTPUT_EXISTS",
        )
        bindings = _validate_runtime_bindings(
            source,
            runner_arguments=runner_arguments,
            parent_authority_path=parent_authority_path,
            runner_arguments_path=runner_arguments_path,
            actual_runner_executable_path=actual_runner,
            actual_authority_python_executable_path=actual_python,
            require_parent=True,
        )
        reservation, reservation_hash = _json_file_snapshot(
            reservation_path,
            "E2E_RESERVATION_RECEIPT_INVALID",
        )
        claim, claim_hash = _json_file_snapshot(
            claim_path,
            "E2E_CLAIM_RECEIPT_INVALID",
        )
        if reservation is None or reservation_hash is None:
            raise E2EFinalAdmissionError("E2E_RESERVATION_RECEIPT_REQUIRED")
        if claim is None or claim_hash is None:
            raise E2EFinalAdmissionError("E2E_CLAIM_RECEIPT_REQUIRED")
        _validate_reservation_receipt(reservation)
        _validate_claim_receipt(claim)
        if (
            reservation["admissionId"] != source["admissionId"]
            or reservation["admissionPath"] != str(admission)
            or reservation["admissionSha256"] != source_hash
            or reservation["parentAuthorityPath"] != bindings["parentAuthorityPath"]
            or reservation["parentAuthoritySha256"] != bindings["parentAuthoritySha256"]
            or reservation["runnerArgumentsPath"] != bindings["runnerArgumentsPath"]
            or reservation["runnerArgumentsSha256"] != bindings["runnerArgumentsSha256"]
            or reservation["runnerExecutablePath"] != bindings["runnerExecutablePath"]
            or reservation["runnerExecutableSha256"] != bindings["runnerExecutableSha256"]
            or reservation["authorityPythonExecutablePath"]
            != bindings["authorityPythonExecutablePath"]
            or reservation["authorityPythonExecutableSha256"]
            != bindings["authorityPythonExecutableSha256"]
        ):
            raise E2EFinalAdmissionError("E2E_WAL_CROSS_LINEAGE")
        if (
            claim["reservationReceiptPath"] != str(reservation_path)
            or claim["reservationReceiptSha256"] != reservation["receiptSha256"]
            or claim["admissionId"] != source["admissionId"]
            or claim["admissionSha256"] != source_hash
            or claim["ownerProcessIdentity"] != observed_identity
            or _path_key(Path(observed_identity["imagePath"]))
            != _path_key(Path(bindings["runnerExecutablePath"]))
            or observed_identity["imageSha256"]
            != bindings["runnerExecutableSha256"]
        ):
            raise E2EFinalAdmissionError("E2E_RUNNER_PROCESS_IDENTITY_DRIFT")
        ledger_value, _ = _ledger_snapshot(ledger)
        row = ledger_value.get("attempts", {}).get(source["attemptKey"])
        if (
            not isinstance(row, dict)
            or row.get("state") != "runner_claimed"
            or row.get("claimReceiptPath") != str(claim_path)
            or row.get("claimReceiptSha256") != claim["receiptSha256"]
            or row.get("ownerProcessIdentity") != observed_identity
            or row.get("admissionId") != source["admissionId"]
        ):
            raise E2EFinalAdmissionError("E2E_WAL_CROSS_LINEAGE")
        return {
            "schemaVersion": CLAIM_WITNESS_SCHEMA,
            "claimId": claim["claimSha256"],
            "claimReceiptPath": str(claim_path),
            "claimReceiptSha256": claim["receiptSha256"],
            "ownerProcessIdentity": observed_identity,
            "attemptKey": source["attemptKey"],
            "admissionId": source["admissionId"],
            "admissionPath": str(admission),
            "runnerArgumentsPath": bindings["runnerArgumentsPath"],
            "reservationReceiptPath": str(reservation_path),
            "reservationReceiptSha256": reservation["receiptSha256"],
        }


def _claim_witness_path(
    source: dict[str, Any], *, admission_path: Path, require_absent: bool
) -> Path:
    repo_root = _canonical_directory(
        Path(str(source["repoRoot"])), code="E2E_ADMISSION_INVALID"
    )
    candidate = _canonical_expected_receipt_path(
        Path(str(source["expectedClaimWitnessPath"])),
        repo_root=repo_root,
        suffix=EXPECTED_CLAIM_WITNESS_SUFFIX,
        code="E2E_CLAIM_WITNESS_PATH_INVALID",
        exists_code="E2E_CLAIM_WITNESS_OUTPUT_EXISTS",
        require_absent=require_absent,
    )
    if candidate == admission_path:
        raise E2EFinalAdmissionError("E2E_CLAIM_WITNESS_PATH_INVALID")
    return candidate


def write_runner_claim_witness(
    *,
    admission_path: Path,
    ledger_path: Path,
    runner_arguments: list[str],
    parent_authority_path: Path,
    runner_arguments_path: Path,
    reservation_receipt: Path,
    claim_receipt: Path,
    witness_output: Path,
    actual_runner_executable_path: Path,
    actual_authority_python_executable_path: Path,
    expected_owner_pid: int,
) -> dict[str, Any]:
    """runner claimのcanonical witnessを一度だけ物理化する。"""

    admission = Path(admission_path).resolve(strict=True)
    source = _read_json(admission, "E2E_ADMISSION_INVALID")
    _validate_admission(source)
    expected = _claim_witness_path(
        source, admission_path=admission, require_absent=True
    )
    requested = Path(os.path.abspath(os.fspath(witness_output)))
    if _path_key(requested) != _path_key(expected):
        raise E2EFinalAdmissionError("E2E_CLAIM_WITNESS_PATH_DRIFT")
    witness = validate_runner_claim(
        admission_path=admission,
        ledger_path=ledger_path,
        runner_arguments=runner_arguments,
        parent_authority_path=parent_authority_path,
        runner_arguments_path=runner_arguments_path,
        reservation_receipt=reservation_receipt,
        claim_receipt=claim_receipt,
        actual_runner_executable_path=actual_runner_executable_path,
        actual_authority_python_executable_path=actual_authority_python_executable_path,
        expected_owner_pid=expected_owner_pid,
    )
    _write_exclusive(expected, witness)
    persisted = _read_json(expected, "E2E_CLAIM_WITNESS_INVALID")
    if persisted != witness:
        raise E2EFinalAdmissionError("E2E_CLAIM_WITNESS_DRIFT")
    return witness


def validate_runner_claim_witness(
    *, admission_path: Path, ledger_path: Path, witness_path: Path
) -> dict[str, Any]:
    """witness fileからownerと全claim lineageをcanonical consumerへ再束縛する。"""

    admission = Path(admission_path).resolve(strict=True)
    source = _read_json(admission, "E2E_ADMISSION_INVALID")
    _validate_admission(source)
    expected = _claim_witness_path(
        source, admission_path=admission, require_absent=False
    )
    supplied = Path(witness_path).resolve(strict=True)
    if _path_key(supplied) != _path_key(expected):
        raise E2EFinalAdmissionError("E2E_CLAIM_WITNESS_PATH_DRIFT")
    witness = _read_json(supplied, "E2E_CLAIM_WITNESS_INVALID")
    owner = _validate_process_identity(witness.get("ownerProcessIdentity"))
    required = {
        "schemaVersion",
        "claimId",
        "claimReceiptPath",
        "claimReceiptSha256",
        "ownerProcessIdentity",
        "attemptKey",
        "admissionId",
        "admissionPath",
        "runnerArgumentsPath",
        "reservationReceiptPath",
        "reservationReceiptSha256",
    }
    if (
        set(witness) != required
        or witness.get("schemaVersion") != CLAIM_WITNESS_SCHEMA
        or _path_key(Path(str(witness.get("admissionPath") or "")))
        != _path_key(admission)
    ):
        raise E2EFinalAdmissionError("E2E_CLAIM_WITNESS_INVALID")
    actual = validate_runner_claim(
        admission_path=admission,
        ledger_path=ledger_path,
        runner_arguments=_read_runner_arguments(
            Path(str(witness["runnerArgumentsPath"]))
        ),
        parent_authority_path=Path(str(source["expectedParentAuthorityPath"])),
        runner_arguments_path=Path(str(witness["runnerArgumentsPath"])),
        reservation_receipt=Path(str(witness["reservationReceiptPath"])),
        claim_receipt=Path(str(witness["claimReceiptPath"])),
        actual_runner_executable_path=Path(str(source["runnerExecutablePath"])),
        actual_authority_python_executable_path=Path(
            str(source["authorityPythonExecutablePath"])
        ),
        expected_owner_pid=int(owner["pid"]),
    )
    if actual != witness:
        raise E2EFinalAdmissionError("E2E_CLAIM_WITNESS_DRIFT")
    return actual


def _issue_from_manifest(manifest_path: Path, output_path: Path) -> dict[str, Any]:
    manifest = _read_json(manifest_path, "E2E_ISSUE_MANIFEST_INVALID")
    return issue_admission(
        issue_date=str(manifest.get("issueDate") or ""),
        canonical_product_id=str(manifest.get("canonicalProductId") or ""),
        repo_root=Path(str(manifest.get("repoRoot") or "")),
        runner_path=Path(str(manifest.get("runnerPath") or "")),
        runner_arguments=manifest.get("runnerArguments"),
        expected_parent_authority_path=Path(
            str(
                manifest.get("expectedParentAuthorityPath")
                or manifest.get("parentAuthorityPath")
                or ""
            )
        ),
        runner_arguments_path=Path(
            str(
                manifest.get("expectedRunnerArgumentsPath")
                or manifest.get("runnerArgumentsPath")
                or ""
            )
        ),
        runner_executable_path=Path(
            str(manifest.get("runnerExecutablePath") or "")
        ),
        authority_python_executable_path=Path(
            str(manifest.get("authorityPythonExecutablePath") or "")
        ),
        evidence_bindings=manifest.get("evidenceBindings"),
        output_path=output_path,
        expected_reservation_receipt_path=(
            Path(str(manifest["expectedReservationReceiptPath"]))
            if manifest.get("expectedReservationReceiptPath")
            else None
        ),
        expected_claim_receipt_path=(
            Path(str(manifest["expectedClaimReceiptPath"]))
            if manifest.get("expectedClaimReceiptPath")
            else None
        ),
        expected_claim_witness_path=(
            Path(str(manifest["expectedClaimWitnessPath"]))
            if manifest.get("expectedClaimWitnessPath")
            else None
        ),
    )


def default_attempt_ledger_path() -> Path:
    return _managed_authority_root() / "news-grasp-e2e-final-attempts.json"


def _read_runner_arguments(path: Path) -> list[str]:
    try:
        value = json.loads(
            _read_stable_bytes(
                Path(path).resolve(strict=True),
                max_bytes=MAX_RUNNER_ARGUMENTS_BYTES,
                code="E2E_RUNNER_ARGUMENTS_INVALID",
            ).decode("utf-8-sig")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
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
    consume_parser.add_argument("--parent-authority", type=Path, required=True)
    consume_parser.add_argument("--reservation-output", type=Path, required=True)
    consume_parser.add_argument("--runner-executable", type=Path, required=True)
    consume_parser.add_argument(
        "--authority-python-executable", type=Path, required=True
    )
    validate_issued_parser = subparsers.add_parser("validate-issued")
    validate_issued_parser.add_argument("--admission", type=Path, required=True)
    validate_issued_parser.add_argument(
        "--runner-arguments-file", type=Path, required=True
    )
    validate_issued_parser.add_argument(
        "--reservation-output", type=Path, required=True
    )
    validate_issued_parser.add_argument("--claim-output", type=Path, required=True)
    validate_issued_parser.add_argument(
        "--claim-witness-output", type=Path, required=True
    )
    validate_issued_parser.add_argument(
        "--parent-authority",
        "--expected-parent-authority",
        dest="parent_authority",
        type=Path,
        required=True,
    )
    validate_issued_parser.add_argument(
        "--runner-executable", type=Path, required=True
    )
    validate_issued_parser.add_argument(
        "--authority-python-executable", type=Path, required=True
    )
    claim_parser = subparsers.add_parser("claim-runner")
    claim_parser.add_argument("--admission", type=Path, required=True)
    claim_parser.add_argument("--runner-arguments-file", type=Path, required=True)
    claim_parser.add_argument("--parent-authority", type=Path, required=True)
    claim_parser.add_argument("--reservation-receipt", type=Path, required=True)
    claim_parser.add_argument("--claim-output", type=Path, required=True)
    claim_parser.add_argument("--runner-executable", type=Path, required=True)
    claim_parser.add_argument(
        "--authority-python-executable", type=Path, required=True
    )
    claim_parser.add_argument("--current-runner-pid", type=int, required=True)
    claim_parser.add_argument("--claim-nonce", required=True)
    validate_claim_parser = subparsers.add_parser("validate-runner-claim")
    validate_claim_parser.add_argument("--admission", type=Path, required=True)
    validate_claim_parser.add_argument(
        "--runner-arguments-file", type=Path, required=True
    )
    validate_claim_parser.add_argument("--parent-authority", type=Path, required=True)
    validate_claim_parser.add_argument(
        "--reservation-receipt", type=Path, required=True
    )
    validate_claim_parser.add_argument("--claim-receipt", type=Path, required=True)
    validate_claim_parser.add_argument("--runner-executable", type=Path, required=True)
    validate_claim_parser.add_argument(
        "--authority-python-executable", type=Path, required=True
    )
    validate_claim_parser.add_argument("--expected-owner-pid", type=int, required=True)
    write_witness_parser = subparsers.add_parser("write-runner-claim-witness")
    write_witness_parser.add_argument("--admission", type=Path, required=True)
    write_witness_parser.add_argument(
        "--runner-arguments-file", type=Path, required=True
    )
    write_witness_parser.add_argument("--parent-authority", type=Path, required=True)
    write_witness_parser.add_argument(
        "--reservation-receipt", type=Path, required=True
    )
    write_witness_parser.add_argument("--claim-receipt", type=Path, required=True)
    write_witness_parser.add_argument("--witness-output", type=Path, required=True)
    write_witness_parser.add_argument("--runner-executable", type=Path, required=True)
    write_witness_parser.add_argument(
        "--authority-python-executable", type=Path, required=True
    )
    write_witness_parser.add_argument("--expected-owner-pid", type=int, required=True)
    validate_witness_parser = subparsers.add_parser(
        "validate-runner-claim-witness"
    )
    validate_witness_parser.add_argument("--admission", type=Path, required=True)
    validate_witness_parser.add_argument("--claim-witness", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "issue":
            result = _issue_from_manifest(args.manifest, args.output)
        elif args.command == "consume":
            result = consume_admission(
                admission_path=args.admission,
                ledger_path=default_attempt_ledger_path(),
                runner_arguments=_read_runner_arguments(args.runner_arguments_file),
                parent_authority_path=args.parent_authority,
                runner_arguments_path=args.runner_arguments_file,
                reservation_output=args.reservation_output,
                actual_runner_executable_path=args.runner_executable,
                actual_authority_python_executable_path=args.authority_python_executable,
            )
        elif args.command == "validate-issued":
            result = validate_issued_admission(
                admission_path=args.admission,
                runner_arguments=_read_runner_arguments(args.runner_arguments_file),
                parent_authority_path=args.parent_authority,
                runner_arguments_path=args.runner_arguments_file,
                reservation_output=args.reservation_output,
                claim_output=args.claim_output,
                claim_witness_output=args.claim_witness_output,
                actual_runner_executable_path=args.runner_executable,
                actual_authority_python_executable_path=args.authority_python_executable,
            )
        elif args.command == "claim-runner":
            result = claim_runner(
                admission_path=args.admission,
                ledger_path=default_attempt_ledger_path(),
                runner_arguments=_read_runner_arguments(args.runner_arguments_file),
                parent_authority_path=args.parent_authority,
                runner_arguments_path=args.runner_arguments_file,
                reservation_receipt=args.reservation_receipt,
                claim_output=args.claim_output,
                actual_runner_executable_path=args.runner_executable,
                actual_authority_python_executable_path=args.authority_python_executable,
                current_runner_pid=args.current_runner_pid,
                claim_nonce=args.claim_nonce,
            )
        elif args.command == "validate-runner-claim":
            result = validate_runner_claim(
                admission_path=args.admission,
                ledger_path=default_attempt_ledger_path(),
                runner_arguments=_read_runner_arguments(args.runner_arguments_file),
                parent_authority_path=args.parent_authority,
                runner_arguments_path=args.runner_arguments_file,
                reservation_receipt=args.reservation_receipt,
                claim_receipt=args.claim_receipt,
                actual_runner_executable_path=args.runner_executable,
                actual_authority_python_executable_path=args.authority_python_executable,
                expected_owner_pid=args.expected_owner_pid,
            )
        elif args.command == "write-runner-claim-witness":
            result = write_runner_claim_witness(
                admission_path=args.admission,
                ledger_path=default_attempt_ledger_path(),
                runner_arguments=_read_runner_arguments(args.runner_arguments_file),
                parent_authority_path=args.parent_authority,
                runner_arguments_path=args.runner_arguments_file,
                reservation_receipt=args.reservation_receipt,
                claim_receipt=args.claim_receipt,
                witness_output=args.witness_output,
                actual_runner_executable_path=args.runner_executable,
                actual_authority_python_executable_path=args.authority_python_executable,
                expected_owner_pid=args.expected_owner_pid,
            )
        else:
            result = validate_runner_claim_witness(
                admission_path=args.admission,
                ledger_path=default_attempt_ledger_path(),
                witness_path=args.claim_witness,
            )
    except E2EFinalAdmissionError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
