"""News-Grasp clean-room value review: sealed ExpectedRed oracles.

This file intentionally exercises only the five highest-value gaps selected for
the first clean-room review turn.  It is a value suite: each assertion names
the operational boundary which must be true before a later Green claim.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import importlib
from importlib.machinery import SourceFileLoader
import importlib.util
import inspect
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping

import pytest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_PATH = ROOT / "scripts" / "ops" / "news-grasp-task-launcher.pyw"
INSTALLER_PATH = ROOT / "scripts" / "ops" / "install-news-grasp-ops.ps1"
BOOTSTRAP_PATH = ROOT / "scripts" / "ops" / "news-grasp-bootstrap.ps1"
OBSERVED_AT = datetime(2026, 8, 22, 6, 0, tzinfo=timezone(timedelta(hours=9)))
SCHEDULE_ID = "news-grasp-daily-v1"
INTENT = "reconcile"


def _load_launcher() -> Any:
    loader = SourceFileLoader(
        "news_grasp_task_launcher_value_review", str(LAUNCHER_PATH)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _decision() -> dict[str, Any]:
    return {
        "status": "ACQUIRED",
        "ownerDisposition": "ACQUIRED",
        "slotKind": "Scheduled",
        "slotKey": f"{SCHEDULE_ID}/2026-08-22/Scheduled",
        "writerKey": "writer-scheduled",
        "ownerKey": "writer-scheduled",
        "fenceToken": 7,
    }


class _Controller:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def reconcile(self, **_kwargs: Any) -> dict[str, Any]:
        self.events.append("reconcile")
        return _decision()

    def commit_slot(self, **_kwargs: Any) -> dict[str, Any]:
        self.events.append("commit")
        return {"status": "committed"}


def _run_dispatch(
    helper: Any,
    bin_dir: Path,
    events: list[str],
    *,
    validator: Any = None,
) -> tuple[Any, dict[str, int], list[str]]:
    factory_calls = {"count": 0}
    child_calls: list[str] = []

    def factory(**_kwargs: Any) -> _Controller:
        factory_calls["count"] += 1
        events.append("controller_factory")
        return _Controller(events)

    def child(*_args: Any, **_kwargs: Any) -> int:
        child_calls.append("child")
        events.append("child")
        return 0

    result = helper(
        SCHEDULE_ID,
        INTENT,
        bin_dir=bin_dir,
        observed_at=OBSERVED_AT,
        controller_factory=factory,
        child_runner=child,
        task_context_validator=validator,
    )
    return result, factory_calls, child_calls


def test_r1_task_origin_witness_rejects_before_controller_wal_ledger_child(
    tmp_path: Path,
) -> None:
    """Omitted scheduler-origin witness must fail closed before every side effect."""
    launcher = _load_launcher()
    helper = launcher.run_cleanroom_dispatch
    events: list[str] = []
    result, factory_calls, child_calls = _run_dispatch(
        helper, tmp_path / "bin", events, validator=None
    )
    failures: list[str] = []
    if factory_calls["count"] or events or child_calls or result == 0:
        failures.append(
            "missing task-scheduler origin witness reached controller/child/commit: "
            f"result={result!r}, events={events!r}"
        )
    context_command = " ".join(launcher._cleanroom_context_powershell_command())
    if not any(
        marker in context_command
        for marker in ("ParentProcessId", "ParentProcessID", "Win32_Process")
    ):
        failures.append(
            "default Task context oracle checks Running state but no OS Task Scheduler parent"
        )
    assert not failures, "R1 ExpectedRed: " + " | ".join(failures)


def test_r3_task_origin_canary_is_real_task_start_and_fail_closed_unverified() -> None:
    """The canary must start the installed Task, prove its nonce, restore, and commit."""
    launcher = _load_launcher()
    launcher_source = LAUNCHER_PATH.read_text(encoding="utf-8")
    installer_source = INSTALLER_PATH.read_text(encoding="utf-8")
    bootstrap_source = BOOTSTRAP_PATH.read_text(encoding="utf-8")
    failures: list[str] = []

    candidate_names = (
        "run_task_origin_canary",
        "run_task_startup_canary",
        "run_cleanroom_task_origin_canary",
        "run_task_origin_entry_canary",
    )
    canary = next(
        (getattr(launcher, name, None) for name in candidate_names),
        None,
    )
    if not callable(canary):
        failures.append("launcher has no task-origin canary callable")
    else:
        parameter_names = set(inspect.signature(canary).parameters)
        required_groups = {
            "Task action/start seam": {"task_action", "start_task", "action"},
            "nonce receipt/wait seam": {"nonce", "wait_receipt", "receipt"},
            "restore seam": {"restore_task", "restore", "restore_action"},
            "final parity seam": {"final_parity", "parity", "verify_parity"},
        }
        for label, alternatives in required_groups.items():
            if not parameter_names.intersection(alternatives):
                failures.append(f"canary lacks {label}: {sorted(parameter_names)}")

    launcher_lower = launcher_source.casefold()
    installer_lower = installer_source.casefold()
    bootstrap_lower = bootstrap_source.casefold()
    source_groups = {
        "launcher Task-origin guard": ("task-origin" in launcher_lower or "task_origin" in launcher_lower),
        "launcher isolated Controller/ledger": (
            "news_grasp_cleanroom_controller" in launcher_lower
            and "ledger" in launcher_lower
        ),
        "launcher terminal commit": "commit_slot" in launcher_lower,
        "installer temporary Task start": "start-scheduledtask" in installer_lower,
        "installer nonce receipt": "nonce" in installer_lower and "receipt" in installer_lower,
        "canonical 11-token canary interface": (
            all(
                marker in launcher_lower
                for marker in (
                    "task-origin-canary",
                    "--canary-nonce",
                    "--canary-generation",
                    "--canary-receipt-path",
                )
            )
            and "len(arguments) != 11" in launcher_lower
            and all(
                marker in installer_lower
                for marker in (
                    "task-origin-canary --canary-nonce",
                    "--canary-generation",
                    "--canary-receipt-path",
                )
            )
            and "task-origin-canary --nonce" not in installer_lower
            and '"task-origin-canary", "--nonce"' not in launcher_lower
            and '"task-origin-canary", "--generation"' not in launcher_lower
        ),
        "installer restore/final parity": (
            ("restore" in installer_lower or "set-scheduledtask" in installer_lower)
            and ("parity" in installer_lower or "final" in installer_lower)
        ),
        "bootstrap SmokeTest child": "smoketest" in bootstrap_lower,
    }
    failures.extend(label for label, present in source_groups.items() if not present)
    assert not failures, "R3 ExpectedRed: " + " | ".join(failures)


def test_r4_missing_or_invalid_stable_authority_has_zero_runtime_effects(
    tmp_path: Path,
) -> None:
    """Stable authority is an admission gate, not an optional post-controller hint."""
    launcher = _load_launcher()
    helper = launcher.run_cleanroom_dispatch
    failures: list[str] = []
    for case, authority_bytes in (("missing", None), ("invalid", b"{}\n")):
        bin_dir = tmp_path / case
        bin_dir.mkdir()
        if authority_bytes is not None:
            (bin_dir / "news-grasp-stable-task-authority-v1.json").write_bytes(
                authority_bytes
            )
        events: list[str] = []
        result, factory_calls, child_calls = _run_dispatch(
            helper,
            bin_dir,
            events,
            validator=lambda **_kwargs: True,
        )
        if factory_calls["count"] or child_calls or events or result == 0:
            failures.append(
                f"{case} authority reached runtime: result={result!r}, events={events!r}"
            )
    assert not failures, "R4 ExpectedRed: " + " | ".join(failures)


def test_r5_task_pythonw_path_is_exact_binding_value_without_normalization(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Binding taskPythonwPath is authoritative even when it is not python.exe's sibling."""
    import tools.daily_self_heal as dsh

    live_bin = tmp_path / "bin"
    live_bin.mkdir()
    binding_path = live_bin / "news-grasp-high-cost-binding-v1.json"
    recovery_path = live_bin / "news-grasp-recovery-runtime-binding-v1.json"
    python_exe = live_bin / "python.exe"
    bound_pythonw = live_bin / "bound-pythonw.exe"
    python_exe.write_bytes(b"python-fixture")
    bound_pythonw.write_bytes(b"bound-pythonw-fixture")
    receipt = "a" * 64
    binding_raw = b'{"binding":"fixture"}\n'
    binding = {
        "schemaVersion": "NEWS_GRASP_HIGH_COST_BINDING_V1",
        "bindingReceiptSha256": receipt,
    }
    recovery = {
        "schemaVersion": "NEWS_GRASP_RECOVERY_RUNTIME_BINDING_V1",
        "highCostBindingPath": str(binding_path.resolve()),
        "highCostBindingReceiptSha256": receipt,
        "highCostBindingFileSha256": hashlib.sha256(binding_raw).hexdigest(),
        "pythonExe": str(python_exe.resolve()),
        "pythonExeSha256": hashlib.sha256(python_exe.read_bytes()).hexdigest(),
        "taskPythonwPath": str(bound_pythonw.resolve()),
        "taskPythonwSha256": hashlib.sha256(bound_pythonw.read_bytes()).hexdigest(),
        "pythonTrustAnchor": "authenticode:python-software-foundation",
        "pythonSignerSubject": "CN=Python Software Foundation, O=Python Software Foundation, fixture",
        "pythonSignerThumbprint": "d" * 40,
        "pythonwTrustAnchor": "authenticode:python-software-foundation",
        "pythonwSignerSubject": "CN=Python Software Foundation, O=Python Software Foundation, fixture",
        "pythonwSignerThumbprint": "d" * 40,
        "opsRepoRoot": str(tmp_path.resolve()),
        "opsHead": "b" * 40,
        "trustedRemote": "https://github.com/HIDEPON-UMG/News-Grasp.git",
        "dailySelfHealPath": str((tmp_path / "tools" / "daily_self_heal.py").resolve()),
        "dailySelfHealSha256": "c" * 64,
    }
    binding_path.write_bytes(b"placeholder\n")
    recovery_path.write_text(json.dumps(recovery) + "\n", encoding="utf-8")

    def fake_json(path: Path, *, expected: Path) -> tuple[dict[str, Any], bytes]:
        if path == binding_path:
            return binding, binding_raw
        assert path == recovery_path == expected
        return recovery, b"recovery\n"

    def fake_bytes(path: Path, **_kwargs: Any) -> bytes:
        return python_exe.read_bytes() if path == python_exe else bound_pythonw.read_bytes()

    monkeypatch.setattr(dsh, "_canonical_live_json", fake_json)
    monkeypatch.setattr(dsh, "_canonical_file_bytes", fake_bytes)
    monkeypatch.setattr(
        dsh,
        "_authenticode_identity",
        lambda _path, **_kwargs: {
            "status": "Valid",
            "subject": "CN=Python Software Foundation, O=Python Software Foundation, fixture",
            "thumbprint": "d" * 40,
        },
    )
    monkeypatch.setattr(
        dsh,
        "_trusted_ops_generation",
        lambda _root: {
            "head": "b" * 40,
            "remote": "https://github.com/HIDEPON-UMG/News-Grasp.git",
            "daily_self_heal_sha256": "c" * 64,
        },
    )

    result = dsh._validate_live_high_cost_binding_files(
        live_bin_root=live_bin,
        binding_path=binding_path,
        binding_receipt_sha256=receipt,
        ops_repo_root=tmp_path,
    )
    assert result.get("ok") is True, result
    assert Path(str(result.get("task_pythonw_path"))).resolve() == bound_pythonw.resolve(), result


