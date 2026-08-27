"""Clean-room follow-up ExpectedRed value oracles (R9-R12)."""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import inspect
import json
from pathlib import Path
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_PATH = ROOT / "scripts" / "ops" / "news-grasp-task-launcher.pyw"
INSTALLER_PATH = ROOT / "scripts" / "ops" / "install-news-grasp-ops.ps1"
BOOTSTRAP_PATH = ROOT / "scripts" / "ops" / "news-grasp-bootstrap.ps1"


def test_r9_task_origin_payload_binds_full_system32_signature_and_schedule_ancestry() -> None:
    """Same-name taskeng/taskhost outside signed System32 must not be accepted."""
    launcher = importlib.util.module_from_spec(
        spec := importlib.util.spec_from_file_location("ng_launcher_r9", LAUNCHER_PATH)
    )
    assert spec.loader is not None
    spec.loader.exec_module(launcher)
    command = " ".join(launcher._cleanroom_context_powershell_command(launcher_pid=1234)).casefold()
    source = LAUNCHER_PATH.read_text(encoding="utf-8").casefold()
    required = {
        "parent full path payload": "parentprocesspath" in command,
        "service PID ancestry payload": "win32_service" in command and "schedule" in command,
        "System32 boundary": "system32" in command or "system32" in source,
        "Microsoft signature check": "get-authenticodeSignature".casefold() in source
        and "signercertificate" in source,
        "same-name path rejection": "parentprocesspath" in source and "system32" in source,
    }
    missing = [label for label, present in required.items() if not present]
    assert not missing, "R9 ExpectedRed: " + ", ".join(missing)


def test_r10_cleanroom_binding_requires_production_bootstrap_authority_execute_match(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """All clean-room Task Execute values must equal validated binding taskPythonwPath."""
    dsh = importlib.import_module("tools.daily_self_heal")
    live_bin = tmp_path / "bin"
    live_bin.mkdir()
    launcher = live_bin / "news-grasp-task-launcher.pyw"
    launcher.write_text("launcher\n", encoding="utf-8")
    binding = live_bin / "news-grasp-high-cost-binding-v1.json"
    authority_exec = r"C:\TaskPython\pythonw.exe"
    bound_exec = r"C:\BoundPython\pythonw.exe"
    receipt = "a" * 64
    launcher_path = str(launcher.resolve())
    runner_args = [launcher_path, "dispatch", "--schedule-id", "news-grasp-daily-v1", "--intent", "reconcile"]
    bootstrap_args = [
        launcher_path,
        "bootstrap",
        "--scheduled-task-name",
        "News-Grasp Bootstrap",
        "--high-cost-binding-path",
        str(binding.resolve()),
        "--high-cost-binding-sha256",
        receipt,
    ]
    authority = {
        "schemaVersion": "STABLE_TASK_AUTHORITY_V1",
        "action": [authority_exec, *runner_args],
        "taskName": "News-Grasp Production",
        "taskPath": "\\",
        "multipleInstancesPolicy": "IgnoreNew",
        "manifestAction": dict(dsh._CLEANROOM_MANIFEST_ACTION),
        "workingDirectoryToken": "<RUNTIME_ROOT>",
        "triggers": [dict(row) for row in dsh._CLEANROOM_MANIFEST_TRIGGERS],
        "highCostBindingPath": str(binding.resolve()),
        "highCostBindingReceiptSha256": receipt,
    }
    authority["authoritySha256"] = hashlib.sha256(
        json.dumps(
            authority,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    def records(details: dict[str, str]) -> list[dict[str, str]]:
        args = runner_args if details["kind"] == "runner" else bootstrap_args
        return [{"execute": authority_exec, "arguments": subprocess.list2cmdline(args)}]

    monkeypatch.setattr(dsh, "_task_action_records", records)
    monkeypatch.setattr(
        dsh,
        "_canonical_live_json",
        lambda _path, *, expected: (authority, b"authority\n"),
    )
    monkeypatch.setattr(
        dsh,
        "_validate_live_high_cost_binding_files",
        lambda **_kwargs: {
            "ok": True,
            "task_pythonw_path": bound_exec,
            "binding_path": str(binding.resolve()),
            "binding_receipt_sha256": receipt,
        },
    )
    result = dsh._validate_live_high_cost_binding_authority(
        task_details={"kind": "runner"},
        bootstrap_details={"kind": "bootstrap"},
        live_task_launcher_path=launcher,
        task_name="News-Grasp Production",
        bootstrap_task_name="News-Grasp Bootstrap",
        ops_repo_root=tmp_path,
    )
    assert result.get("ok") is False, (
        "R10 ExpectedRed: authority/Production Execute escaped validated taskPythonwPath: "
        f"{result!r}"
    )


def test_r11_bootstrap_execution_receipt_is_atomic_authoritative_and_not_pointer_only() -> None:
    """Readiness must consume a typed execution receipt, not infer execution from a pointer."""
    bootstrap = BOOTSTRAP_PATH.read_text(encoding="utf-8").casefold()
    dsh = importlib.import_module("tools.daily_self_heal")
    dsh_source = Path(dsh.__file__).read_text(encoding="utf-8").casefold()
    failures: list[str] = []
    schema = "news_grasp_bootstrap_execution_receipt_v1"
    for label, present in {
        "bootstrap typed receipt schema": schema in bootstrap,
        "atomic receipt write": "write-atomicutf8text" in bootstrap and "execution" in bootstrap,
        "receipt issueDate/observedAt/generationId": all(
            key in bootstrap for key in ("issuedate", "observedat", "generationid")
        ),
        "receipt stable authority hash": "stableauthoritysha" in bootstrap,
        "readiness reads receipt": schema in dsh_source and "execution_receipt" in dsh_source,
        "missing receipt rejection": "execution_receipt_missing" in dsh_source,
        "stale receipt rejection": "execution_receipt_stale" in dsh_source,
        "mismatched receipt rejection": "execution_receipt_mismatch" in dsh_source,
        "gate receives receipt": any(
            name in inspect.signature(dsh._bootstrap_observation_gate).parameters
            for name in ("execution_receipt", "bootstrap_execution_receipt")
        ),
    }.items():
        if not present:
            failures.append(label)
    assert not failures, "R11 ExpectedRed: " + ", ".join(failures)


def test_r12_temporary_installer_canary_quiesces_triggers_waits_closed_and_asserts_rollback() -> None:
    """Temporary action must quiesce triggers, await a closed instance, then restore atomically."""
    source = INSTALLER_PATH.read_text(encoding="utf-8-sig")
    start = source.index("function Invoke-NewsGraspProductionEntryCanary")
    end = source.index("trap {", start)
    function_source = source[start:end].casefold()
    required = {
        "trigger quiescence": "disable-scheduledtask" in function_source
        and ("-trigger" in function_source or "triggers" in function_source),
        "Task state wait": all(
            token in function_source
            for token in ("get-scheduledtask", "state", "running")
        ),
        "exact instance closed wait": "get-scheduledtaskinfo" in function_source
        and "instance" in function_source,
        "timeout rollback": "timeout" in function_source and "finally" in function_source,
        "final assertion": "assert-" in function_source,
    }
    missing = [label for label, present in required.items() if not present]
    assert not missing, "R12 ExpectedRed: " + ", ".join(missing)
