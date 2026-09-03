"""News-Grasp Daily六operationを同一process・同一writerで完走する入口。"""

from __future__ import annotations

import os
import sys
import ctypes
import uuid
from pathlib import Path
from typing import Any, Sequence

from tools import news_grasp_daily_gate as daily
from tools import news_grasp_direct_runtime as runtime


SEQUENCE_SCHEMA = "NEWS_GRASP_DAILY_SEQUENCE_RECEIPT_V1"
PROTECTED_RELEASE = daily.PROTECTED_RELEASE
PROTECTED_RELEASE_POLICY = daily.PROTECTED_RELEASE_POLICY


def _canonical_daily_state_root() -> Path:
    """Windows Known FolderからDaily唯一のproduction state rootを解決する。

    Daily launcherはRelease gateをimportしない。これによりscheduled call graphへ
    Release-only moduleを混入させず、caller環境変数によるstate root差替えも拒否する。
    """

    if os.name != "nt":
        raise OSError("daily_windows_known_folder_required")
    class _Guid(ctypes.Structure):
        _fields_ = [
            ("data1", ctypes.c_uint32),
            ("data2", ctypes.c_uint16),
            ("data3", ctypes.c_uint16),
            ("data4", ctypes.c_ubyte * 8),
        ]

    folder_id = _Guid.from_buffer_copy(
        uuid.UUID("f1b32785-6fba-4fcf-9d55-7b8e7f157091").bytes_le
    )
    output = ctypes.c_wchar_p()
    status = ctypes.windll.shell32.SHGetKnownFolderPath(
        ctypes.byref(folder_id),
        0,
        None,
        ctypes.byref(output),
    )
    if status != 0 or not output.value:
        raise OSError(f"daily_known_folder_unavailable:{status}")
    try:
        return (Path(output.value) / "News-Grasp" / "direct-mainline").resolve(strict=False)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(output)


def _contains_writer_capability(value: Any) -> bool:
    """machine receiptへwriter capabilityのkeyを一つも投影しない。"""

    if isinstance(value, dict):
        for key, nested in value.items():
            canonical_key = str(key).replace("_", "").casefold()
            if canonical_key in {"writerlease", "fencingtoken", "continuationcapability"}:
                return True
            if _contains_writer_capability(nested):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_writer_capability(item) for item in value)
    return False


def _red(reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "schemaVersion": SEQUENCE_SCHEMA,
        "ok": False,
        "status": "red",
        "failures": [reason],
        "humanImpact": {
            "noFocusTheft": True,
            "noAutoOpen": True,
            "noUserMonitoring": True,
        },
        **extra,
    }


def _protected_release_preflight(*, repo_root: Path, issue_date: str) -> dict[str, Any] | None:
    """通常Dailyから保護済みreleaseを再実行する経路をrun作成前に閉じる。"""

    failure = daily.protected_release_failure(
        repo_root=repo_root,
        issue_date=issue_date,
        require_contract_integrity=True,
    )
    if failure:
        return _red(
            failure,
            issue_date=issue_date,
            protected_release=PROTECTED_RELEASE,
            protected_release_policy=PROTECTED_RELEASE_POLICY,
            exact_successor="explicit_new_release_authority_required",
        )
    return None


def run_sequence(*, repo_root: Path, state_root: Path, issue_date: str, scheduler_trigger_at: str) -> dict[str, Any]:
    protected = _protected_release_preflight(repo_root=repo_root, issue_date=issue_date)
    if protected is not None:
        return protected
    identity = daily.resolve_daily_identity_context(repo_root=repo_root, issue_date=issue_date)
    if identity.get("ok") is not True:
        return _red("daily_identity_preflight_red", identity=identity)
    store = runtime.DirectRunStore(state_root)
    receipts = daily.run_daily_sequence(
        store=store,
        cwd=repo_root,
        issue_date=issue_date,
        run_intent=runtime.RUN_INTENT,
        automation_id=runtime.AUTOMATION_ID,
        scheduler_trigger_at=scheduler_trigger_at,
        manifest_id=str(identity.get("manifest_id") or ""),
        manifest_reservation_id=str(identity.get("manifest_reservation_id") or ""),
        source_baseline=str(identity.get("source_baseline") or ""),
        runtime_generation=runtime.RUNTIME_SCHEMA_V2,
        remote_base_sha=str(identity.get("remote_base_sha") or ""),
        allowed_side_effect_ids=list(identity.get("allowed_side_effect_ids") or ()),
    )
    final = receipts[-1] if receipts else {}
    result = {
        "schemaVersion": SEQUENCE_SCHEMA,
        "ok": len(receipts) == len(daily.DAILY_OPERATIONS) and final.get("ok") is True and final.get("status") == "completed",
        "status": final.get("status") or "red",
        "run_id": str((receipts[0] if receipts else {}).get("run_id") or ""),
        "operation_count": len(receipts),
        "operation_receipts": receipts,
        "failures": list(final.get("failures") or ()),
        "humanImpact": {
            "noFocusTheft": True,
            "noAutoOpen": True,
            "noUserMonitoring": True,
        },
    }
    # writer leaseとfencing capabilityはprocess memoryから外へ出さない。
    if _contains_writer_capability(result):
        return _red("daily_writer_capability_projection_violation")
    return result


def _main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        runtime._emit_cli(_red("daily_sequence_argv_forbidden"))
        return 2
    if os.path.normcase(os.path.abspath(sys.executable)) != os.path.normcase(
        os.path.abspath(daily.DAILY_PYTHON)
    ):
        runtime._emit_cli(
            _red(
                "fixed_python_3_12_required",
                expected_python=daily.DAILY_PYTHON,
                observed_python=sys.executable,
            )
        )
        return 2
    issue_date = os.environ.get("NEWS_GRASP_ISSUE_DATE", "").strip() or daily._issue_date_default()
    repo_root = Path(os.environ.get("NEWS_GRASP_REPO_ROOT", str(Path.cwd())))
    # production single-flight stateはWindows Known Folderの一箇所だけを使う。
    # NEWS_GRASP_STATE_ROOTはtest APIのrun_sequence引数以外ではauthorityにしない。
    state_root = _canonical_daily_state_root()
    scheduler_trigger_at = (
        os.environ.get("NEWS_GRASP_SCHEDULER_TRIGGER_AT", "").strip()
        or f"{issue_date}T06:00:00+09:00"
    )
    try:
        result = run_sequence(
            repo_root=repo_root,
            state_root=state_root,
            issue_date=issue_date,
            scheduler_trigger_at=scheduler_trigger_at,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        result = _red(f"daily_sequence_error:{type(exc).__name__}:{exc}")
    runtime._emit_cli(result)
    return 0 if result.get("ok") is True and result.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(_main())
