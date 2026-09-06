"""Release gate専用のdirect-mainline NoPublish実行器。

Daily launcherから本moduleをimportしない。隔離済みworktreeと隔離stateだけを使い、
content producer・派生物・六operationのtransactionを実行する一方、外部providerは
一切呼ばない。保護済みissueは再生成せず、翌日のsimulation issueへ写像する。
"""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import importlib
import importlib.util
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

_PRODUCT_ROOT = Path(__file__).resolve().parents[1]
if str(_PRODUCT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PRODUCT_ROOT))
_TRUSTED_SITE_PACKAGES = Path(sys.executable).resolve().parent / "Lib" / "site-packages"
if _TRUSTED_SITE_PACKAGES.is_dir() and str(_TRUSTED_SITE_PACKAGES) not in sys.path:
    # -Sでstartup hook/user siteを無効化したまま、固定Python配下の依存だけを許可する。
    sys.path.append(str(_TRUSTED_SITE_PACKAGES))

SCHEMA = "NEWS_GRASP_RELEASE_NOPUBLISH_RECEIPT_V1"
STATE_SCHEMA = "NEWS_GRASP_RELEASE_NOPUBLISH_STATE_V1"
JST = timezone(timedelta(hours=9), name="Asia/Tokyo")
PROTECTED_RELEASE = "2026-09-02"
daily: Any = None
runtime: Any = None
_RELEASE_CAPABILITY_MARKER = object()
_CANONICAL_RESULT_PREFIX = "release-nopublish-result-"
_CANONICAL_STATE_PREFIX = "release-nopublish-state-"
_LOCAL_ENTRY_CONTEXT_MARKER = object()


class _ReleaseCapability:
    """live high-cost claim検証後にだけ発行するprocess-local capability。"""

    __slots__ = ("witness", "_marker")

    def __init__(self, witness: Mapping[str, Any], marker: object) -> None:
        if marker is not _RELEASE_CAPABILITY_MARKER:
            raise RuntimeError("nopublish_high_cost_capability_invalid")
        self.witness = dict(witness)
        self._marker = marker


class _LocalEntryContext:
    """製品内local entryが検査済み境界をcoreへ渡すためのprocess-local token。"""

    __slots__ = (
        "artifact_root",
        "isolation_receipt",
        "run_identity",
        "source_issue_date",
        "state_root",
        "_marker",
    )

    def __init__(
        self,
        *,
        artifact_root: Path,
        isolation_receipt: Path,
        run_identity: Mapping[str, Any],
        source_issue_date: str,
        state_root: Path,
        marker: object,
    ) -> None:
        if marker is not _LOCAL_ENTRY_CONTEXT_MARKER:
            raise RuntimeError("nopublish_local_entry_context_invalid")
        self.artifact_root = artifact_root
        self.isolation_receipt = isolation_receipt
        self.run_identity = dict(run_identity)
        self.source_issue_date = source_issue_date
        self.state_root = state_root
        self._marker = marker


def _load_release_runtime_modules() -> tuple[Any, Any]:
    """NoPublishの製品runtimeを必要時に遅延importする。"""

    global daily, runtime
    if daily is None:
        daily = importlib.import_module("tools.news_grasp_daily_gate")
    if runtime is None:
        runtime = importlib.import_module("tools.news_grasp_direct_runtime")
    return daily, runtime


def _load_exact_module(path: Path, *, prefix: str) -> Any:
    candidate = path.resolve(strict=True)
    if candidate.is_symlink() or not candidate.is_file():
        raise RuntimeError("nopublish_authority_consumer_invalid")
    module_name = f"{prefix}_{hashlib.sha256(str(candidate).encode()).hexdigest()[:16]}"
    spec = importlib.util.spec_from_file_location(module_name, candidate)
    if spec is None or spec.loader is None:
        raise RuntimeError("nopublish_authority_consumer_invalid")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _require_high_cost_claim(root: Path) -> _ReleaseCapability:
    """current PowerShell parentのlive claimをledgerまでread-onlyで再検証する。"""

    names = {
        "admission": "NEWS_GRASP_E2E_ADMISSION_PATH",
        "arguments": "NEWS_GRASP_E2E_ARGUMENTS_PATH",
        "claim": "NEWS_GRASP_E2E_CLAIM_PATH",
        "reservation": "NEWS_GRASP_E2E_RESERVATION_PATH",
        "parent": "NEWS_GRASP_E2E_PARENT_AUTHORITY_PATH",
    }
    paths: dict[str, Path] = {}
    for key, environment_name in names.items():
        raw = os.environ.get(environment_name, "")
        if not raw:
            raise RuntimeError("nopublish_high_cost_claim_missing")
        candidate = Path(raw).resolve(strict=True)
        if candidate.is_symlink() or not candidate.is_file() or not candidate.is_relative_to(root):
            raise RuntimeError("nopublish_high_cost_claim_invalid")
        paths[key] = candidate
    bridge = _load_exact_module(
        root / "tools" / "e2e_final_admission_bridge.py",
        prefix="news_grasp_nopublish_claim_bridge",
    )
    child_identity = bridge._query_process_identity(os.getpid())
    parent_pid = int(child_identity.get("parentPid") or 0)
    if parent_pid <= 0:
        raise RuntimeError("nopublish_high_cost_claim_invalid")
    arguments = json.loads(paths["arguments"].read_text(encoding="utf-8-sig"))
    claim = json.loads(paths["claim"].read_text(encoding="utf-8-sig"))
    if not isinstance(arguments, list) or not isinstance(claim, dict):
        raise RuntimeError("nopublish_high_cost_claim_invalid")
    witness = bridge.validate_runner_claim(
        admission_path=paths["admission"],
        ledger_path=bridge.default_attempt_ledger_path(),
        runner_arguments=arguments,
        parent_authority_path=paths["parent"],
        runner_arguments_path=paths["arguments"],
        reservation_receipt=paths["reservation"],
        claim_receipt=paths["claim"],
        actual_runner_executable_path=Path(str(claim.get("runnerExecutablePath") or "")),
        actual_authority_python_executable_path=Path(sys.executable),
        expected_owner_pid=parent_pid,
    )
    if not isinstance(witness, dict) or witness.get("ownerProcessIdentity") != claim.get("ownerProcessIdentity"):
        raise RuntimeError("nopublish_high_cost_claim_invalid")
    if not isinstance(witness.get("claimId"), str) or not witness["claimId"]:
        raise RuntimeError("nopublish_high_cost_claim_invalid")
    return _ReleaseCapability({**witness, "moduleProcessIdentity": child_identity}, _RELEASE_CAPABILITY_MARKER)


