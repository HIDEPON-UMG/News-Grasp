from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import ModuleType
from typing import Any, Mapping
import uuid

from tools.news_grasp_high_cost_binding import resolve_binding_from_environment


_SCHEDULED_ADMISSION_LOCK = threading.Lock()


def resolve_broker_path() -> Path:
    resolved = resolve_binding_from_environment()
    candidate = Path(str(resolved["brokerInstalledPath"])).resolve()
    if not candidate.is_file():
        raise RuntimeError("MODEL_SPAWN_BROKER_UNAVAILABLE")
    return candidate


def _load_broker() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "aiharness_model_spawn_broker", resolve_broker_path()
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("MODEL_SPAWN_BROKER_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    candidate = Path(raw)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(candidate, path)
    finally:
        candidate.unlink(missing_ok=True)


def _run_broker_json(arguments: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, str(resolve_broker_path()), *arguments],
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=30,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        raise RuntimeError(f"HIGH_COST_AUTHORITY_ISSUANCE_FAILED:{completed.returncode}")
    try:
        value = json.loads(completed.stdout)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("HIGH_COST_AUTHORITY_ISSUANCE_INVALID") from exc
    if not isinstance(value, dict):
        raise RuntimeError("HIGH_COST_AUTHORITY_ISSUANCE_INVALID")
    return value


def ensure_scheduled_operation_admission(*, repo_root: Path, issue_date: str) -> Path:
    """direct日次本線のscheduled model admissionを一度だけ確立する。"""

    root = repo_root.resolve(strict=True)
    path = root / "build" / "high-cost-operation-admissions" / issue_date / "daily-production.json"
    with _SCHEDULED_ADMISSION_LOCK:
        if path.is_file() and not path.is_symlink():
            try:
                value = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("HIGH_COST_SCHEDULED_ADMISSION_INVALID") from exc
            if (
                isinstance(value, dict)
                and value.get("schemaVersion") == "HIGH_COST_SCHEDULED_OPERATION_ADMISSION_V1"
                and value.get("issueDate") == issue_date
                and value.get("operationKind") == "scheduled_production"
            ):
                return path
            raise RuntimeError("HIGH_COST_SCHEDULED_ADMISSION_INVALID")

        mission = _run_broker_json(["issue-news-grasp-audit-mission"])
        task_contract = root / "automation" / "news-grasp-6-40" / "automation.toml.template"
        runner = root / "tools" / "news_grasp_direct_runtime.py"
        if not task_contract.is_file() or task_contract.is_symlink() or not runner.is_file() or runner.is_symlink():
            raise RuntimeError("HIGH_COST_SCHEDULED_AUTHORITY_SOURCE_MISSING")
        broker = _load_broker()
        store = broker.HighCostControlStore.open_or_create_production()
        try:
            permit = broker.issue_scheduled_production_launch_permit_in_store(
                store=store,
                issue_date=issue_date,
                task_action_sha256=_sha256_file(task_contract),
                runner_sha256=_sha256_file(runner),
                launch_nonce=f"direct-{issue_date}-{uuid.uuid4().hex}",
                mission_authority=mission,
            )
        finally:
            store.close()
        admission = broker.admit_scheduled_news_grasp_operation(
            issue_date=issue_date,
            operation_kind="scheduled_production",
            authority_evidence=permit,
            expected_task_action_sha256=_sha256_file(task_contract),
            expected_runner_sha256=_sha256_file(runner),
        )
        _atomic_json(path, admission)
        return path


def run_model_process(command: list[str], *, route: str, **kwargs: Any) -> Any:
    """公開本線の品質生成だけを実行する直接Codex経路。"""

    for key in (
        "operation_admission_path",
        "expected_operation_kind",
        "expected_issue_date",
        "call_id",
        "execution_root",
    ):
        kwargs.pop(key, None)
    kwargs["shell"] = False
    kwargs.setdefault("creationflags", getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return subprocess.run(command, **kwargs)


def popen_model_process(command: list[str], *, route: str, **kwargs: Any) -> Any:
    return _load_broker().popen_model_process(command, route=route, **kwargs)
