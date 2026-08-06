from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_control(workspace_harness: Path) -> ModuleType:
    source = workspace_harness / "tools" / "harness" / "high_cost_control_v2.py"
    spec = importlib.util.spec_from_file_location(
        "isolated_current_high_cost_control_v2", source
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("CURRENT_HIGH_COST_CONTROL_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _creationflags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0


def _launch_marker(path: Path, value: str) -> subprocess.Popen[str]:
    script = (
        "from pathlib import Path; "
        f"Path({str(path)!r}).write_text({value!r}, encoding='utf-8')"
    )
    return subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_creationflags(),
    )


def _new_store(*, workspace_harness: Path, isolation_root: Path):
    control = _load_control(workspace_harness)
    runtime_root = Path(
        tempfile.mkdtemp(
            prefix="dcp05-",
            dir=str(isolation_root / "artifacts" / "red-runtime"),
        )
    )
    anchor = control.MemoryAnchor()
    store = control.HighCostControlStore.create_for_test(
        runtime_root / "ledger.sqlite3", anchor
    )
    authority = control.scheduled_news_grasp_authority("2026-08-05")
    store.ensure_production_task(
        authority=authority,
        max_calls=9,
        max_full_e2e_attempts=0,
        request_id=f"issue:{authority.task_identity}",
    )
    store.reserve_scheduled_production_attempt(
        authority=authority,
        issue_date="2026-08-05",
        allow_existing=False,
    )
    return control, runtime_root, store, authority


def _events(store: Any) -> list[dict[str, Any]]:
    rows = store.db.execute(
        "SELECT sequence,request_id,event_type,payload_json FROM events ORDER BY sequence"
    ).fetchall()
    return [
        {
            "sequence": int(row["sequence"]),
            "requestId": str(row["request_id"]),
            "eventType": str(row["event_type"]),
            "payload": json.loads(str(row["payload_json"])),
        }
        for row in rows
    ]


def _write_payload_fixture(fixture_root: Path) -> None:
    script = fixture_root / "codex.ps1"
    script.write_text(
        """param(
    [string]$CallId,
    [string]$Boundary,
    [string]$ProbeId,
    [string]$FixtureRoot
)
$ErrorActionPreference = 'Stop'
$bootstrap = Join-Path $FixtureRoot ($CallId + '.' + $ProbeId + '.bootstrap')
$payload = Join-Path $FixtureRoot ($CallId + '.' + $ProbeId + '.payload')
[System.IO.File]::WriteAllText($bootstrap, $Boundary, [System.Text.UTF8Encoding]::new($false))
if ($Boundary -eq 'bootstrap_started_before_payload_commit') {
    Start-Sleep -Milliseconds 300
}
[System.IO.File]::WriteAllText($payload, $Boundary, [System.Text.UTF8Encoding]::new($false))
if ($Boundary -eq 'payload_started_before_completion') { exit 94 }
exit 0
""",
        encoding="utf-8-sig",
        newline="\n",
    )


def _run_crash_probe(
    *,
    workspace_harness: Path,
    isolation_root: Path,
    runtime_root: Path,
    ledger: Path,
    boundary: str,
    call_id: str,
    probe_id: str,
) -> subprocess.Popen[str]:
    probe = (
        isolation_root
        / "repo"
        / "tests"
        / "fixtures"
        / "autonomous_operations"
        / "broker_crash_probe.py"
    )
    return subprocess.Popen(
        [
            sys.executable,
            str(probe),
            "--control",
            str(workspace_harness / "tools" / "harness" / "high_cost_control_v2.py"),
            "--broker",
            str(workspace_harness / "tools" / "harness" / "model_spawn_broker.py"),
            "--ledger",
            str(ledger),
            "--fixture-root",
            str(runtime_root),
            "--boundary",
            boundary,
            "--call-id",
            call_id,
            "--probe-id",
            probe_id,
        ],
        cwd=isolation_root / "repo",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_creationflags(),
    )


def observe_current_broker_children(
    *, workspace_harness: Path, isolation_root: Path, perspective: str
) -> dict[str, Any]:
    (isolation_root / "artifacts" / "red-runtime").mkdir(
        parents=True, exist_ok=True
    )
    control, runtime_root, store, authority = _new_store(
        workspace_harness=workspace_harness,
        isolation_root=isolation_root,
    )
    launched: list[dict[str, Any]] = []
    errors: list[str] = []
    control_source = workspace_harness / "tools" / "harness" / "high_cost_control_v2.py"
    broker_source = workspace_harness / "tools" / "harness" / "model_spawn_broker.py"
    try:
        if perspective == "primary":
            for index in range(2):
                call_id = f"primary-call-{index}"
                reservation = store.reserve_production_call(
                    authority=authority,
                    route="daily:summary",
                    call_id=call_id,
                )
                marker = runtime_root / f"primary-{index}.marker"
                process = _launch_marker(marker, call_id)
                stdout, stderr = process.communicate(timeout=20)
                started = control._mark_model_call_started_in_store(
                    store=store,
                    authority=authority,
                    route="daily:summary",
                    call_id=call_id,
                )
                launched.append(
                    {
                        "callId": call_id,
                        "returnCode": process.returncode,
                        "marker": marker.read_text(encoding="utf-8"),
                        "stdout": stdout,
                        "stderr": stderr,
                        "reservation": reservation,
                        "started": started,
                    }
                )
        elif perspective == "adversarial":
            call_id = "duplicate-call"
            ledger = store.path
            store.close()
            _write_payload_fixture(runtime_root)
            duplicate_probe_ids = ["duplicate-0", "duplicate-1"]
            processes = [
                _run_crash_probe(
                    workspace_harness=workspace_harness,
                    isolation_root=isolation_root,
                    runtime_root=runtime_root,
                    ledger=ledger,
                    boundary="payload_started_before_completion",
                    call_id=call_id,
                    probe_id=probe_id,
                )
                for probe_id in duplicate_probe_ids
            ]
            outputs = [process.communicate(timeout=30) for process in processes]
            store = control.HighCostControlStore.open_for_test(
                ledger, control.MemoryAnchor()
            )
            derive_api = getattr(control, "derive_child_authority_in_store", None)
            consume_api = getattr(control, "consume_child_authority_in_store", None)
            adversarial_api_results: dict[str, Any] = {
                "deriveAvailable": callable(derive_api),
                "consumeAvailable": callable(consume_api),
                "wrongState": {"attempted": False, "payloadReached": False},
                "tokenSwap": {"attempted": False, "payloadReached": False},
            }
            if callable(derive_api) and callable(consume_api):
                try:
                    child_a = derive_api(
                        store=store,
                        authority=authority,
                        route="daily:summary",
                        call_id="adversarial-a",
                    )
                    child_b = derive_api(
                        store=store,
                        authority=authority,
                        route="daily:summary",
                        call_id="adversarial-b",
                    )
                    adversarial_api_results["wrongState"]["attempted"] = True
                    try:
                        consume_api(
                            store=store,
                            authority=authority,
                            child_grant=child_a,
                            process_launch_token="0" * 64,
                        )
                    except Exception as error:
                        adversarial_api_results["wrongState"]["error"] = str(error)
                    adversarial_api_results["tokenSwap"]["attempted"] = True
                    try:
                        consume_api(
                            store=store,
                            authority=authority,
                            child_grant=child_a,
                            process_launch_token=str(
                                child_b.get("processLaunchToken") or ""
                            ),
                        )
                    except Exception as error:
                        adversarial_api_results["tokenSwap"]["error"] = str(error)
                except Exception as error:
                    adversarial_api_results["setupError"] = str(error)
            launched.append(
                {
                    "duplicateProcessReturnCodes": [
                        int(process.returncode) for process in processes
                    ],
                    "duplicateOutputs": outputs,
                    "duplicatePayloadReached": any(
                        (
                            runtime_root / f"{call_id}.{probe_id}.payload"
                        ).exists()
                        for process, probe_id in zip(
                            processes, duplicate_probe_ids, strict=True
                        )
                        if process.returncode != 94
                    ),
                    "adversarialApiResults": adversarial_api_results,
                }
            )
        elif perspective == "recovery":
            ledger = store.path
            store.close()
            _write_payload_fixture(runtime_root)
            boundaries = (
                "before_consume",
                "consume_committed_before_bootstrap",
                "bootstrap_started_before_payload_commit",
                "payload_started_before_completion",
            )
            traces: list[dict[str, Any]] = []
            for index, boundary in enumerate(boundaries):
                call_id = f"crash-{index}"
                process = _run_crash_probe(
                    workspace_harness=workspace_harness,
                    isolation_root=isolation_root,
                    runtime_root=runtime_root,
                    ledger=ledger,
                    boundary=boundary,
                    call_id=call_id,
                    probe_id=f"trace-{index}",
                )
                stdout, stderr = process.communicate(timeout=30)
                time.sleep(0.4)
                reopened = control.HighCostControlStore.open_for_test(
                    ledger, control.MemoryAnchor()
                )
                try:
                    current_events = _events(reopened)
                    reconcile_api = getattr(
                        control, "reconcile_child_authorities_in_store", None
                    )
                    if callable(reconcile_api):
                        try:
                            reconcile = reconcile_api(store=reopened)
                        except Exception as error:
                            reconcile = {
                                "attempted": True,
                                "available": True,
                                "error": str(error),
                            }
                    else:
                        reconcile = {
                            "attempted": True,
                            "available": False,
                            "resultCode": "CHILD_RECONCILE_API_ABSENT",
                        }
                finally:
                    reopened.close()
                traces.append(
                    {
                        "boundary": boundary,
                        "brokerProcessReturnCode": process.returncode,
                        "stdout": stdout,
                        "stderr": stderr,
                        "bootstrapReached": (
                            runtime_root / f"{call_id}.trace-{index}.bootstrap"
                        ).exists(),
                        "payloadReached": (
                            runtime_root / f"{call_id}.trace-{index}.payload"
                        ).exists(),
                        "events": current_events,
                        "reconcile": reconcile,
                    }
                )
            race_call = "delayed-bootstrap-race"
            race_probe_ids = ["race-0", "race-1"]
            race_processes = [
                _run_crash_probe(
                    workspace_harness=workspace_harness,
                    isolation_root=isolation_root,
                    runtime_root=runtime_root,
                    ledger=ledger,
                    boundary="bootstrap_started_before_payload_commit",
                    call_id=race_call,
                    probe_id=probe_id,
                )
                for probe_id in race_probe_ids
            ]
            race_outputs = [process.communicate(timeout=30) for process in race_processes]
            time.sleep(0.4)
            store = control.HighCostControlStore.open_for_test(
                ledger, control.MemoryAnchor()
            )
            reconcile_api = getattr(
                control, "reconcile_child_authorities_in_store", None
            )
            if callable(reconcile_api):
                try:
                    race_reconcile = reconcile_api(store=store)
                except Exception as error:
                    race_reconcile = {"available": True, "error": str(error)}
            else:
                race_reconcile = {
                    "attempted": True,
                    "available": False,
                    "resultCode": "CHILD_RECONCILE_API_ABSENT",
                }
            launched = traces
            launched.append(
                {
                    "raceProcessReturnCodes": [p.returncode for p in race_processes],
                    "raceOutputs": race_outputs,
                    "payloadReachedByProbe": {
                        probe_id: (
                            runtime_root / f"{race_call}.{probe_id}.payload"
                        ).exists()
                        for probe_id in race_probe_ids
                    },
                    "startEventCount": sum(
                        1
                        for event in _events(store)
                        if event["eventType"] == "model_process_started"
                        and event["payload"].get("callIdSha256")
                    ),
                    "reconcile": race_reconcile,
                    "casWinnerCount": sum(
                        1 for process in race_processes if process.returncode == 93
                    ),
                    "lateLoserPayloadNotReached": (
                        sum(1 for process in race_processes if process.returncode == 93)
                        == 1
                        and all(
                            not (
                                runtime_root / f"{race_call}.{probe_id}.payload"
                            ).exists()
                            for process, probe_id in zip(
                                race_processes, race_probe_ids, strict=True
                            )
                            if process.returncode != 93
                        )
                    ),
                }
            )
        else:
            raise AssertionError(f"DCP05_PERSPECTIVE_INVALID:{perspective}")
        events = _events(store)
        reconcile_observations = [
            item.get("reconcile")
            for item in launched
            if isinstance(item, dict) and "reconcile" in item
        ]
        return {
            "schemaVersion": "CURRENT_BROKER_CHILD_OBSERVATION_V2",
            "returnCode": 0,
            "launched": launched,
            "errors": errors,
            "events": events,
            "childGrantIds": [
                event["payload"]["childGrantId"]
                for event in events
                if "childGrantId" in event["payload"]
            ],
            "processLaunchTokens": [
                event["payload"]["processLaunchToken"]
                for event in events
                if "processLaunchToken" in event["payload"]
            ],
            "childStatePredicate": {
                "derivedFromApis": {
                    name: callable(getattr(control, name, None))
                    for name in (
                        "derive_child_authority_in_store",
                        "consume_child_authority_in_store",
                        "reconcile_child_authorities_in_store",
                    )
                },
                "derivedFromLedgerEventTypes": sorted(
                    {str(event["eventType"]) for event in events}
                ),
            },
            "reconcileResult": reconcile_observations,
            "recoveryOracle": {
                "canonicalOutcomes": {
                    "before_consume": "reserved_unconsumed_reissue_once",
                    "consume_committed_before_bootstrap": "consumed_unbootstrapped_reissue_once",
                    "bootstrap_started_before_payload_commit": "bootstrap_ambiguous_no_auto_reissue",
                    "payload_started_before_completion": "payload_started_never_reissue",
                },
                "casWinnerCountExpected": 1,
                "lateLoserPayloadNotReachedExpected": True,
                "payloadStartedChildAutoReissueExpected": False,
            },
            "consumerSources": [
                {
                    "path": str(control_source),
                    "symbol": "HighCostControlStore+child_authority",
                },
                {
                    "path": str(broker_source),
                    "symbol": "run_model_subprocess",
                },
            ],
            "input": {
                "authority": {
                    "taskIdentity": authority.task_identity,
                    "operationKind": "scheduled_production",
                    "issueDate": "2026-08-05",
                },
                "route": "daily:summary",
                "requestedCalls": (
                    ["primary-call-0", "primary-call-1"]
                    if perspective == "primary"
                    else ["duplicate-call", "duplicate-call"]
                    if perspective == "adversarial"
                    else [
                        "before_consume",
                        "consume_committed_before_bootstrap",
                        "bootstrap_started_before_payload_commit",
                        "payload_started_before_completion",
                        "delayed-bootstrap-race",
                    ]
                ),
            },
        }
    finally:
        try:
            store.close()
        except Exception:
            pass