def _await_owner_start_confirmation(root: Path, process_identity: dict[str, Any]) -> None:
    """本体の生存中にownerのOS照合と永続開始確認を待つ。"""

    from tools.news_grasp_preentry_journal import environment_journal

    context = environment_journal()
    if context is None:
        raise RuntimeError("NEWS_GRASP_PREENTRY_CONTEXT_MISSING")
    journal, issue_date, session_id = context
    detail = {
        "processIdentity": process_identity,
        "modulePath": str(root / "tools" / "news_grasp_release_nopublish.py"),
    }
    journal.append(issue_date, session_id, "module_entered", detail)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        for event in journal.events(issue_date, session_id):
            if event["phase"] == "module_started":
                if event["detail"] != detail:
                    raise RuntimeError("NEWS_GRASP_PREENTRY_START_IDENTITY_DRIFT")
                return
        time.sleep(0.05)
    raise RuntimeError("NEWS_GRASP_PREENTRY_OWNER_CONFIRMATION_TIMEOUT")


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _reject_reparse_chain(path: Path, *, reason: str) -> None:
    """canonical product pathの途中にあるjunction/symlinkを拒否する。"""

    absolute = Path(os.path.abspath(os.fspath(path)))
    for current in (absolute, *absolute.parents):
        if not current.exists() and not current.is_symlink():
            continue
        info = current.lstat()
        attributes = int(getattr(info, "st_file_attributes", 0))
        if stat.S_ISLNK(info.st_mode) or attributes & 0x400:
            raise RuntimeError(reason)


def _canonical_release_state_root() -> Path:
    """KnownFolder由来のRelease-only NoPublish state rootを返す。"""

    _load_release_runtime_modules()
    base = Path(runtime._windows_local_app_data())
    raw_root = Path(
        os.path.abspath(os.fspath(base / "News-Grasp" / "release-nopublish"))
    )
    # resolve() はjunctionを隠すため、解決前の構成要素を先に検査する。
    _reject_reparse_chain(
        raw_root,
        reason="nopublish_release_state_reparse_rejected",
    )
    root = raw_root.resolve(strict=False)
    _reject_reparse_chain(root, reason="nopublish_release_state_reparse_rejected")
    return root


def _canonical_path(path: Path, *, strict: bool, reason: str) -> Path:
    _reject_reparse_chain(path, reason=reason)
    resolved = path.resolve(strict=strict)
    _reject_reparse_chain(resolved, reason=reason)
    return resolved