def test_r8_startup_canary_is_bounded_and_run_canary_false_is_not_green(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Child startup captures output/bounds time and readiness cannot bless a skipped canary."""
    launcher = _load_launcher()
    run_kwargs: dict[str, Any] = {}

    class _Completed:
        returncode = 0
        stdout = b""
        stderr = b""
        timed_out = False
        output_exceeded = False

    class _OwnedProcessModule:
        @staticmethod
        def run_owned_bounded(_command: list[str], **kwargs: Any) -> _Completed:
            run_kwargs.update(kwargs)
            return _Completed()

    def fake_load_module(_path: Path, *, prefix: str) -> _OwnedProcessModule:
        assert prefix == "news_grasp_owned_process_runtime"
        return _OwnedProcessModule()

    def fake_run(_command: list[str], **kwargs: Any) -> _Completed:
        run_kwargs.update(kwargs)
        return _Completed()

    monkeypatch.setattr(launcher.subprocess, "run", fake_run)
    monkeypatch.setattr(launcher, "_load_module_from_exact_path", fake_load_module)
    assert (
        launcher._run_cleanroom_child(
            "runner",
            ["runner", "--smoke"],
            bin_dir=tmp_path,
            safety={
                "creationflags": 0,
                "owned_process_module": str(LAUNCHER_PATH),
            },
        )
        == 0
    )
    failures: list[str] = []
    if not isinstance(run_kwargs.get("timeout"), (int, float)):
        failures.append("startup timeout missing")
    if not isinstance(run_kwargs.get("max_output_bytes"), int):
        failures.append("startup output bound missing")
    if Path(run_kwargs.get("cwd", "")).resolve() != tmp_path.resolve():
        failures.append("startup cwd is not exact")

    dsh = importlib.import_module("tools.daily_self_heal")
    monkeypatch.setattr(dsh, "compare_files", lambda *_args, **_kwargs: {
        "repo_exists": True,
        "live_exists": True,
        "synced": True,
        "repo_sha256": "a" * 64,
        "live_sha256": "a" * 64,
    })
    monkeypatch.setattr(dsh, "_task_launcher_source_contract", lambda _path: {"ok": True})
    monkeypatch.setattr(dsh, "_task_launcher_action_mode", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(dsh, "_runner_action_start_contract", lambda *_args, **_kwargs: {
        "is_production_start": True,
        "requires_start": True,
        "has_start": True,
        "forbidden_switches": [],
    })
    monkeypatch.setattr(dsh, "_runner_has_pre_run_bootstrap_interlock", lambda *_args: True)
    monkeypatch.setattr(dsh, "_legacy_direct_clean_runtime_contract", lambda *_args: False)
    monkeypatch.setattr(dsh, "_cleanroom_live_task_definition", lambda **_kwargs: {
        "recognized": True,
        "ok": True,
        "stableAuthority": {},
    })
    monkeypatch.setattr(dsh, "_validate_live_high_cost_binding_authority", lambda **_kwargs: {
        "ok": True,
        "binding_path": str(tmp_path / "binding.json"),
        "binding_receipt_sha256": "a" * 64,
    })
    task_details = {
        "ok": True,
        "state": "Ready",
        "last_task_result": 0,
        "action_summary": "pythonw launcher.pyw runner",
        "triggers": [{"enabled": True, "start_boundary": "2026-08-22T06:00:00"}],
        "next_run_time": "2026-08-22T06:00:00+09:00",
        "number_of_missed_runs": 0,
    }
    bootstrap_details = {
        "ok": True,
        "task_name": "News-Grasp Bootstrap",
        "state": "Ready",
        "enabled": True,
        "task_path": "\\",
        "multiple_instances_policy": "IgnoreNew",
        "last_task_result": 0,
        "action_summary": "bootstrap.ps1 -SmokeTest -TimeoutMinutes 2 -StateFile state -LogDir logs",
        "actions": [{"execute": "powershell.exe", "arguments": "bootstrap.ps1 -SmokeTest"}],
        "triggers": [{"enabled": True, "start_boundary": "2026-08-22T05:55:00"}],
        "next_run_time": "2026-08-22T05:55:00+09:00",
        "number_of_missed_runs": 0,
    }
    monkeypatch.setattr(
        dsh,
        "_scheduled_task_details",
        lambda *, task_name, powershell_exe: task_details
        if task_name == "News-Grasp Production"
        else bootstrap_details,
    )
    readiness = dsh.verify_live_runner_readiness(
        repo_root=tmp_path,
        ops_repo_root=tmp_path,
        date="2026-08-22",
        live_runner_path=tmp_path / "runner.ps1",
        live_watcher_path=tmp_path / "watcher.ps1",
        live_bootstrap_path=tmp_path / "bootstrap.ps1",
        live_task_launcher_path=tmp_path / "launcher.pyw",
        run_canary=False,
    )
    if readiness.get("ok") is True or readiness.get("next_run_readiness", {}).get("status") == "ready":
        failures.append(f"run_canary=False was Green: {readiness!r}")
    assert not failures, "R8 ExpectedRed: " + " | ".join(failures)
