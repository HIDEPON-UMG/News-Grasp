from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"PROBE_IMPORT_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--broker", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--boundary", required=True)
    parser.add_argument("--call-id", required=True)
    parser.add_argument("--probe-id", required=True)
    args = parser.parse_args()

    control = _load("probe_high_cost_control_v2", args.control)
    broker = _load("probe_model_spawn_broker", args.broker)
    store = control.HighCostControlStore.open_for_test(
        args.ledger, control.MemoryAnchor()
    )
    authority = control.scheduled_news_grasp_authority("2026-08-05")

    def reserve(
        route,
        call_id,
        _reservation_path,
        _operation_admission_path,
        _expected_operation_kind,
        _expected_issue_date,
    ):
        effective_call_id = str(call_id or args.call_id)
        value = store.reserve_production_call(
            authority=authority,
            route=route,
            call_id=effective_call_id,
        )
        return {**value, "callId": effective_call_id}, None

    def mark_started(*, route: str, call_id: str):
        if args.boundary == "before_consume":
            os._exit(91)
        return control._mark_model_call_started_in_store(
            store=store,
            authority=authority,
            route=route,
            call_id=call_id,
        )

    broker._reserve = reserve
    broker.mark_production_model_call_started = mark_started
    broker.mark_child_bootstrap_started = lambda *, call_id: control.mark_child_bootstrap_started_in_store(
        store=store, authority=authority, call_id=call_id
    )
    broker.mark_child_payload_started = lambda *, call_id: control.mark_child_payload_started_in_store(
        store=store, authority=authority, call_id=call_id
    )
    broker.mark_child_completed = lambda *, call_id: control.mark_child_completed_in_store(
        store=store, authority=authority, call_id=call_id
    )
    original_run = broker.subprocess.run
    if args.boundary == "consume_committed_before_bootstrap":
        broker.subprocess.run = lambda *_args, **_kwargs: os._exit(92)
    elif args.boundary == "bootstrap_started_before_payload_commit":
        def launch_then_crash(command, **kwargs):
            capture_output = bool(kwargs.pop("capture_output", False))
            kwargs.pop("check", None)
            kwargs.pop("timeout", None)
            if capture_output:
                kwargs.setdefault("stdout", subprocess.PIPE)
                kwargs.setdefault("stderr", subprocess.PIPE)
            process = subprocess.Popen(command, **kwargs)
            bootstrap_marker = (
                args.fixture_root / f"{args.call_id}.{args.probe_id}.bootstrap"
            )
            deadline = time.monotonic() + 10
            while not bootstrap_marker.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            control.mark_child_bootstrap_started_in_store(
                store=store, authority=authority, call_id=args.call_id
            )
            os._exit(93)

        broker.subprocess.run = launch_then_crash

    powershell = Path(
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    )
    command = [
        str(powershell),
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(args.fixture_root / "codex.ps1"),
        "-CallId",
        args.call_id,
        "-Boundary",
        args.boundary,
        "-ProbeId",
        args.probe_id,
        "-FixtureRoot",
        str(args.fixture_root),
    ]
    try:
        completed = broker.run_model_subprocess(
            command,
            route="repair:crash-probe",
            call_id=args.call_id,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=20,
        )
        return int(completed.returncode)
    finally:
        broker.subprocess.run = original_run
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
