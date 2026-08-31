"""News-Grasp専用handlerのHumanImpact境界。"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Mapping


class HumanImpactContractError(RuntimeError):
    """人間影響契約違反。"""


PRODUCTION_ROUTE_PATHS = (
    "automation/news-grasp-6-40/automation.toml.template",
    "automation/skills/news-grasp-direct-mainline/SKILL.md",
    "tools/news_grasp_title_materializer.py",
    "tools/news_grasp_direct_runtime.py",
    "tools/news_grasp_direct_completion.py",
    "tools/news_grasp_title_control.py",
    "scripts/ops/run_codex_with_timeout.ps1",
    "scripts/ops/news-grasp-task-launcher.pyw",
    "tools/audit_recovery_control.py",
    "tools/news_grasp_owned_process.py",
    "tools/tts/aivis_client.py",
    "tools/tts/proc.py",
)

FORBIDDEN_RAW_TERMINATION = (
    "TerminateProcess(",
    "Stop-Process",
    "taskkill",
    ".kill(",
    ".terminate(",
)

FORBIDDEN_FOCUS_OR_AUTO_OPEN = (
    "SetForegroundWindow",
    "GetForegroundWindow",
    "AppActivate",
    "Invoke-Item",
    "explorer.exe",
    "-WindowStyle Normal",
    "-WindowStyle Maximized",
)

FORBIDDEN_USER_MONITORING = (
    "GetAsyncKeyState",
    "SetWinEventHook",
    "PrintWindow",
    "BitBlt",
    "pyautogui",
    "ImageGrab.grab",
)


def _route_source(
    *, root: Path, relative_path: str, source_overrides: Mapping[str, str]
) -> str:
    if relative_path in source_overrides:
        return source_overrides[relative_path]
    path = root / relative_path
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise HumanImpactContractError("NG_HUMAN_IMPACT_ROUTE_UNAVAILABLE") from exc


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_production_human_impact(
    repo_root: Path | str,
    *,
    source_overrides: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """実production routeを読み、Human Impact契約を一括検証する。"""

    root = Path(repo_root).resolve(strict=True)
    overrides = dict(source_overrides or {})
    unknown = sorted(set(overrides) - set(PRODUCTION_ROUTE_PATHS))
    if unknown:
        raise HumanImpactContractError("NG_HUMAN_IMPACT_ROUTE_UNKNOWN")
    sources = {
        relative: _route_source(
            root=root, relative_path=relative, source_overrides=overrides
        )
        for relative in PRODUCTION_ROUTE_PATHS
    }
    combined = "\n".join(sources.values())
    if any(token in combined for token in FORBIDDEN_RAW_TERMINATION):
        raise HumanImpactContractError("NG_RAW_PROCESS_TERMINATION_FORBIDDEN")
    if any(token in combined for token in FORBIDDEN_FOCUS_OR_AUTO_OPEN):
        raise HumanImpactContractError("NG_FOCUS_OR_AUTO_OPEN_FORBIDDEN")
    if any(token in combined for token in FORBIDDEN_USER_MONITORING):
        raise HumanImpactContractError("NG_USER_MONITORING_FORBIDDEN")

    wrapper = sources["scripts/ops/run_codex_with_timeout.ps1"]
    required_job_markers = (
        "PROC_THREAD_ATTRIBUTE_JOB_LIST",
        "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE",
        "CREATE_SUSPENDED | CREATE_NO_WINDOW | EXTENDED_STARTUPINFO_PRESENT",
        "[NewsGraspOwnedJob]::CloseOwnedJob($ownedJobHandle)",
    )
    if any(marker not in wrapper for marker in required_job_markers):
        raise HumanImpactContractError("NG_OWNED_JOB_CREATION_CONTRACT_INVALID")
    job_attribute = wrapper.index("new IntPtr(PROC_THREAD_ATTRIBUTE_JOB_LIST)")
    process_creation = wrapper.index("if (!CreateProcess(")
    process_resume = wrapper.index("if (ResumeThread(")
    if not job_attribute < process_creation < process_resume:
        raise HumanImpactContractError("NG_OWNED_JOB_CREATION_ORDER_INVALID")

    start_process_statements: list[str] = []
    for relative, source in sources.items():
        if not relative.endswith(".ps1"):
            continue
        for line in source.splitlines():
            if re.search(r"\bStart-Process\b", line, flags=re.IGNORECASE):
                start_process_statements.append(line)
                if "-WindowStyle Hidden" not in line:
                    raise HumanImpactContractError("NG_FOCUS_THEFT_LAUNCH_VISIBLE")

    launcher = sources["scripts/ops/news-grasp-task-launcher.pyw"]
    if (
        "CreateToolhelp32Snapshot" in launcher
        and "for _ in range(max_hops):" not in launcher
    ):
        raise HumanImpactContractError("NG_PROCESS_OWNERSHIP_SCAN_UNBOUNDED")
    direct_runtime = sources["tools/news_grasp_direct_runtime.py"]
    if (
        "subprocess.run(" in direct_runtime
        and "creationflags" not in direct_runtime
        and "CREATE_NO_WINDOW" not in direct_runtime
    ):
        raise HumanImpactContractError("NG_DIRECT_RUNTIME_CHILD_WINDOW_UNBOUNDED")
    if "news-grasp-runner.ps1" in (
        sources["automation/news-grasp-6-40/automation.toml.template"]
        + direct_runtime
        + sources["tools/news_grasp_direct_completion.py"]
    ):
        raise HumanImpactContractError("NG_LEGACY_RUNNER_ROUTE_FORBIDDEN")

    return {
        "schemaVersion": "HUMAN_IMPACT_PRODUCTION_RECEIPT_V1",
        "status": "green",
        "noFocusTheft": True,
        "noAutoOpen": True,
        "noUserMonitoring": True,
        "ownedProcessOnly": True,
        "persistentPolling": False,
        "rawProcessTermination": False,
        "processCreationMode": "creation_time_job_membership",
        "cleanupMode": "owned_job_close",
        "startProcessCount": len(start_process_statements),
        "routeHashes": {
            relative: _sha256_text(source) for relative, source in sources.items()
        },
    }


def validate_human_impact(intent: Mapping[str, Any]) -> dict[str, Any]:
    required = {"noFocusTheft", "noAutoOpen", "noUserMonitoring", "ownedProcessOnly"}
    if not required.issubset(intent) or any(intent.get(key) is not True for key in required):
        raise HumanImpactContractError("NG_HUMAN_IMPACT_CONTRACT_INVALID")
    if intent.get("rawProcessKill") is True or intent.get("sharedProcessTermination") is True:
        raise HumanImpactContractError("NG_RAW_PROCESS_TERMINATION_FORBIDDEN")
    return {"status": "green", "noFocusTheft": True, "noAutoOpen": True, "noUserMonitoring": True, "ownedProcessOnly": True}


validate = validate_human_impact
