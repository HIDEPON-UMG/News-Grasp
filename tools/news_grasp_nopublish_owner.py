"""NoPublish runnerのproduct-local process ownerと終端authority writer。"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence


MAX_JSON_BYTES = 64 * 1024
MAX_RUNTIME_FILE_BYTES = 64 * 1024 * 1024
HASH_PATTERN_LENGTH = 64


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((_path_key(candidate), _path_key(root))) == _path_key(root)
    except ValueError:
        return False


def _has_reparse_attribute(info: os.stat_result) -> bool:
    attributes = int(getattr(info, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(info.st_mode) or bool(attributes & reparse_flag)


def _file_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    """Windowsでpath/handle間のpermission bit差を除いた安定identityを返す。"""

    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_nlink),
        int(info.st_size),
        int(info.st_mtime_ns),
    )


def _assert_no_reparse_traversal(path: Path) -> None:
    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if not os.path.lexists(current):
            continue
        try:
            info = os.lstat(current)
        except OSError as error:
            raise RuntimeError("NEWS_GRASP_NOPUBLISH_OWNER_PATH_INVALID") from error
        if _has_reparse_attribute(info):
            raise RuntimeError("NEWS_GRASP_NOPUBLISH_OWNER_PATH_INVALID")


def _canonical_repo_file(
    path: Path,
    *,
    root: Path,
    max_bytes: int,
    require_unique: bool = False,
) -> Path:
    lexical_root = Path(os.path.abspath(os.fspath(root)))
    lexical = Path(os.path.abspath(os.fspath(path)))
    if not _is_within(lexical, lexical_root):
        raise RuntimeError("NEWS_GRASP_NOPUBLISH_OWNER_PATH_INVALID")
    _assert_no_reparse_traversal(lexical_root)
    _assert_no_reparse_traversal(lexical)
    try:
        root_resolved = lexical_root.resolve(strict=True)
        candidate = lexical.resolve(strict=True)
        info = os.stat(candidate, follow_symlinks=False)
    except OSError as error:
        raise RuntimeError("NEWS_GRASP_NOPUBLISH_OWNER_PATH_INVALID") from error
    if (
        not _is_within(candidate, root_resolved)
        or not stat.S_ISREG(info.st_mode)
        or info.st_size > max_bytes
        or (require_unique and int(info.st_nlink) != 1)
    ):
        raise RuntimeError("NEWS_GRASP_NOPUBLISH_OWNER_PATH_INVALID")
    return candidate


def _canonical_repo_future_file(
    path: Path,
    *,
    root: Path,
    allow_existing: bool = False,
) -> Path:
    lexical_root = Path(os.path.abspath(os.fspath(root)))
    lexical = Path(os.path.abspath(os.fspath(path)))
    if not _is_within(lexical, lexical_root):
        raise RuntimeError("NEWS_GRASP_NOPUBLISH_OWNER_PATH_INVALID")
    _assert_no_reparse_traversal(lexical_root)
    _assert_no_reparse_traversal(lexical.parent)
    try:
        root_resolved = lexical_root.resolve(strict=True)
        parent_resolved = lexical.parent.resolve(strict=True)
    except OSError as error:
        raise RuntimeError("NEWS_GRASP_NOPUBLISH_OWNER_PATH_INVALID") from error
    if not _is_within(parent_resolved, root_resolved):
        raise RuntimeError("NEWS_GRASP_NOPUBLISH_OWNER_PATH_INVALID")
    if lexical.exists():
        if not allow_existing:
            raise RuntimeError("NEWS_GRASP_NOPUBLISH_OWNER_PATH_INVALID")
        return _canonical_repo_file(
            lexical,
            root=root_resolved,
            max_bytes=MAX_JSON_BYTES,
            require_unique=True,
        )
    return lexical


def _canonical_external_file(path: Path, *, max_bytes: int) -> Path:
    lexical = Path(os.path.abspath(os.fspath(path)))
    _assert_no_reparse_traversal(lexical)
    try:
        candidate = lexical.resolve(strict=True)
        info = os.stat(candidate, follow_symlinks=False)
    except OSError as error:
        raise RuntimeError("NEWS_GRASP_NOPUBLISH_OWNER_PATH_INVALID") from error
    if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
        raise RuntimeError("NEWS_GRASP_NOPUBLISH_OWNER_PATH_INVALID")
    return candidate


def _read_stable_bytes(
    path: Path,
    *,
    max_bytes: int,
    root: Path | None = None,
    require_unique: bool = False,
) -> tuple[Path, bytes]:
    candidate = (
        _canonical_repo_file(
            path,
            root=root,
            max_bytes=max_bytes,
            require_unique=require_unique,
        )
        if root is not None
        else _canonical_external_file(path, max_bytes=max_bytes)
    )
    try:
        lexical_info = os.lstat(candidate)
        expected_info = os.stat(candidate, follow_symlinks=False)
        if _has_reparse_attribute(lexical_info):
            raise RuntimeError("NEWS_GRASP_NOPUBLISH_OWNER_INPUT_INVALID")
        expected_identity = _file_identity(expected_info)
        with candidate.open("rb") as handle:
            before = os.fstat(handle.fileno())
            if (
                _file_identity(before) != expected_identity
                or _has_reparse_attribute(before)
                or not stat.S_ISREG(before.st_mode)
                or before.st_size > max_bytes
                or (require_unique and int(before.st_nlink) != 1)
            ):
                raise RuntimeError("NEWS_GRASP_NOPUBLISH_OWNER_INPUT_INVALID")
            payload = handle.read(max_bytes + 1)
            after = os.fstat(handle.fileno())
    except OSError as error:
        raise RuntimeError("NEWS_GRASP_NOPUBLISH_OWNER_INPUT_INVALID") from error
    if (
        len(payload) > max_bytes
        or len(payload) != before.st_size
        or _file_identity(after) != expected_identity
    ):
        raise RuntimeError("NEWS_GRASP_NOPUBLISH_OWNER_INPUT_INVALID")
    return candidate, payload


def _read_json_snapshot(
    path: Path,
    *,
    root: Path,
    max_bytes: int = MAX_JSON_BYTES,
    require_unique: bool = True,
) -> tuple[Any, bytes]:
    try:
        _, payload = _read_stable_bytes(
            path,
            root=root,
            max_bytes=max_bytes,
            require_unique=require_unique,
        )
        return json.loads(payload.decode("utf-8-sig")), payload
    except (RuntimeError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError("NEWS_GRASP_NOPUBLISH_OWNER_INPUT_INVALID") from error


def _file_sha256(
    path: Path,
    *,
    root: Path | None = None,
    max_bytes: int = MAX_RUNTIME_FILE_BYTES,
    require_unique: bool = False,
) -> str:
    _, payload = _read_stable_bytes(
        path,
        root=root,
        max_bytes=max_bytes,
        require_unique=require_unique,
    )
    return hashlib.sha256(payload).hexdigest()


def _load_bound_module(
    path: Path,
    name: str,
    *,
    root: Path,
    expected_sha256: str,
) -> Any:
    candidate, payload = _read_stable_bytes(
        path,
        root=root,
        max_bytes=MAX_RUNTIME_FILE_BYTES,
        require_unique=True,
    )
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise RuntimeError("NEWS_GRASP_NOPUBLISH_OWNER_RUNTIME_BINDING_DRIFT")
    spec = importlib.util.spec_from_file_location(name, candidate)
    if spec is None or spec.loader is None:
        raise RuntimeError("NEWS_GRASP_NOPUBLISH_OWNER_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        exec(compile(payload.decode("utf-8-sig"), str(candidate), "exec"), module.__dict__)
    except (UnicodeError, SyntaxError) as error:
        raise RuntimeError("NEWS_GRASP_NOPUBLISH_OWNER_IMPORT_FAILED") from error
    return module


def _validate_sha256(value: str) -> str:
    normalized = str(value).lower()
    if len(normalized) != HASH_PATTERN_LENGTH or any(char not in "0123456789abcdef" for char in normalized):
        raise RuntimeError("NEWS_GRASP_NOPUBLISH_OWNER_RUNTIME_BINDING_INVALID")
    return normalized


def _read_launch_evidence(
    path: Path,
    *,
    issue_date: str,
    expected_root: Path,
    expected_min_mtime_ns: int,
) -> dict[str, Any]:
    candidate = _canonical_repo_file(
        path,
        root=expected_root,
        max_bytes=MAX_JSON_BYTES,
        require_unique=True,
    )
    value, _ = _read_json_snapshot(candidate, root=expected_root)
    if (
        candidate.stat().st_mtime_ns < expected_min_mtime_ns
        or not isinstance(value, dict)
        or value.get("schemaVersion") != "NEWS_GRASP_RUNNER_LAUNCH_EVIDENCE_V1"
        or value.get("status") != "terminal_state_reached"
        or value.get("reasonCode") != "NEWS_GRASP_RELEASE_NOPUBLISH_TERMINAL"
        or value.get("issueDate") != issue_date
        or int(value.get("childExitCode", -1)) != 0
    ):
        raise RuntimeError("NEWS_GRASP_RUNNER_LAUNCH_EVIDENCE_IDENTITY_DRIFT")
    return value


def _write_terminal_authority(
    *,
    bridge: Any,
    producer_path: Path,
    repo_root: Path,
    policy_path: Path,
    attempt: int,
    admission_path: Path,
    runner_arguments_path: Path,
    runner_state_path: Path,
    claim_path: Path,
    process_identity: dict[str, Any],
    child_launch_evidence: dict[str, Any],
) -> Path:
    root = Path(repo_root).resolve(strict=True)
    policy = _canonical_repo_file(
        policy_path,
        root=root,
        max_bytes=MAX_JSON_BYTES,
        require_unique=True,
    )
    admission_value, admission_bytes = _read_json_snapshot(admission_path, root=root)
    arguments_value, arguments_bytes = _read_json_snapshot(runner_arguments_path, root=root)
    state_value, state_bytes = _read_json_snapshot(runner_state_path, root=root)
    claim_value, _ = _read_json_snapshot(claim_path, root=root)
    if not isinstance(admission_value, dict) or _path_key(Path(str(admission_value.get("repoRoot") or ""))) != _path_key(root):
        raise RuntimeError("E2E_RUNNER_TERMINAL_AUTHORITY_PATH_INVALID")
    admission = _canonical_repo_file(admission_path, root=root, max_bytes=MAX_JSON_BYTES, require_unique=True)
    arguments = _canonical_repo_file(runner_arguments_path, root=root, max_bytes=MAX_JSON_BYTES, require_unique=True)
    state = _canonical_repo_file(runner_state_path, root=root, max_bytes=MAX_JSON_BYTES, require_unique=True)
    claim = _canonical_repo_file(claim_path, root=root, max_bytes=MAX_JSON_BYTES, require_unique=True)
    try:
        bridge._validate_claim_receipt(claim_value)
    except Exception as error:
        raise RuntimeError("E2E_RUNNER_TERMINAL_AUTHORITY_CLAIM_INVALID") from error
    claim_owner = claim_value.get("ownerProcessIdentity") if isinstance(claim_value, dict) else None
    if (
        not isinstance(arguments_value, list)
        or not isinstance(state_value, dict)
        or state_value.get("status") != "publish_dry_run_ok"
        or int(state_value.get("exit_code", -1)) != 0
        or child_launch_evidence.get("status") != "terminal_state_reached"
        or str(state_value.get("e2eFinalAdmissionPath")) != str(admission)
        or str(state_value.get("e2eFinalRunnerArgumentsPath")) != str(arguments)
        or not isinstance(claim_owner, dict)
        or claim_value.get("admissionPath") != str(admission)
        or claim_value.get("admissionSha256") != hashlib.sha256(admission_bytes).hexdigest()
        or claim_value.get("runnerArgumentsPath") != str(arguments)
        or claim_value.get("runnerArgumentsSha256") != hashlib.sha256(arguments_bytes).hexdigest()
        or claim_value.get("runnerExecutablePath") != process_identity.get("imagePath")
        or claim_value.get("runnerExecutableSha256") != process_identity.get("imageSha256")
        or process_identity != claim_owner
    ):
        raise RuntimeError("E2E_RUNNER_TERMINAL_AUTHORITY_INVALID")

    ledger_path = bridge.default_attempt_ledger_path().resolve()
    ledger_value, ledger_hash = bridge._ledger_snapshot(ledger_path)
    ledger_row = ledger_value.get("attempts", {}).get(str(claim_value.get("attemptKey")))
    if (
        not isinstance(ledger_row, dict)
        or ledger_row.get("state") != "runner_claimed"
        or ledger_row.get("claimReceiptPath") != str(claim)
        or ledger_row.get("claimReceiptSha256") != claim_value.get("receiptSha256")
        or ledger_row.get("ownerProcessIdentity") != claim_owner
    ):
        raise RuntimeError("E2E_RUNNER_TERMINAL_AUTHORITY_LEDGER_INVALID")

    producer = _canonical_repo_file(
        producer_path,
        root=root,
        max_bytes=MAX_RUNTIME_FILE_BYTES,
        require_unique=True,
    )
    unsigned: dict[str, Any] = {
        "schemaVersion": "NEWS_GRASP_E2E_RUNNER_TERMINAL_AUTHORITY_V1",
        "attempt": int(attempt),
        "admissionPath": str(admission),
        "admissionSha256": hashlib.sha256(admission_bytes).hexdigest(),
        "runnerArgumentsPath": str(arguments),
        "runnerArgumentsSha256": hashlib.sha256(arguments_bytes).hexdigest(),
        "claimPath": str(claim),
        "claimSha256": str(claim_value.get("receiptSha256") or ""),
        "claimOwnerProcessIdentity": claim_owner,
        "ledgerPath": str(ledger_path),
        "ledgerSha256": ledger_hash or "",
        "statePath": str(state),
        "stateSha256": hashlib.sha256(state_bytes).hexdigest(),
        "runnerExitCode": 0,
        "runnerStatus": "publish_dry_run_ok",
        "ownerProcessIdentity": claim_owner,
        "launcherProcessIdentity": process_identity,
        "childLaunchEvidenceSha256": hashlib.sha256(
            json.dumps(
                child_launch_evidence,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "producerPath": str(producer),
        "producerSha256": _file_sha256(
            producer,
            root=root,
            require_unique=True,
        ),
    }
    unsigned["authoritySha256"] = bridge._canonical_sha256(unsigned)
    output = _canonical_repo_future_file(
        policy.parent / f"e2e-terminal-authority-{int(attempt)}.json",
        root=root,
        allow_existing=True,
    )
    if output.exists():
        persisted, _ = _read_json_snapshot(output, root=root)
        if persisted != unsigned:
            raise RuntimeError("E2E_RUNNER_TERMINAL_AUTHORITY_DRIFT")
    else:
        bridge._write_exclusive(output, unsigned)
    return output


def run_owned_nopublish(
    *,
    repo_root: Path,
    python_executable: Path,
    powershell_executable: Path,
    runner_arguments_path: Path,
    policy_path: Path,
    attempt: int,
    admission_path: Path,
    state_path: Path,
    claim_path: Path,
    launch_evidence_path: Path,
    expected_owner_sha256: str = "",
    expected_bridge_sha256: str = "",
    expected_owned_process_sha256: str = "",
) -> int:
    root = Path(os.path.abspath(os.fspath(repo_root)))
    requested_python = python_executable.resolve(strict=True)
    current_python = Path(sys.executable).resolve(strict=True)
    if _path_key(requested_python) != _path_key(current_python):
        raise RuntimeError("NEWS_GRASP_NOPUBLISH_OWNER_PYTHON_IDENTITY_DRIFT")

    owner_hash = _validate_sha256(expected_owner_sha256)
    bridge_hash = _validate_sha256(expected_bridge_sha256)
    owned_process_hash = _validate_sha256(expected_owned_process_sha256)
    owner_path = _canonical_repo_file(
        Path(__file__),
        root=root,
        max_bytes=MAX_RUNTIME_FILE_BYTES,
        require_unique=True,
    )
    if _file_sha256(owner_path, root=root, require_unique=True) != owner_hash:
        raise RuntimeError("NEWS_GRASP_NOPUBLISH_OWNER_RUNTIME_BINDING_DRIFT")
    bridge = _load_bound_module(
        root / "tools" / "e2e_final_admission_bridge.py",
        "news_grasp_nopublish_owner_bridge",
        root=root,
        expected_sha256=bridge_hash,
    )
    owned = _load_bound_module(
        root / "tools" / "news_grasp_owned_process.py",
        "news_grasp_nopublish_owner_process",
        root=root,
        expected_sha256=owned_process_hash,
    )

    admission_value, _ = _read_json_snapshot(admission_path, root=root)
    arguments, _ = _read_json_snapshot(runner_arguments_path, root=root)
    if (
        not isinstance(admission_value, dict)
        or _path_key(Path(str(admission_value.get("repoRoot") or ""))) != _path_key(root)
        or not isinstance(arguments, list)
        or any(not isinstance(item, str) or not item for item in arguments)
        or arguments.count("-File") != 1
    ):
        raise RuntimeError("NEWS_GRASP_NOPUBLISH_OWNER_ARGUMENTS_INVALID")

    expected_powershell = Path(
        os.path.expandvars(r"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe")
    )
    powershell = _canonical_external_file(
        powershell_executable,
        max_bytes=MAX_RUNTIME_FILE_BYTES,
    )
    powershell_sha256 = _file_sha256(powershell)
    if (
        _path_key(powershell) != _path_key(expected_powershell)
        or _path_key(Path(str(admission_value.get("runnerExecutablePath") or ""))) != _path_key(powershell)
        or admission_value.get("runnerExecutableSha256") != powershell_sha256
    ):
        raise RuntimeError("NEWS_GRASP_NOPUBLISH_OWNER_POWERSHELL_IDENTITY_DRIFT")

    runner_path = _canonical_repo_file(
        Path(arguments[arguments.index("-File") + 1]),
        root=root,
        max_bytes=MAX_RUNTIME_FILE_BYTES,
        require_unique=True,
    )
    if (
        _path_key(Path(str(admission_value.get("runnerPath") or ""))) != _path_key(runner_path)
        or admission_value.get("runnerSha256")
        != _file_sha256(runner_path, root=root, require_unique=True)
    ):
        raise RuntimeError("NEWS_GRASP_NOPUBLISH_OWNER_ARGUMENTS_INVALID")
    _canonical_repo_file(policy_path, root=root, max_bytes=MAX_JSON_BYTES, require_unique=True)
    _canonical_repo_future_file(state_path, root=root)
    _canonical_repo_future_file(claim_path, root=root)
    _canonical_repo_future_file(launch_evidence_path, root=root)

    child_environment = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONINSPECT", "PYTHONUSERBASE"):
        child_environment.pop(name, None)
    child_environment.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "PYTHONNOUSERSITE": "1",
            "NEWS_GRASP_REPO_ROOT": str(root.resolve(strict=True)),
        }
    )
    started_ns = time.time_ns()
    process = owned.spawn_owned(
        [str(powershell), *arguments],
        cwd=str(root.resolve(strict=True)),
        env=child_environment,
        capture_output=False,
    )
    try:
        observed = bridge._query_process_identity(process.pid)
        if (
            _path_key(Path(str(observed["imagePath"]))) != _path_key(powershell)
            or observed["imageSha256"] != powershell_sha256
        ):
            process.close_job()
            raise RuntimeError("NEWS_GRASP_NOPUBLISH_OWNER_POWERSHELL_IDENTITY_DRIFT")
        process_identity = {
            "pid": int(observed["pid"]),
            "parentPid": int(observed["parentPid"]),
            "creationFileTimeUtc": str(observed["creationFileTimeUtc"]),
            "imagePath": str(observed["imagePath"]),
            "imageSha256": str(observed["imageSha256"]),
        }
        try:
            exit_code = int(process.wait(timeout=60 * 60))
        except subprocess.TimeoutExpired:
            process.close_job()
            raise RuntimeError("NEWS_GRASP_RELEASE_NOPUBLISH_TIMEOUT")
    finally:
        process.close()

    if exit_code != 0:
        return exit_code
    evidence = _read_launch_evidence(
        launch_evidence_path,
        issue_date=str(admission_value["issueDate"]),
        expected_root=root,
        expected_min_mtime_ns=started_ns,
    )
    if (
        int(evidence.get("processId", -1)) != process_identity["pid"]
        or _path_key(Path(str(evidence.get("powershellPath") or ""))) != _path_key(powershell)
        or evidence.get("powershellSha256") != powershell_sha256
        or _path_key(Path(str(evidence.get("runnerPath") or ""))) != _path_key(runner_path)
        or evidence.get("runnerSha256")
        != _file_sha256(runner_path, root=root, require_unique=True)
    ):
        raise RuntimeError("NEWS_GRASP_RUNNER_LAUNCH_EVIDENCE_IDENTITY_DRIFT")
    _write_terminal_authority(
        bridge=bridge,
        producer_path=owner_path,
        repo_root=root,
        policy_path=policy_path,
        attempt=attempt,
        admission_path=admission_path,
        runner_arguments_path=runner_arguments_path,
        runner_state_path=state_path,
        claim_path=claim_path,
        process_identity=process_identity,
        child_launch_evidence=evidence,
    )
    return 0


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--python-executable", type=Path, required=True)
    parser.add_argument("--powershell-executable", type=Path, required=True)
    parser.add_argument("--runner-arguments", type=Path, required=True)
    parser.add_argument("--attempt-policy", type=Path, required=True)
    parser.add_argument("--logical-attempt", type=int, choices=(1, 2), required=True)
    parser.add_argument("--admission", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--claim", type=Path, required=True)
    parser.add_argument("--launch-evidence", type=Path, required=True)
    parser.add_argument("--expected-owner-sha256", required=True)
    parser.add_argument("--expected-bridge-sha256", required=True)
    parser.add_argument("--expected-owned-process-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        return run_owned_nopublish(
            repo_root=args.repo_root,
            python_executable=args.python_executable,
            powershell_executable=args.powershell_executable,
            runner_arguments_path=args.runner_arguments,
            policy_path=args.attempt_policy,
            attempt=args.logical_attempt,
            admission_path=args.admission,
            state_path=args.state,
            claim_path=args.claim,
            launch_evidence_path=args.launch_evidence,
            expected_owner_sha256=args.expected_owner_sha256,
            expected_bridge_sha256=args.expected_bridge_sha256,
            expected_owned_process_sha256=args.expected_owned_process_sha256,
        )
    except Exception as exc:
        print(f"NEWS_GRASP_NOPUBLISH_OWNER_FAILED:{type(exc).__name__}:{exc}", file=sys.stderr)
        return 76


if __name__ == "__main__":
    raise SystemExit(_main())
