from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
from importlib.machinery import SourceFileLoader
import importlib.util
import inspect
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, Mapping

import pytest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_PATH = ROOT / "scripts" / "ops" / "news-grasp-task-launcher.pyw"
INSTALLER_PATH = ROOT / "scripts" / "ops" / "install-news-grasp-ops.ps1"
MANIFEST_PATH = ROOT / "config" / "news_grasp_cleanroom_task_manifest_v1.json"
OBSERVED_AT = datetime(2026, 8, 22, 6, 0, tzinfo=timezone(timedelta(hours=9)))
EXPECTED_RUNTIME_ROOT = Path.home() / ".news-grasp-runtime"
EXPECTED_MANIFEST_NAME = "news_grasp_cleanroom_task_manifest_v1.json"
EXPECTED_SCHEDULE_ID = "news-grasp-daily-v1"
EXPECTED_INTENT = "reconcile"


def _load_launcher() -> Any:
    loader = SourceFileLoader("news_grasp_task_launcher_operational_cutover", str(LAUNCHER_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None and spec.loader is not None, "launcher import spec unavailable"
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _dispatch_helper() -> tuple[Any, Any]:
    module = _load_launcher()
    helper = getattr(module, "run_cleanroom_dispatch", None)
    assert callable(helper), "production launcher must expose run_cleanroom_dispatch"
    parameters = inspect.signature(helper).parameters
    required = (
        "schedule_id",
        "intent",
        "bin_dir",
        "observed_at",
        "controller_factory",
        "child_runner",
        "task_context_validator",
    )
    assert all(name in parameters for name in required), (
        "run_cleanroom_dispatch signature must expose " + ", ".join(required)
    )
    assert all(
        parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        for name in required[2:]
    ), "runtime seams must be keyword-only"
    return module, helper


def _decision(*, status: str, slot_kind: str = "Scheduled") -> dict[str, Any]:
    slot_key = f"{EXPECTED_SCHEDULE_ID}/2026-08-22/{slot_kind}"
    writer = f"writer-{slot_kind.lower()}"
    return {
        "status": status,
        "decision": status,
        "slotKind": slot_kind,
        "slotKey": slot_key,
        "writerKey": writer,
        "ownerKey": writer,
        "fenceToken": 7,
    }


class _FakeController:
    def __init__(self, decision: Mapping[str, Any]) -> None:
        self.decision = dict(decision)
        self.commits: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def reconcile(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return dict(self.decision)

    acquire = reconcile

    def commit_slot(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.commits.append((args, dict(kwargs)))
        return {"status": "committed"}

    commit = commit_slot


def _find_value(value: Any, names: set[str]) -> Any:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in names:
                return item
            found = _find_value(item, names)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for item in value:
            found = _find_value(item, names)
            if found is not None:
                return found
    return None


def _install_dispatch_authority(module: Any, bin_dir: Path) -> None:
    """controller/child seam用に、production authorityをtmpへ完全生成する。"""
    bin_dir.mkdir(parents=True, exist_ok=True)
    launcher_path = LAUNCHER_PATH.resolve()
    task_pythonw = bin_dir / "pythonw.exe"
    task_pythonw.write_bytes(b"deterministic pythonw fixture")
    binding_path = bin_dir / "news-grasp-high-cost-binding-v1.json"
    binding_receipt = "a" * 64
    binding_path.write_text(
        json.dumps(
            {
                "schemaVersion": "NEWS_GRASP_HIGH_COST_BINDING_V1",
                "bindingReceiptSha256": binding_receipt,
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    recovery_path = bin_dir / "news-grasp-recovery-runtime-binding-v1.json"
    recovery_path.write_text(
        json.dumps(
            {
                "schemaVersion": "NEWS_GRASP_RECOVERY_RUNTIME_BINDING_V1",
                "highCostBindingPath": str(binding_path.resolve()),
                "highCostBindingReceiptSha256": binding_receipt,
                "taskPythonwPath": str(task_pythonw.resolve()),
                "taskPythonwSha256": hashlib.sha256(task_pythonw.read_bytes()).hexdigest(),
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    authority = {
        "schemaVersion": module.STABLE_TASK_AUTHORITY_SCHEMA,
        "stableLauncherPath": str(launcher_path),
        "stableLauncherSha256": hashlib.sha256(launcher_path.read_bytes()).hexdigest(),
        "action": [
            str(task_pythonw.resolve()),
            str(launcher_path),
            "dispatch",
            "--schedule-id",
            EXPECTED_SCHEDULE_ID,
            "--intent",
            EXPECTED_INTENT,
        ],
        "repoArgumentCount": 0,
        "highCostBindingPath": str(binding_path.resolve()),
        "highCostBindingReceiptSha256": binding_receipt,
    }
    authority["authoritySha256"] = hashlib.sha256(
        json.dumps(
            authority,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    (bin_dir / "news-grasp-stable-task-authority-v1.json").write_text(
        json.dumps(authority, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _dispatch_task_origin_witness_payload(module: Any, *, task_pythonw: str) -> dict[str, Any]:
    """current R1/R9 validatorが要求するcanonical parent/service chain。"""
    parent_pid = max(1, os.getpid() - 1)
    parent_path = r"C:\Windows\System32\svchost.exe"
    parent_command = parent_path + " -k netsvcs -s Schedule"
    signer = "CN=Microsoft Windows, O=Microsoft Corporation"
    launcher_path = LAUNCHER_PATH.resolve()
    runtime = Path.home() / ".news-grasp-runtime" / "production-runtime"
    return {
        "targetProcessId": os.getpid(),
        "parentProcessId": parent_pid,
        "parentProcessName": "svchost.exe",
        "parentProcessCommandLine": parent_command,
        "parentProcessPath": parent_path,
        "parentAuthenticodeStatus": "Valid",
        "parentAuthenticodeSubject": signer,
        "scheduleServiceName": "Schedule",
        "scheduleServicePid": parent_pid,
        "scheduleServiceState": "Running",
        "scheduleServiceCommandLine": parent_command,
        "taskName": "News-Grasp Production",
        "enabled": True,
        "state": "Running",
        "lastRunTime": OBSERVED_AT.isoformat(),
        "taskPath": "\\",
        "multipleInstancesPolicy": "Parallel",
        "actions": [
            {
                "execute": task_pythonw,
                "arguments": subprocess.list2cmdline(
                    [
                        str(launcher_path),
                        "dispatch",
                        "--schedule-id",
                        EXPECTED_SCHEDULE_ID,
                        "--intent",
                        EXPECTED_INTENT,
                    ]
                ),
                "workingDirectory": str(runtime),
            }
        ],
        "triggers": [
            {"enabled": True, "kind": "MSFT_TaskDailyTrigger", "startBoundary": "2026-08-22T06:00:00+09:00"},
            {"enabled": True, "kind": "MSFT_TaskDailyTrigger", "startBoundary": "2026-08-22T06:40:00+09:00"},
        ],
        "ancestorChain": [
            {
                "pid": parent_pid,
                "path": parent_path,
                "name": "svchost.exe",
                "commandLine": parent_command,
                "authenticodeStatus": "Valid",
                "authenticodeSubject": signer,
            }
        ],
    }


def test_task_origin_witness_accepts_protected_direct_schedule_service_parent() -> None:
    """実Windowsでprotected svchostのpath/commandが空でもSchedule PID直親を受理する。"""
    module = _load_launcher()
    payload = _dispatch_task_origin_witness_payload(
        module,
        task_pythonw=r"C:\Python312\pythonw.exe",
    )
    schedule_pid = payload["scheduleServicePid"]
    payload.update(
        {
            "parentProcessId": schedule_pid,
            "parentProcessName": "svchost.exe",
            "parentProcessCommandLine": "",
            "parentProcessPath": "",
            "parentAuthenticodeStatus": "",
            "parentAuthenticodeSubject": "",
            "scheduleServiceCommandLine": r"C:\Windows\System32\svchost.exe -k netsvcs -p",
            "ancestorChain": [],
        }
    )
    assert module._cleanroom_validate_process_witness(payload) is True
    payload["parentProcessId"] = int(schedule_pid) + 1
    assert module._cleanroom_validate_process_witness(payload) is False


def test_entry_canary_child_is_an_isolated_local_probe_not_a_high_cost_bootstrap(
    tmp_path: Path,
) -> None:
    """entry-canaryは外部model authorityに依存せず、隔離child起動だけを実証する。"""
    module = _load_launcher()
    generation = "a" * 64
    nonce = "b" * 32
    expected_probe_path = tmp_path / "entry-canary" / generation / nonce / "child-probe.txt"
    command, safety = module._cleanroom_child_command(
        route="task-origin-child-probe",
        bin_dir=tmp_path / "bin",
        authority={"action": [r"C:\Python312\pythonw.exe"]},
        runtime_root=tmp_path,
        canary_generation=generation,
        canary_nonce=nonce,
    )
    assert command[1].endswith("news-grasp-task-launcher.pyw")
    assert command[2:] == [
        "task-origin-child-probe",
        "--canary-generation",
        generation,
        "--canary-nonce",
        nonce,
    ]
    assert str(expected_probe_path) not in command
    assert "--high-cost-binding-path" not in command
    assert "--high-cost-binding-sha256" not in command
    assert safety["externalEffectCount"] == 0
    assert safety["probePath"] == str(expected_probe_path)


def test_cleanroom_child_probe_is_exact_exclusive_and_rejects_invalid_names(
    tmp_path: Path,
) -> None:
    """child probeはruntime root内の固定名をexclusive-createし、path escapeを拒否する。"""
    module = _load_launcher()
    writer = getattr(module, "_write_cleanroom_child_probe", None)
    assert callable(writer), "launcher must expose _write_cleanroom_child_probe"
    generation = "a" * 64
    nonce = "b" * 32
    expected = tmp_path / "entry-canary" / generation / nonce / "child-probe.txt"
    expected.parent.mkdir(parents=True)
    written = writer(generation=generation, nonce=nonce, runtime_root=tmp_path)
    assert Path(written).resolve() == expected.resolve()
    assert expected.read_text(encoding="utf-8") == "probe_ok"
    with pytest.raises((FileExistsError, OSError, RuntimeError)):
        writer(generation=generation, nonce=nonce, runtime_root=tmp_path)
    assert expected.read_text(encoding="utf-8") == "probe_ok"

    invalid_cases = (
        ("../" + "a" * 61, nonce),
        ("A" * 64, nonce),
        (generation, "../" + "b" * 29),
        (generation, "B" * 32),
    )
    for invalid_generation, invalid_nonce in invalid_cases:
        with pytest.raises((OSError, RuntimeError, ValueError)):
            writer(
                generation=invalid_generation,
                nonce=invalid_nonce,
                runtime_root=tmp_path,
            )
    assert not (tmp_path.parent / "escape").exists()


def test_cleanroom_child_probe_propagates_managed_directory_reparse_rejection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """managed-directory/reparse guardの拒否理由をchild probeで握り潰さない。"""
    module = _load_launcher()
    writer = getattr(module, "_write_cleanroom_child_probe", None)
    assert callable(writer), "launcher must expose _write_cleanroom_child_probe"
    rejection = "NEWS_GRASP_CLEANROOM_CHILD_PROBE_MANAGED_PATH_INVALID"

    def reject(*_args: Any, **_kwargs: Any) -> Path:
        raise RuntimeError(rejection)

    monkeypatch.setattr(module, "_assert_managed_path", reject)
    with pytest.raises(RuntimeError, match=re.escape(rejection)):
        writer(generation="a" * 64, nonce="b" * 32, runtime_root=tmp_path)


def test_legacy_probe_cli_rejects_caller_supplied_outside_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """legacy --probe は任意caller pathへ書き込まず、専用modeへ閉じる。"""
    module = _load_launcher()
    outside = tmp_path / "outside" / "legacy-child-probe.txt"
    monkeypatch.setattr(
        sys,
        "argv",
        ["news-grasp-task-launcher.pyw", "bootstrap", "--probe", str(outside)],
    )
    try:
        result = module.main()
    except SystemExit as error:
        result = error.code
    assert result not in (0, None)
    assert not outside.exists()


def _run_dispatch(
    helper: Any,
    tmp_path: Path,
    decision: Mapping[str, Any],
    *,
    child_exit: int = 0,
    task_context_validator: Any = None,
) -> tuple[Any, _FakeController, list[tuple[tuple[Any, ...], dict[str, Any]]], dict[str, Any]]:
    controller = _FakeController(decision)
    factory_calls: dict[str, Any] = {}
    child_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def controller_factory(*args: Any, **kwargs: Any) -> _FakeController:
        factory_calls["args"] = args
        factory_calls["kwargs"] = dict(kwargs)
        return controller

    def child_runner(*args: Any, **kwargs: Any) -> int:
        child_calls.append((args, dict(kwargs)))
        return child_exit

    # Positive deterministic seam carries the canonical Task Scheduler witness.
    # Negative validator tests pass an explicit false/raising validator below.
    if task_context_validator is None:
        task_context_validator = lambda **_context: True
    module = helper.__globals__
    _install_dispatch_authority(SimpleNamespace(**module), tmp_path / "bin")
    result = helper(
        EXPECTED_SCHEDULE_ID,
        EXPECTED_INTENT,
        bin_dir=tmp_path / "bin",
        observed_at=OBSERVED_AT,
        controller_factory=controller_factory,
        child_runner=child_runner,
        task_context_validator=task_context_validator,
    )
    return result, controller, child_calls, factory_calls


def _manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _live_snapshot() -> dict[str, Any]:
    task = _manifest()["tasks"][0]
    return {
        "schemaVersion": "NEWS_GRASP_LIVE_TASK_SNAPSHOT_V1",
        "tasks": [
            {
                "taskPath": task["taskPath"],
                "taskName": task["taskName"],
                "enabled": True,
                "multipleInstancesPolicy": task["multipleInstancesPolicy"],
                "triggers": deepcopy(task["triggers"]),
                "action": deepcopy(task["action"]),
            }
        ],
        "extraEnabledTasks": [],
    }


def _canonical_readiness() -> dict[str, Any]:
    action = {
        "entryModule": "tools.news_grasp_cleanroom_dispatch",
        "argv": [
            "dispatch",
            "--schedule-id",
            EXPECTED_SCHEDULE_ID,
            "--intent",
            EXPECTED_INTENT,
        ],
        "workingDirectoryToken": "<RUNTIME_ROOT>",
    }
    triggers = [
        {
            "triggerId": "scheduled-0600",
            "kind": "daily",
            "localTime": "06:00:00",
            "timeZone": "Asia/Tokyo",
        },
        {
            "triggerId": "audit-0640",
            "kind": "daily",
            "localTime": "06:40:00",
            "timeZone": "Asia/Tokyo",
        },
    ]
    authority = {
        "schemaVersion": "STABLE_TASK_AUTHORITY_V1",
        "taskName": "News-Grasp Production",
        "taskPath": "\\",
        "multipleInstancesPolicy": "Parallel",
        "action": deepcopy(action),
        "triggers": deepcopy(triggers),
        "workingDirectoryToken": "<RUNTIME_ROOT>",
        "authoritySha256": "a" * 64,
    }
    digest = "b" * 64
    return {
        "ok": True,
        "repo_runner": {"sha256": digest},
        "live_runner": {"sha256": digest},
        "repo_watcher": {"sha256": digest},
        "live_watcher": {"sha256": digest},
        "repo_bootstrap": {"sha256": digest},
        "live_bootstrap": {"sha256": digest},
        "repo_task_launcher": {"sha256": digest},
        "live_task_launcher": {"sha256": digest},
        "scheduled_task": {
            "ok": True,
            "taskName": "News-Grasp Production",
            "taskPath": "\\",
            "multipleInstancesPolicy": "Parallel",
            "action": deepcopy(action),
            "triggers": deepcopy(triggers),
            "stableAuthority": authority,
            "authority": deepcopy(authority),
        },
        "stable_authority": deepcopy(authority),
        "canary": {"ok": True, "status": "smoke_ok"},
    }


def test_launcher_exposes_dispatch_api_and_cli_surface() -> None:
    module, _helper = _dispatch_helper()
    source = LAUNCHER_PATH.read_text(encoding="utf-8-sig")
    assert module.__file__ == str(LAUNCHER_PATH)
    assert '"dispatch"' in source or "'dispatch'" in source
    assert '--schedule-id' in source
    assert '--intent' in source


def test_dispatch_task_context_validator_rejects_before_side_effects_and_default_guard_is_read_only(
    tmp_path: Path,
) -> None:
    """Task観測はchild/controller/commitより前に拒否し、既定guardはread-only canonical観測だけを行う。"""
    _module, helper = _dispatch_helper()

    source = LAUNCHER_PATH.read_text(encoding="utf-8-sig")
    for token in (
        "Get-ScheduledTask",
        "Get-ScheduledTaskInfo",
        "News-Grasp Production",
        "Running",
        "LastRunTime",
        "news-grasp-task-launcher.pyw",
        "dispatch",
        "--schedule-id",
        EXPECTED_SCHEDULE_ID,
        "--intent",
        EXPECTED_INTENT,
        "Parallel",
        "06:00",
        "06:40",
        "production-runtime",
    ):
        assert token in source, f"default task-context guard must bind {token!r}"
    for mutation in (
        "Register-ScheduledTask",
        "Enable-ScheduledTask",
        "Disable-ScheduledTask",
        "Start-ScheduledTask",
        "Set-ScheduledTask",
    ):
        assert mutation not in source, f"task-context guard must not mutate Task state: {mutation}"

    def run_rejected(validator: Any) -> Any:
        try:
            return _run_dispatch(
                helper,
                tmp_path,
                _decision(status="ACQUIRED", slot_kind="Scheduled"),
                task_context_validator=validator,
            )
        except Exception as error:  # typed rejection is acceptable at this seam
            return error

    rejected = run_rejected(lambda *_args, **_kwargs: False)
    if not isinstance(rejected, BaseException):
        result, controller, children, factory = rejected
        assert isinstance(result, int) and result != 0
        assert factory == {}
        assert children == []
        assert controller.commits == []

    def raising_validator(*_args: Any, **_kwargs: Any) -> bool:
        raise RuntimeError("task context unavailable")

    rejected_exception = run_rejected(raising_validator)
    if not isinstance(rejected_exception, BaseException):
        result, controller, children, factory = rejected_exception
        assert isinstance(result, int) and result != 0
        assert factory == {}
        assert children == []
        assert controller.commits == []


def test_dispatch_rejects_unknown_schedule_or_intent_and_binds_runtime_manifest(tmp_path: Path) -> None:
    _module, helper = _dispatch_helper()
    factory_calls = 0

    def controller_factory(*args: Any, **kwargs: Any) -> _FakeController:
        nonlocal factory_calls
        factory_calls += 1
        return _FakeController(_decision(status="NOT_DUE"))

    def child_runner(*args: Any, **kwargs: Any) -> int:
        raise AssertionError("child must not run for rejected input")

    for schedule_id, intent in (("wrong-schedule", EXPECTED_INTENT), (EXPECTED_SCHEDULE_ID, "wrong-intent")):
        with pytest.raises(Exception):
            helper(
                schedule_id,
                intent,
                bin_dir=tmp_path / "bin",
                observed_at=OBSERVED_AT,
                controller_factory=controller_factory,
                child_runner=child_runner,
            )
    assert factory_calls == 0

    result, _controller, _children, factory = _run_dispatch(
        helper, tmp_path, _decision(status="NOT_DUE")
    )
    assert result == 0
    runtime_root = _find_value(factory, {"runtime_root", "runtimeRoot"})
    manifest_path = _find_value(factory, {"manifest_path", "manifestPath"})
    assert runtime_root is not None and Path(str(runtime_root)).resolve() == EXPECTED_RUNTIME_ROOT.resolve()
    assert manifest_path is not None
    manifest = Path(str(manifest_path)).resolve()
    assert EXPECTED_MANIFEST_NAME == manifest.name
    assert "production-runtime" in {part.casefold() for part in manifest.parts}


def test_dispatch_routes_acquired_scheduled_and_audit_exactly_once(tmp_path: Path) -> None:
    _module, helper = _dispatch_helper()
    for slot_kind, expected_child in (("Scheduled", "runner"), ("Audit", "deadman")):
        result, controller, children, _factory = _run_dispatch(
            helper,
            tmp_path,
            _decision(status="ACQUIRED", slot_kind=slot_kind),
        )
        assert result == 0
        assert len(children) == 1
        route_values = repr(children[0]).casefold()
        assert expected_child in route_values
        assert len(controller.commits) == 1


def test_dispatch_keeps_attached_not_due_and_terminal_noop_childless(tmp_path: Path) -> None:
    _module, helper = _dispatch_helper()
    for status in ("NOT_DUE", "ATTACHED", "TERMINAL_NOOP"):
        result, controller, children, _factory = _run_dispatch(
            helper,
            tmp_path,
            _decision(status=status),
        )
        assert result == 0
        assert children == []
        assert controller.commits == []


def test_dispatch_commits_child_outcome_once_with_slot_writer_fence_and_no_window_seam(tmp_path: Path) -> None:
    module, helper = _dispatch_helper()
    source = LAUNCHER_PATH.read_text(encoding="utf-8-sig")
    assert "shell=False" in source
    assert "subprocess.CREATE_NO_WINDOW" in source
    for child_exit, terminal_state in ((0, "SUCCEEDED"), (17, "FAILED")):
        decision = _decision(status="ACQUIRED", slot_kind="Scheduled")
        result, controller, children, _factory = _run_dispatch(
            helper,
            tmp_path,
            decision,
            child_exit=child_exit,
        )
        assert result == child_exit
        assert len(children) == 1
        assert len(controller.commits) == 1
        commit_args, commit_kwargs = controller.commits[0]
        payload = {"args": commit_args, "kwargs": commit_kwargs}
        assert _find_value(payload, {"slotKey", "slot_key"}) == decision["slotKey"]
        assert _find_value(payload, {"fenceToken", "fence_token"}) == decision["fenceToken"]
        assert _find_value(payload, {"terminalState", "terminal_state"}) == terminal_state
        assert _find_value(payload, {"writerKey", "ownerKey", "writer_key", "owner_key"}) == decision["ownerKey"]
    assert module.__file__ == str(LAUNCHER_PATH)


def test_installer_uses_single_canonical_dispatch_task_and_disables_legacy_controls() -> None:
    source = INSTALLER_PATH.read_text(encoding="utf-8-sig")
    assert re.search(r"dispatch.*--schedule-id.*news-grasp-daily-v1.*--intent.*reconcile", source, re.I | re.S)
    assert all(token in source for token in ("06:00", "06:40", "05:55"))
    assert re.search(r"MultipleInstances(?:'|\s*=\s*|\s+)Parallel", source, re.I)
    assert not re.search(r"(?:Register|Enable)-ScheduledTask\s+[^\r\n]*\$DeadmanTaskName", source, re.I)
    assert not re.search(r"(?:Register|Enable)-ScheduledTask\s+[^\r\n]*\$LegacyRunnerTaskName", source, re.I)
    assert "function Assert-NewsGraspInstalledState" in source
    authority_block = source[source.index("function Assert-NewsGraspInstalledState") :]
    assert "dispatch" in authority_block and "Parallel" in authority_block


def test_release_live_task_parity_accepts_canonical_and_rejects_all_drift_classes() -> None:
    release = importlib.import_module("tools.news_grasp_cleanroom_release")
    validator = getattr(release, "validate_live_task_parity", None)
    assert callable(validator), "release must expose validate_live_task_parity"
    manifest = _manifest()
    live = _live_snapshot()
    assert validator(manifest, live) is True

    drift_cases = []
    for path in (
        ("action",),
        ("triggers",),
        ("multipleInstancesPolicy",),
        ("workingDirectory",),
        ("taskName",),
        ("extraEnabledTasks",),
    ):
        negative = deepcopy(live)
        if path == ("action",):
            negative["tasks"][0]["action"]["argv"].append("--drift")
        elif path == ("triggers",):
            negative["tasks"][0]["triggers"][0]["localTime"] = "06:01:00"
        elif path == ("multipleInstancesPolicy",):
            negative["tasks"][0]["multipleInstancesPolicy"] = "IgnoreNew"
        elif path == ("workingDirectory",):
            negative["tasks"][0]["action"]["workingDirectory"] = "<WRONG_RUNTIME_ROOT>"
        elif path == ("taskName",):
            negative["tasks"][0]["taskName"] = "News-Grasp Runner"
        else:
            negative["extraEnabledTasks"] = [{"taskName": "News-Grasp Deadman", "enabled": True}]
        drift_cases.append(negative)
    for negative in drift_cases:
        with pytest.raises(Exception):
            validator(manifest, negative)


def test_release_live_task_parity_rejects_caller_self_report_boolean() -> None:
    release = importlib.import_module("tools.news_grasp_cleanroom_release")
    validator = getattr(release, "validate_live_task_parity", None)
    assert callable(validator), "release must expose validate_live_task_parity"
    manifest = _manifest()
    live = _live_snapshot()
    with pytest.raises(Exception):
        validator(manifest, True)
    with pytest.raises(Exception):
        validator(True, live)


def test_live_readiness_accepts_cleanroom_dispatch_and_rejects_legacy_runner_only() -> None:
    daily_self_heal = importlib.import_module("tools.daily_self_heal")
    consumer = getattr(daily_self_heal, "live_runner_readiness_manifest_ok", None)
    assert callable(consumer), "live readiness consumer is missing"
    canonical = _canonical_readiness()
    assert consumer(canonical) is True

    legacy = deepcopy(canonical)
    legacy_task = legacy["scheduled_task"]
    legacy_task["multipleInstancesPolicy"] = "IgnoreNew"
    legacy_task["action"] = {
        "execute": r"C:\Python312\pythonw.exe",
        "arguments": "news-grasp-task-launcher.pyw runner",
    }
    legacy_task["triggers"] = [
        {
            "triggerId": "scheduled-0600",
            "kind": "daily",
            "localTime": "06:00:00",
            "timeZone": "Asia/Tokyo",
        }
    ]
    legacy_task["stableAuthority"] = {
        "schemaVersion": "STABLE_TASK_AUTHORITY_V1",
        "taskName": "News-Grasp Runner",
        "action": deepcopy(legacy_task["action"]),
        "triggers": deepcopy(legacy_task["triggers"]),
        "multipleInstancesPolicy": "IgnoreNew",
    }
    assert consumer(legacy) is False