def _json_file(path: Path, *, reason: str) -> dict[str, Any]:
    candidate = _canonical_path(path, strict=True, reason=reason)
    if not candidate.is_file() or candidate.is_symlink():
        raise RuntimeError(reason)
    try:
        value = json.loads(candidate.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(reason) from exc
    if not isinstance(value, dict):
        raise RuntimeError(reason)
    return value


def _canonical_result_path(state_root: Path, issue_date: str) -> Path:
    return state_root / f"{_CANONICAL_RESULT_PREFIX}{issue_date}.json"


def _canonical_state_path(state_root: Path, issue_date: str) -> Path:
    return state_root / f"{_CANONICAL_STATE_PREFIX}{issue_date}.json"


def _initial_run_identity(
    *,
    repo_root: Path,
    source_issue_date: str,
    isolation_receipt: Path,
) -> dict[str, str]:
    """初回だけ観測したrun identityを、再開時に再計算しない形へ束ねる。"""

    source_head = _git(repo_root, "rev-parse", "HEAD")
    receipt_sha256 = hashlib.sha256(isolation_receipt.read_bytes()).hexdigest()
    simulation_date = simulation_issue_date(source_issue_date)
    manifest_id = _sha(
        {
            "source_head": source_head,
            "source_issue_date": source_issue_date,
            "simulation_issue_date": simulation_date,
            "isolation_receipt_sha256": receipt_sha256,
        }
    )
    return {
        "schemaVersion": "NEWS_GRASP_RELEASE_NOPUBLISH_RUN_IDENTITY_V1",
        "sourceIssueDate": source_issue_date,
        "simulationIssueDate": simulation_date,
        "sourceHead": source_head,
        "isolationReceiptSha256": receipt_sha256,
        "manifestId": manifest_id,
    }


def _bound_run_identity(
    value: Any,
    *,
    source_issue_date: str,
) -> dict[str, str]:
    """immutable bindingから初回run identityを取り出し、shapeを検証する。"""

    if not isinstance(value, Mapping):
        raise RuntimeError("nopublish_run_identity_binding_missing")
    identity = {str(key): str(item) for key, item in value.items()}
    expected_manifest_id = _sha(
        {
            "source_head": identity.get("sourceHead", ""),
            "source_issue_date": identity.get("sourceIssueDate", ""),
            "simulation_issue_date": identity.get("simulationIssueDate", ""),
            "isolation_receipt_sha256": identity.get("isolationReceiptSha256", ""),
        }
    )
    if (
        identity.get("schemaVersion") != "NEWS_GRASP_RELEASE_NOPUBLISH_RUN_IDENTITY_V1"
        or identity.get("sourceIssueDate") != source_issue_date
        or identity.get("simulationIssueDate") != simulation_issue_date(source_issue_date)
        or re.fullmatch(r"[0-9a-f]{40}", identity.get("sourceHead", "")) is None
        or re.fullmatch(r"[0-9a-f]{64}", identity.get("isolationReceiptSha256", "")) is None
        or re.fullmatch(r"[0-9a-f]{64}", identity.get("manifestId", "")) is None
        or identity.get("manifestId") != expected_manifest_id
    ):
        raise RuntimeError("nopublish_run_identity_binding_invalid")
    return identity


def _require_local_entry_context(
    context: Any,
    *,
    repo_root: Path,
    source_issue_date: str,
    state_root: Path,
    isolation_receipt: Path,
) -> _LocalEntryContext:
    if not isinstance(context, _LocalEntryContext):
        raise RuntimeError("nopublish_local_entry_context_required:local_authority")
    if context._marker is not _LOCAL_ENTRY_CONTEXT_MARKER:
        raise RuntimeError("nopublish_local_entry_context_invalid")
    if context.source_issue_date != source_issue_date:
        raise RuntimeError("nopublish_local_entry_context_identity_drift")
    canonical_state = _canonical_release_state_root()
    supplied_state = _canonical_path(
        state_root,
        strict=False,
        reason="nopublish_release_state_reparse_rejected",
    )
    context_state = _canonical_path(
        context.state_root,
        strict=False,
        reason="nopublish_release_state_reparse_rejected",
    )
    if supplied_state != canonical_state or context_state != canonical_state:
        raise RuntimeError("nopublish_local_entry_context_state_drift")
    supplied_root = _canonical_path(
        repo_root,
        strict=True,
        reason="nopublish_target_root_invalid",
    )
    context_root = _canonical_path(
        context.artifact_root,
        strict=True,
        reason="nopublish_recovery_required",
    )
    if supplied_root != context_root:
        raise RuntimeError("nopublish_local_entry_context_artifact_drift")
    supplied_receipt = _canonical_path(
        isolation_receipt,
        strict=True,
        reason="nopublish_isolation_receipt_reparse_rejected",
    )
    context_receipt = _canonical_path(
        context.isolation_receipt,
        strict=True,
        reason="nopublish_isolation_receipt_reparse_rejected",
    )
    if supplied_receipt != context_receipt:
        raise RuntimeError("nopublish_local_entry_context_receipt_drift")
    _bound_run_identity(
        context.run_identity,
        source_issue_date=source_issue_date,
    )
    return context


def _process_identity(pid: int) -> dict[str, Any]:
    """既存bridgeのOS観測を使い、起動判定そのものには使わない。"""

    from tools.e2e_final_admission_bridge import _query_process_identity

    value = _query_process_identity(pid)
    if not isinstance(value, dict):
        raise RuntimeError("nopublish_process_identity_invalid")
    return value


def _journal_set(canonical_state_root: Path) -> list[Any]:
    """canonical product journalだけをNoPublishの正本として返す。"""

    from tools.news_grasp_preentry_journal import PreentryJournal

    return [PreentryJournal(canonical_state_root / "preentry.sqlite3")]


def _append_journal_event(
    journals: Sequence[Any],
    *,
    issue_date: str,
    session_id: str,
    phase: str,
    detail: Mapping[str, Any],
) -> None:
    for journal in journals:
        journal.append(issue_date, session_id, phase, dict(detail))


def _journal_module_start_count(journal: Any, issue_date: str) -> int:
    return sum(
        1
        for row in journal.events(issue_date)
        if row.get("phase") == "module_started"
    )


def _worktree_path_key(path: str | Path) -> str:
    """git porcelainのworktree pathをOS正規化した完全一致キーへ変換する。"""

    normalized = os.path.normpath(os.path.abspath(os.fspath(path)))
    normalized = os.path.normcase(normalized)
    trimmed = normalized.rstrip("\\/")
    return trimmed or normalized


def _path_is_within(candidate: Path, base: Path) -> bool:
    """既存または未作成のpathを、区切り境界付きでbase配下に限定する。"""

    candidate_key = _worktree_path_key(candidate)
    base_key = _worktree_path_key(base)
    if candidate_key == base_key:
        return False
    separator = "\\" if "\\" in base_key else "/"
    return candidate_key.startswith(base_key + separator)


def _diagnostic_roots(repo_root: Path, canonical_state: Path) -> tuple[Path, Path]:
    """診断出力を許可する専用subtreeを、解決前検査付きで返す。"""

    requested_root = _canonical_path(
        repo_root / "build" / "release-nopublish-diagnostics",
        strict=False,
        reason="nopublish_diagnostic_path_reparse_rejected",
    )
    canonical_root = _canonical_path(
        canonical_state / "diagnostics",
        strict=False,
        reason="nopublish_diagnostic_path_reparse_rejected",
    )
    return requested_root, canonical_root


def _validate_diagnostic_paths(
    *,
    repo_root: Path,
    canonical_state: Path,
    state_file: Path,
    receipt_path: Path,
    isolation_receipt: Path,
) -> tuple[Path, Path]:
    """state/receiptを専用subtreeの同一invocation directoryへ限定する。"""

    requested_root, canonical_root = _diagnostic_roots(repo_root, canonical_state)
    state = _canonical_path(
        state_file,
        strict=False,
        reason="nopublish_diagnostic_path_reparse_rejected",
    )
    receipt = _canonical_path(
        receipt_path,
        strict=False,
        reason="nopublish_diagnostic_path_reparse_rejected",
    )
    if (
        state.name != "state.json"
        or receipt.name != "receipt.json"
        or state == receipt
        or state.parent != receipt.parent
    ):
        raise RuntimeError("nopublish_diagnostic_path_invalid")
    if not any(
        state.parent == root or _path_is_within(state.parent, root)
        for root in (requested_root, canonical_root)
    ):
        raise RuntimeError("nopublish_diagnostic_path_invalid")
    resolved_isolation = _canonical_path(
        isolation_receipt,
        strict=True,
        reason="nopublish_isolation_receipt_reparse_rejected",
    )
    if state == resolved_isolation or receipt == resolved_isolation:
        raise RuntimeError("nopublish_diagnostic_path_invalid")
    return state, receipt


def _registered_worktree_path_keys(listing: str) -> set[str]:
    """`git worktree list --porcelain` のworktree行だけを厳密に読む。"""

    paths: set[str] = set()
    for line in str(listing).splitlines():
        if not line.startswith("worktree "):
            continue
        raw_path = line[len("worktree ") :].strip()
        if raw_path:
            paths.add(_worktree_path_key(raw_path))
    return paths


def _validate_isolation_receipt(
    *,
    repo_root: Path,
    source_issue_date: str,
    isolation_receipt: Path,
) -> dict[str, Any]:
    """P08本体を呼ばず、local entryに必要な隔離境界だけを確認する。"""

    receipt_path = _canonical_path(
        isolation_receipt,
        strict=True,
        reason="nopublish_isolation_receipt_reparse_rejected",
    )
    value = _json_file(
        receipt_path,
        reason="nopublish_isolation_receipt_invalid",
    )
    try:
        root = _canonical_path(
            repo_root,
            strict=True,
            reason="nopublish_target_root_invalid",
        )
        target = _canonical_path(
            Path(str(value.get("targetRoot") or "")),
            strict=True,
            reason="nopublish_target_root_invalid",
        )
        source = _canonical_path(
            Path(str(value.get("sourceRepo") or "")),
            strict=True,
            reason="nopublish_source_repo_invalid",
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        if isinstance(exc, RuntimeError):
            raise
        raise RuntimeError("nopublish_isolation_receipt_invalid") from exc
    if (
        value.get("schemaVersion") != "NEWS_GRASP_E2E_ISOLATION_V1"
        or
        value.get("status") != "Green"
        or value.get("issueDate") != source_issue_date
        or target != root
        or source == target
        or not source.is_dir()
        or value.get("runnerArtifactPredicate") is not False
    ):
        raise RuntimeError("nopublish_isolation_receipt_invalid")

    # receiptの記載だけを信頼せず、実repoのworktree登録と完全一致させる。
    try:
        listing = _git(source, "worktree", "list", "--porcelain")
    except Exception as exc:  # noqa: BLE001 - typed entry Red
        raise RuntimeError("nopublish_worktree_registration_invalid") from exc
    if _worktree_path_key(target) not in _registered_worktree_path_keys(listing):
        raise RuntimeError("nopublish_worktree_registration_invalid")
    return {
        **value,
        "sourceRepo": str(source),
        "targetRoot": str(target),
        "receiptPath": str(receipt_path),
    }


def _saved_green_result(
    path: Path,
    *,
    artifact_root: Path,
    canonical_state: Path,
    source_issue_date: str,
    run_identity: Mapping[str, Any],
) -> dict[str, Any] | None:
    """保存済みGreenをcanonical runtimeと実成果物へ再束縛して再提示する。"""

    if not path.exists():
        return None

    invalid_reason = "nopublish_saved_result_invalid"
    value = _json_file(path, reason=invalid_reason)
    status = str(value.get("status") or "").casefold()
    if value.get("ok") is False or status in {"red", "failed", "blocked"}:
        return None
    if value.get("ok") is not True or status != "publish_dry_run_ok":
        raise RuntimeError(invalid_reason)
    if value.get("receiptSha256") != _sha(
        {key: item for key, item in value.items() if key != "receiptSha256"}
    ):
        raise RuntimeError(invalid_reason)

    identity = _bound_run_identity(
        run_identity,
        source_issue_date=source_issue_date,
    )
    expected_simulation_issue_date = simulation_issue_date(source_issue_date)
    if (
        value.get("schemaVersion") != SCHEMA
        or value.get("source_issue_date") != source_issue_date
        or value.get("simulation_issue_date") != expected_simulation_issue_date
        or str(value.get("source_head") or "").casefold() != identity["sourceHead"]
    ):
        raise RuntimeError(invalid_reason)

    saved_path = _canonical_path(path, strict=True, reason=invalid_reason)
    state_root = _canonical_path(
        canonical_state,
        strict=True,
        reason=invalid_reason,
    )
    expected_state_root = _canonical_release_state_root()
    if state_root != expected_state_root:
        raise RuntimeError(invalid_reason)
    if saved_path != _canonical_result_path(state_root, source_issue_date):
        raise RuntimeError(invalid_reason)
    artifact = _canonical_path(
        artifact_root,
        strict=True,
        reason=invalid_reason,
    )
    if not artifact.is_dir():
        raise RuntimeError(invalid_reason)

    run_id = value.get("run_id")
    if (
        not isinstance(run_id, str)
        or re.fullmatch(
            r"direct-\d{4}-\d{2}-\d{2}-\d+-[0-9a-f]{32}",
            run_id,
        )
        is None
    ):
        raise RuntimeError(invalid_reason)
    if type(value.get("externalEffectCount")) is not int or value["externalEffectCount"] != 0:
        raise RuntimeError(invalid_reason)

    daily_module, runtime_module = _load_release_runtime_modules()
    operations = tuple(str(item) for item in daily_module.DAILY_OPERATIONS)
    if (
        len(operations) != 6
        or value.get("operation_count") != len(operations)
        or value.get("operation_ids") != list(operations)
        or not isinstance(value.get("receipts"), list)
        or len(value["receipts"]) != len(operations)
    ):
        raise RuntimeError(invalid_reason)

    saved_receipts: dict[str, Mapping[str, Any]] = {}
    for item in value["receipts"]:
        if not isinstance(item, Mapping):
            raise RuntimeError(invalid_reason)
        operation_id = item.get("operation_id")
        if not isinstance(operation_id, str) or operation_id in saved_receipts:
            raise RuntimeError(invalid_reason)
        saved_receipts[operation_id] = item
    if list(saved_receipts) != list(operations):
        raise RuntimeError(invalid_reason)

    def parse_mapping(raw: Any) -> dict[str, Any]:
        try:
            parsed = json.loads(str(raw))
        except (TypeError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(invalid_reason) from exc
        if not isinstance(parsed, dict):
            raise RuntimeError(invalid_reason)
        return parsed

    def matches_saved_receipt(
        saved: Mapping[str, Any],
        database: Mapping[str, Any],
    ) -> bool:
        # run_daily_sequenceのprojectionは認可/human-impactの観測値を追加し、
        # schemaVersionだけをgate用へ投影する。operation本体の全値は一致させる。
        for key, expected in database.items():
            if key == "schemaVersion":
                continue
            if saved.get(key) != expected:
                return False
        nested = saved.get("operation_receipt")
        if nested is not None:
            if not isinstance(nested, Mapping):
                return False
            for key, expected in database.items():
                if key == "schemaVersion":
                    continue
                if nested.get(key) != expected:
                    return False
        return True

    db_path = _canonical_path(
        state_root / "direct-mainline.sqlite3",
        strict=True,
        reason=invalid_reason,
    )
    if not db_path.is_file() or db_path.is_symlink():
        raise RuntimeError(invalid_reason)

    try:
        connection = sqlite3.connect(
            f"file:{db_path.as_posix()}?mode=ro",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        with closing(connection) as conn:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            run = conn.execute(
                "SELECT run_id,status,run_intent,issue_date,cwd,manifest_id "
                "FROM runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise RuntimeError(invalid_reason)
            if (
                str(run["run_id"] or "") != run_id
                or str(run["status"] or "") != "completed"
                or str(run["run_intent"] or "") != "release_nopublish"
                or str(run["issue_date"] or "") != expected_simulation_issue_date
                or str(run["manifest_id"] or "") != identity["manifestId"]
            ):
                raise RuntimeError(invalid_reason)
            stored_cwd = str(run["cwd"] or "")
            if not stored_cwd:
                raise RuntimeError(invalid_reason)
            if _canonical_path(
                Path(stored_cwd),
                strict=True,
                reason=invalid_reason,
            ) != artifact:
                raise RuntimeError(invalid_reason)

            daily_rows = conn.execute(
                "SELECT operation_id,operation_index,input_hash,handler_id,"
                "payload_json,receipt_json,receipt_hash "
                "FROM daily_operation_receipts WHERE run_id=? "
                "ORDER BY operation_index",
                (run_id,),
            ).fetchall()
            if len(daily_rows) != len(operations):
                raise RuntimeError(invalid_reason)

            for expected_index, (row, operation_id) in enumerate(zip(daily_rows, operations)):
                if (
                    str(row["operation_id"] or "") != operation_id
                    or int(row["operation_index"]) != expected_index
                ):
                    raise RuntimeError(invalid_reason)
                receipt = parse_mapping(row["receipt_json"])
                payload = parse_mapping(row["payload_json"])
                if (
                    receipt.get("run_id") != run_id
                    or receipt.get("operation_id") != operation_id
                    or receipt.get("operation_index") != expected_index
                    or receipt.get("ok") is not True
                    or receipt.get("status") != "completed"
                    or payload.get("run_id") != run_id
                    or payload.get("operation_id") != operation_id
                    or payload.get("operation_index") != expected_index
                    or payload.get("input_hash") != row["input_hash"]
                    or payload.get("handler_id") != row["handler_id"]
                    or str(row["receipt_hash"] or "")
                    != runtime_module._consumer_receipt_hash(receipt)
                    or not matches_saved_receipt(saved_receipts[operation_id], receipt)
                ):
                    raise RuntimeError(invalid_reason)

                producer = payload.get("producer_receipt")
                if not isinstance(producer, Mapping):
                    raise RuntimeError(invalid_reason)
                if operation_id == "external_publication":
                    if (
                        str(row["handler_id"] or "")
                        != "tools.news_grasp_release_nopublish.external_publication"
                        or producer.get("schemaVersion")
                        != "NEWS_GRASP_NOPUBLISH_EXTERNAL_RECEIPT_V1"
                        or producer.get("operation_id") != operation_id
                        or producer.get("producer_id")
                        != "tools.news_grasp_release_nopublish.external_publication"
                        or producer.get("no_publish") is not True
                        or type(producer.get("external_effect_count")) is not int
                        or producer.get("external_effect_count") != 0
                        or type(producer.get("adapter_call_count")) is not int
                        or producer.get("adapter_call_count") != 0
                    ):
                        raise RuntimeError(invalid_reason)
                if operation_id == "consumer_public_verification":
                    observation = producer.get("observation")
                    if not isinstance(observation, Mapping):
                        raise RuntimeError(invalid_reason)
                    if (
                        str(row["handler_id"] or "")
                        != "tools.news_grasp_release_nopublish.consumer_public_verification"
                        or producer.get("operation_id") != operation_id
                        or producer.get("producer_id")
                        != "tools.news_grasp_release_nopublish.consumer_public_verification"
                        or producer.get("external_operation_id")
                        != "release-nopublish-local-observation"
                        or observation.get("ok") is not True
                        or observation.get("status") != "verified"
                        or observation.get("mode")
                        != "consumer_owned_local_nopublish"
                        or type(observation.get("externalEffectCount")) is not int
                        or observation.get("externalEffectCount") != 0
                    ):
                        raise RuntimeError(invalid_reason)

            if str(daily_rows[-1]["operation_id"] or "") != operations[-1]:
                raise RuntimeError(invalid_reason)
            checkpoint = conn.execute(
                "SELECT issue_date,output_hash,validator_id,status,payload_json "
                "FROM daily_artifact_checkpoints "
                "WHERE run_id=? AND artifact_id='content_completion'",
                (run_id,),
            ).fetchone()
            if checkpoint is None or str(checkpoint["status"] or "") != "Green":
                raise RuntimeError(invalid_reason)
            content_payload = parse_mapping(checkpoint["payload_json"])
            if (
                str(checkpoint["issue_date"] or "") != expected_simulation_issue_date
                or str(checkpoint["validator_id"] or "")
                != "content_completion_artifact_hashes_v1"
                or str(checkpoint["output_hash"] or "")
                != runtime_module._consumer_receipt_hash(content_payload)
            ):
                raise RuntimeError(invalid_reason)
            try:
                content_module = importlib.import_module("tools.news_grasp_daily_content")
                content_module._validate_completion_payload(
                    artifact,
                    run_id=run_id,
                    issue_date=expected_simulation_issue_date,
                    value=content_payload,
                )
            except Exception as exc:  # noqa: BLE001 - saved Green is typed Red.
                raise RuntimeError(invalid_reason) from exc
            conn.rollback()
    except RuntimeError:
        raise
    except (OSError, sqlite3.DatabaseError, TypeError, ValueError) as exc:
        raise RuntimeError(invalid_reason) from exc

    result = dict(value)
    # 検証後の再提示は新しいmodel/quality生成を意味しない。
    result["modelCallCount"] = 0
    result["resumed"] = True
    return result


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    _reject_reparse_chain(path, reason="nopublish_output_reparse_rejected")
    path.parent.mkdir(parents=True, exist_ok=True)
    _reject_reparse_chain(path.parent, reason="nopublish_output_reparse_rejected")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_json_bytes(dict(value)) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _git(root: Path, *args: str) -> str:
    git_exe = Path(r"C:\Program Files\Git\cmd\git.exe")
    if not git_exe.is_file():
        raise RuntimeError("nopublish_git_executable_missing")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GCM_INTERACTIVE"] = "Never"
    completed = subprocess.run(
        [str(git_exe), *args],
        cwd=str(root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=30,
        check=False,
        shell=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        env=environment,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"nopublish_git_failed:{args[0]}:{completed.returncode}")
    return completed.stdout.strip().casefold()


def simulation_issue_date(source_issue_date: str) -> str:
    parsed = date.fromisoformat(source_issue_date)
    candidate = parsed + timedelta(days=1) if source_issue_date == PROTECTED_RELEASE else parsed
    return candidate.isoformat()


def _producer_receipt(
    schema: str,
    operation_id: str,
    *,
    values: Mapping[str, Any],
    failures: Sequence[str] = (),
) -> dict[str, Any]:
    failure_rows = [str(item) for item in failures if str(item)]
    body: dict[str, Any] = {
        "schemaVersion": schema,
        "ok": not failure_rows,
        "status": "verified" if not failure_rows else "red",
        "operation_id": operation_id,
        "producer_id": f"tools.news_grasp_release_nopublish.{operation_id}",
        "observed_at": datetime.now(JST).isoformat(),
        "failures": failure_rows,
        **dict(values),
    }
    body["output_hash"] = _sha(body)
    return body


def _scoped_release_receipt(**context: Any) -> dict[str, Any]:
    return _producer_receipt(
        "NEWS_GRASP_NOPUBLISH_SCOPED_RELEASE_RECEIPT_V1",
        "scoped_contract_unit",
        values={
            "mode": "release_promotion_and_isolation_reuse",
            "source_head": str(context.get("source_baseline") or ""),
            "test_process_count": 0,
            "isolation_receipt": str(context.get("isolation_receipt") or ""),
        },
    )


def _external_nopublish_receipt(**_context: Any) -> dict[str, Any]:
    return _producer_receipt(
        "NEWS_GRASP_NOPUBLISH_EXTERNAL_RECEIPT_V1",
        "external_publication",
        values={
            "no_publish": True,
            "external_effect_count": 0,
            "adapter_call_count": 0,
            "duplicate_send_count": 0,
            "duplicate_upload_count": 0,
        },
    )


def _materialize_local_bundle(
    *,
    repo_root: Path,
    issue_date: str,
    run_id: str,
    content_receipt: Mapping[str, Any],
    **context: Any,
) -> dict[str, Any]:
    artifact_hashes = {
        **dict(content_receipt.get("artifact_hashes") or {}),
        **dict(content_receipt.get("derived_artifact_hashes") or {}),
    }
    failures: list[str] = []
    for relative, expected in sorted(artifact_hashes.items()):
        path = (repo_root / str(relative)).resolve(strict=False)
        try:
            path.relative_to(repo_root)
        except ValueError:
            failures.append(f"artifact_outside_repo:{relative}")
            continue
        if not path.is_file() or path.is_symlink():
            failures.append(f"artifact_missing:{relative}")
            continue
        observed = hashlib.sha256(path.read_bytes()).hexdigest()
        if observed != str(expected):
            failures.append(f"artifact_hash_mismatch:{relative}")
    required_home = repo_root / "docs" / "index.html"
    if not required_home.is_file() or required_home.is_symlink():
        failures.append("manifest_home_missing")
    elif "docs/index.html" not in artifact_hashes:
        artifact_hashes["docs/index.html"] = hashlib.sha256(required_home.read_bytes()).hexdigest()
    exact_write_set = sorted(artifact_hashes)
    bundle_id = _sha(
        {
            "issue_date": issue_date,
            "run_id": run_id,
            "exact_write_set": exact_write_set,
            "artifact_hashes": artifact_hashes,
        }
    )
    publish_seal: Mapping[str, Any] = {}
    store = context.get("store")
    if not failures and isinstance(store, runtime.DirectRunStore):
        fresh = runtime.inspect_run(store, run_id=run_id)
        publish_seal = runtime.seal_publish(
            store,
            run_id=run_id,
            writer_lease=str(context.get("writer_lease") or ""),
            release_commit_sha=str(context.get("source_baseline") or fresh.get("source_baseline") or ""),
            exact_write_set=exact_write_set,
            file_hashes=artifact_hashes,
            manifest_id=str(fresh.get("manifest_id") or ""),
            bundle_id=bundle_id,
            external_operation_ids=(),
            external_input_hashes={},
            fencing_token=int(context.get("fencing_token") or 0),
        )
    return {
        "schemaVersion": "NEWS_GRASP_NOPUBLISH_LOCAL_BUNDLE_V1",
        "ok": not failures,
        "status": "sealed" if not failures else "red",
        "bundle_id": bundle_id,
        "issue_date": issue_date,
        "run_id": run_id,
        "exact_write_set": exact_write_set,
        "artifact_hashes": artifact_hashes,
        "external_effect_count": 0,
        "publish_seal": dict(publish_seal),
        "failures": failures,
    }


def _local_consumer_receipt(**context: Any) -> dict[str, Any]:
    store = context.get("store")
    run_id = str(context.get("run_id") or "")
    if not isinstance(store, runtime.DirectRunStore) or not run_id:
        raise RuntimeError("nopublish_consumer_runtime_binding_missing")
    integration = runtime.get_daily_operation_receipt(
        store,
        run_id=run_id,
        operation_id="current_issue_integration",
    )
    external = runtime.get_daily_operation_receipt(
        store,
        run_id=run_id,
        operation_id="external_publication",
    )
    if not isinstance(integration, Mapping) or not isinstance(external, Mapping):
        raise RuntimeError("nopublish_consumer_prior_receipt_missing")
    producer = integration.get("producer_receipt")
    producer = producer if isinstance(producer, Mapping) else {}
    content = producer.get("content_generation")
    content = content if isinstance(content, Mapping) else {}
    bundle = producer.get("release_bundle")
    bundle = bundle if isinstance(bundle, Mapping) else {}
    external_producer = external.get("producer_receipt")
    external_producer = external_producer if isinstance(external_producer, Mapping) else {}
    failures: list[str] = []
    if content.get("ok") is not True:
        failures.append("nopublish_content_receipt_red")
    if bundle.get("ok") is not True:
        failures.append("nopublish_bundle_receipt_red")
    if int(external_producer.get("external_effect_count", -1)) != 0:
        failures.append("nopublish_external_effect_detected")
    fresh = runtime.inspect_run(store, run_id=run_id)
    observed_at = datetime.now(JST).isoformat()
    nonce = uuid.uuid4().hex
    binding = {
        "runId": run_id,
        "issueDate": str(context.get("issue_date") or ""),
        "runIntent": str(context.get("run_intent") or runtime.RUN_INTENT),
        "generation": fresh.get("generation"),
        "manifestId": str(fresh.get("manifest_id") or ""),
        "fencingBindingHash": runtime.fencing_binding_hash(
            run_id=run_id,
            generation=int(fresh.get("generation") or 0),
            writer_lease=str(context.get("writer_lease") or ""),
            fencing_token=int(context.get("fencing_token") or 0),
        ),
        "updatedAt": str(fresh.get("updated_at") or ""),
        "observedAt": observed_at,
        "observationNonce": nonce,
    }
    observation = {
        "ok": not failures,
        "status": "verified" if not failures else "red",
        "observationToken": nonce,
        "observedAt": observed_at,
        "mode": "consumer_owned_local_nopublish",
        "bundleId": str(bundle.get("bundle_id") or ""),
        "artifactCount": len(bundle.get("artifact_hashes") or {}),
        "externalEffectCount": 0,
        "failures": failures,
    }
    return _producer_receipt(
        runtime.CONSUMER_PUBLIC_VERIFICATION_RECEIPT_SCHEMA,
        "consumer_public_verification",
        values={
            "observation": observation,
            "observation_token": nonce,
            "external_operation_id": "release-nopublish-local-observation",
            "freshnessBinding": binding,
        },
        failures=failures,
    )


def _run_release_nopublish_core(
    *,
    repo_root: Path,
    source_issue_date: str,
    state_root: Path,
    isolation_receipt: Path,
    run_identity: Mapping[str, Any] | None = None,
    entry_context: _LocalEntryContext | None = None,
) -> dict[str, Any]:
    _load_release_runtime_modules()
    context = _require_local_entry_context(
        entry_context,
        repo_root=repo_root,
        source_issue_date=source_issue_date,
        state_root=state_root,
        isolation_receipt=isolation_receipt,
    )
    root = repo_root.resolve(strict=True)
    isolated_state = _canonical_path(
        state_root,
        strict=False,
        reason="nopublish_release_state_reparse_rejected",
    )
    canonical_state = _canonical_release_state_root()
    if isolated_state != canonical_state:
        raise ValueError("nopublish_state_root_not_canonical")
    resolved_isolation_receipt = _canonical_path(
        isolation_receipt,
        strict=True,
        reason="nopublish_isolation_receipt_reparse_rejected",
    )
    if not resolved_isolation_receipt.is_file():
        raise ValueError("nopublish_isolation_receipt_missing")
    identity = _bound_run_identity(
        context.run_identity,
        source_issue_date=source_issue_date,
    )
    if run_identity is not None and dict(run_identity) != identity:
        raise RuntimeError("nopublish_run_identity_binding_drift")
    source_head = identity["sourceHead"]
    simulation_date = identity["simulationIssueDate"]
    manifest_id = identity["manifestId"]
    store = runtime.DirectRunStore(
        isolated_state,
        test_only_allow_semantic_verifier=True,
    )
    from tools import news_grasp_daily_content as content

    handlers = {
        "scoped_contract_unit": (
            "tools.news_grasp_release_nopublish.scoped_contract_unit",
            _scoped_release_receipt,
        ),
        "external_publication": (
            "tools.news_grasp_release_nopublish.external_publication",
            _external_nopublish_receipt,
        ),
        "consumer_public_verification": (
            "tools.news_grasp_release_nopublish.consumer_public_verification",
            _local_consumer_receipt,
        ),
    }
    receipts = daily.run_daily_sequence(
        handlers=handlers,
        store=store,
        cwd=root,
        issue_date=simulation_date,
        run_intent="release_nopublish",
        automation_id="news-grasp-release-gate",
        scheduler_trigger_at=datetime.now(JST).isoformat(),
        manifest_id=manifest_id,
        source_baseline=source_head,
        runtime_generation=f"release-nopublish:{source_head}",
        remote_base_sha=source_head,
        allowed_side_effect_ids=(),
        context={
            "repo_root": root,
            "source_baseline": source_head,
            "isolation_receipt": str(resolved_isolation_receipt),
            "content_candidate_provider": content._default_candidate_provider,
            "content_model_runner": content._default_model_runner,
            "content_derived_builder": content._default_derived_builder,
            "content_release_materializer": _materialize_local_bundle,
        },
    )
    final = receipts[-1] if receipts else {}
    external_effect_count = 0
    result = {
        "schemaVersion": SCHEMA,
        "ok": (
            len(receipts) == len(daily.DAILY_OPERATIONS)
            and final.get("ok") is True
            and final.get("status") == "completed"
        ),
        "status": "publish_dry_run_ok" if final.get("ok") is True and final.get("status") == "completed" else "red",
        "source_issue_date": source_issue_date,
        "simulation_issue_date": simulation_date,
        "source_head": source_head,
        "run_id": str((receipts[0] if receipts else {}).get("run_id") or ""),
        "operation_count": len(receipts),
        "operation_ids": [str(row.get("operation_id") or "") for row in receipts],
        "externalEffectCount": external_effect_count,
        "duplicateSendCount": 0,
        "duplicateUploadCount": 0,
        "failures": list(final.get("failures") or ()),
        "receipts": receipts,
    }
    result["receiptSha256"] = _sha(result)
    return result


def run_release_nopublish(
    *,
    repo_root: Path,
    source_issue_date: str,
    state_root: Path,
    isolation_receipt: Path,
    capability: _ReleaseCapability | None = None,
    run_identity: Mapping[str, Any] | None = None,
    entry_context: _LocalEntryContext | None = None,
) -> dict[str, Any]:
    """旧Python公開APIを廃止し、製品内CLIだけを実行入口にする。"""

    del (
        repo_root,
        source_issue_date,
        state_root,
        isolation_receipt,
        capability,
        run_identity,
        entry_context,
    )
    raise RuntimeError("nopublish_public_api_retired_use_cli")


def _entry_issue_date(argv: Sequence[str]) -> str:
    """引数検証前の観測日を得る。実行許可やrun identityには使わない。"""

    observed = datetime.now(JST).date().isoformat()
    for index, argument in enumerate(argv):
        candidate = None
        if argument == "--source-issue-date" and index + 1 < len(argv):
            candidate = argv[index + 1]
        elif argument.startswith("--source-issue-date="):
            candidate = argument.partition("=")[2]
        if candidate is not None:
            try:
                observed = date.fromisoformat(candidate).isoformat()
            except ValueError:
                pass
    return observed


def _main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    entry_issue_date = _entry_issue_date(arguments)
    session_id = uuid.uuid4().hex
    canonical_state = _canonical_release_state_root()
    journals = _journal_set(canonical_state)
    _append_journal_event(
        journals,
        issue_date=entry_issue_date,
        session_id=session_id,
        phase="module_loaded",
        detail={
            "pid": os.getpid(),
            "parentPid": os.getppid(),
            "modulePath": str(Path(__file__).resolve()),
        },
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--source-issue-date", required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--receipt-path", type=Path, required=True)
    parser.add_argument("--isolation-receipt", type=Path, required=True)
    try:
        args = parser.parse_args(arguments)
    except SystemExit as exc:
        _append_journal_event(
            journals,
            issue_date=entry_issue_date,
            session_id=session_id,
            phase="preentry_failed" if exc.code else "terminal",
            detail={"exitCode": int(exc.code or 0), "reasonCode": "argument_parser_exit"},
        )
        raise
    state_file: Path | None = None
    receipt_path: Path | None = None
    issue_date = str(args.source_issue_date or "")
    module_started = False
    execution_started = False
    e2e_attempt_consumed = 0
    e2e_attempt_count = 0
    run_identity: dict[str, str] | None = None
    result: dict[str, Any]
    try:
        date.fromisoformat(issue_date)
        root = _canonical_path(
            args.repo_root,
            strict=True,
            reason="nopublish_repo_root_invalid",
        )
        state_file, receipt_path = _validate_diagnostic_paths(
            repo_root=root,
            canonical_state=canonical_state,
            state_file=args.state_file,
            receipt_path=args.receipt_path,
            isolation_receipt=args.isolation_receipt,
        )
        _load_release_runtime_modules()
        _append_journal_event(
            journals,
            issue_date=issue_date,
            session_id=session_id,
            phase="preentry_started",
            detail={
                "invocationId": session_id,
                "repoRoot": str(root),
                "modulePath": str(Path(__file__).resolve()),
            },
        )
        isolation = _validate_isolation_receipt(
            repo_root=root,
            source_issue_date=issue_date,
            isolation_receipt=args.isolation_receipt,
        )
        canonical_journal = journals[0]
        binding = canonical_journal.binding(issue_date)
        if binding is None:
            run_identity = _initial_run_identity(
                repo_root=root,
                source_issue_date=issue_date,
                isolation_receipt=Path(str(isolation.get("receiptPath") or args.isolation_receipt)),
            )
            binding = canonical_journal.bind(
                issue_date,
                {
                    "issueDate": issue_date,
                    "artifactRoot": str(root),
                    "stateRoot": str(canonical_state),
                    "isolationReceipt": str(isolation.get("receiptPath") or ""),
                    "resultPath": str(_canonical_result_path(canonical_state, issue_date)),
                    "runIdentity": run_identity,
                },
            )
        run_identity = _bound_run_identity(
            binding.get("runIdentity"),
            source_issue_date=issue_date,
        )
        if binding.get("issueDate") != issue_date:
            raise RuntimeError("nopublish_release_state_binding_invalid")
        bound_state = _canonical_path(
            Path(str(binding.get("stateRoot") or "")),
            strict=False,
            reason="nopublish_release_state_binding_invalid",
        )
        if bound_state != canonical_state:
            raise RuntimeError("nopublish_release_state_binding_invalid")
        artifact_root = _canonical_path(
            Path(str(binding.get("artifactRoot") or "")),
            strict=True,
            reason="nopublish_recovery_required",
        )
        result_path = _canonical_path(
            Path(str(binding.get("resultPath") or _canonical_result_path(canonical_state, issue_date))),
            strict=False,
            reason="nopublish_saved_result_path_invalid",
        )
        if result_path != _canonical_result_path(canonical_state, issue_date):
            raise RuntimeError("nopublish_saved_result_path_invalid")
        _append_journal_event(
            journals,
            issue_date=issue_date,
            session_id=session_id,
            phase="local_claim",
            detail={
                "invocationId": session_id,
                "issueDate": issue_date,
                "artifactRoot": str(artifact_root),
                "stateRoot": str(canonical_state),
            },
        )
        with runtime.daily_process_mutex(timeout_ms=0):
            e2e_attempt_count = _journal_module_start_count(canonical_journal, issue_date)
            saved = _saved_green_result(
                result_path,
                artifact_root=artifact_root,
                canonical_state=canonical_state,
                source_issue_date=issue_date,
                run_identity=run_identity,
            )
            if saved is not None:
                _append_journal_event(
                    journals,
                    issue_date=issue_date,
                    session_id=session_id,
                    phase="resume",
                    detail={
                        "invocationId": session_id,
                        "runId": str(saved.get("run_id") or saved.get("runId") or ""),
                        "resultPath": str(result_path),
                    },
                )
                result = saved
                e2e_attempt_consumed = 0
                e2e_attempt_count = _journal_module_start_count(canonical_journal, issue_date)
            else:
                observed = _process_identity(os.getpid())
                observation_detail = {
                    "invocationId": session_id,
                    "processIdentity": observed,
                    "modulePath": str(Path(__file__).resolve()),
                }
                _append_journal_event(
                    journals,
                    issue_date=issue_date,
                    session_id=session_id,
                    phase="os_observed",
                    detail=observation_detail,
                )
                _append_journal_event(
                    journals,
                    issue_date=issue_date,
                    session_id=session_id,
                    phase="module_entered",
                    detail=observation_detail,
                )
                prior_started = _journal_module_start_count(canonical_journal, issue_date) > 0
                if prior_started:
                    execution_started = True
                    e2e_attempt_count = _journal_module_start_count(canonical_journal, issue_date)
                    _append_journal_event(
                        journals,
                        issue_date=issue_date,
                        session_id=session_id,
                        phase="resume",
                        detail={
                            "invocationId": session_id,
                            "artifactRoot": str(artifact_root),
                            "stateRoot": str(canonical_state),
                        },
                    )
                else:
                    _append_journal_event(
                        journals,
                        issue_date=issue_date,
                        session_id=session_id,
                        phase="module_started",
                        detail=observation_detail,
                    )
                    module_started = True
                    execution_started = True
                    e2e_attempt_consumed = 1
                    e2e_attempt_count = 1
                effective_receipt = Path(
                    str(binding.get("isolationReceipt") or isolation.get("receiptPath") or args.isolation_receipt)
                )
                if not effective_receipt.is_file():
                    effective_receipt = Path(str(isolation.get("receiptPath") or args.isolation_receipt))
                entry_context = _LocalEntryContext(
                    artifact_root=artifact_root,
                    isolation_receipt=effective_receipt,
                    run_identity=run_identity,
                    source_issue_date=issue_date,
                    state_root=canonical_state,
                    marker=_LOCAL_ENTRY_CONTEXT_MARKER,
                )
                result = _run_release_nopublish_core(
                    repo_root=artifact_root,
                    source_issue_date=issue_date,
                    state_root=canonical_state,
                    isolation_receipt=effective_receipt,
                    run_identity=run_identity,
                    entry_context=entry_context,
                )
                result["e2eAttemptConsumed"] = e2e_attempt_consumed
                result["e2eAttemptCount"] = e2e_attempt_count
                if result.get("ok") is True and result.get("status") == "publish_dry_run_ok":
                    result["receiptSha256"] = _sha(
                        {key: item for key, item in result.items() if key != "receiptSha256"}
                    )
                    _atomic_json(result_path, result)
        result["e2eAttemptConsumed"] = e2e_attempt_consumed
        result["e2eAttemptCount"] = e2e_attempt_count
        result["receiptSha256"] = _sha(
            {key: item for key, item in result.items() if key != "receiptSha256"}
        )
        _append_journal_event(
            journals,
            issue_date=issue_date,
            session_id=session_id,
            phase="terminal",
            detail={
                "invocationId": session_id,
                "exitCode": 0 if result.get("ok") is True else 1,
                "ok": result.get("ok") is True,
                "runId": str(result.get("run_id") or result.get("runId") or ""),
            },
        )
    except Exception as exc:  # noqa: BLE001 - machine boundary is typed Red.
        if journals:
            try:
                e2e_attempt_count = _journal_module_start_count(journals[0], issue_date)
                execution_started = execution_started or e2e_attempt_count > 0
            except Exception:
                pass
        result = {
            "schemaVersion": SCHEMA,
            "ok": False,
            "status": "red",
            "externalEffectCount": 0,
            "e2eAttemptConsumed": e2e_attempt_consumed,
            "e2eAttemptCount": e2e_attempt_count,
            "failures": [f"release_nopublish_error:{type(exc).__name__}:{exc}"],
        }
        if journals:
            phase = "terminal" if execution_started else "preentry_failed"
            try:
                _append_journal_event(
                    journals,
                    issue_date=entry_issue_date,
                    session_id=session_id,
                    phase=phase,
                    detail={
                        "invocationId": session_id,
                        "exitCode": 1,
                        "reasonCode": str(exc)[:240],
                    },
                )
            except Exception:
                pass
    state = {
        "schemaVersion": STATE_SCHEMA,
        "status": "publish_dry_run_ok" if result.get("ok") is True else "release_nopublish_red",
        "exit_code": 0 if result.get("ok") is True else 1,
        "externalEffectCount": int(result.get("externalEffectCount") or 0),
        "e2eAttemptConsumed": int(result.get("e2eAttemptConsumed") or 0),
        "e2eAttemptCount": int(e2e_attempt_count),
        "canonicalStateRoot": str(canonical_state or ""),
        "diagnosticStateRoot": str(args.state_root),
        "receiptPath": str(receipt_path or ""),
    }
    if (execution_started or result.get("ok") is True) and state_file is not None and receipt_path is not None:
        try:
            _atomic_json(receipt_path, result)
            _atomic_json(state_file, state)
        except Exception as exc:  # noqa: BLE001 - output boundary is typed Red.
            result = {
                "schemaVersion": SCHEMA,
                "ok": False,
                "status": "red",
                "externalEffectCount": 0,
                "e2eAttemptConsumed": e2e_attempt_consumed,
                "e2eAttemptCount": e2e_attempt_count,
                "failures": [f"release_nopublish_output_error:{type(exc).__name__}:{exc}"],
            }
            state["status"] = "release_nopublish_red"
            state["exit_code"] = 1
    sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    if result.get("ok") is not True:
        sys.stderr.write(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return int(state["exit_code"])


if __name__ == "__main__":
    raise SystemExit(_main())
