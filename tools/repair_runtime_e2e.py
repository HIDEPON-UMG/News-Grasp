from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import subprocess
import sys

from tools.repair_coverage_matrix import RepairClass, classify_gate_output
from tools.repair_registry import RepairContext, repair_with_registry


TOOL_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class RepairRuntimeResult:
    gate_id: str
    initial_exit_code: int
    post_repair_exit_code: int | None
    handler_id: str
    repair_status: str
    repair_changed: bool
    repaired_artifacts: tuple[str, ...]
    final_status: str
    initial_output: str
    post_repair_output: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class CompoundRepairStep:
    name: str
    gate_id: str
    command: list[str]
    artifacts: list[str]


@dataclass(frozen=True)
class CompoundRepairPlanResult:
    final_status: str
    steps: tuple[RepairRuntimeResult, ...]
    public_actions_attempted: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _run_gate(repo_root: Path, command: list[str]) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(TOOL_ROOT) if not existing_pythonpath else str(TOOL_ROOT) + ";" + existing_pythonpath
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        command,
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


def run_registry_repair_cycle(
    *,
    repo_root: Path,
    issue: str,
    gate_id: str,
    command: list[str],
    artifacts: list[str],
) -> RepairRuntimeResult:
    first = _run_gate(repo_root, command)
    initial_output = (first.stdout or "") + (first.stderr or "")
    if first.returncode == 0:
        return RepairRuntimeResult(
            gate_id=gate_id,
            initial_exit_code=0,
            post_repair_exit_code=0,
            handler_id="",
            repair_status="not_needed",
            repair_changed=False,
            repaired_artifacts=(),
            final_status="already_green",
            initial_output=initial_output,
            post_repair_output="",
        )

    decision = classify_gate_output(gate_id, initial_output)
    if decision.repair_class != RepairClass.DETERMINISTIC_HANDLER or not decision.handler_id:
        return RepairRuntimeResult(
            gate_id=gate_id,
            initial_exit_code=first.returncode,
            post_repair_exit_code=None,
            handler_id=decision.handler_id,
            repair_status=decision.status_on_failure,
            repair_changed=False,
            repaired_artifacts=(),
            final_status="not_repairable_by_registry",
            initial_output=initial_output,
            post_repair_output="",
        )

    repair = repair_with_registry(
        RepairContext(
            repo_root=repo_root,
            issue=issue,
            handler_id=decision.handler_id,
            artifacts=artifacts,
        )
    )
    if repair.status not in {"repaired", "noop"}:
        return RepairRuntimeResult(
            gate_id=gate_id,
            initial_exit_code=first.returncode,
            post_repair_exit_code=None,
            handler_id=decision.handler_id,
            repair_status=repair.status,
            repair_changed=repair.changed,
            repaired_artifacts=repair.artifacts,
            final_status="repair_failed",
            initial_output=initial_output,
            post_repair_output=repair.message,
        )

    second = _run_gate(repo_root, command)
    post_output = (second.stdout or "") + (second.stderr or "")
    return RepairRuntimeResult(
        gate_id=gate_id,
        initial_exit_code=first.returncode,
        post_repair_exit_code=second.returncode,
        handler_id=decision.handler_id,
        repair_status=repair.status,
        repair_changed=repair.changed,
        repaired_artifacts=repair.artifacts,
        final_status="green_after_repair" if second.returncode == 0 else "still_red",
        initial_output=initial_output,
        post_repair_output=post_output,
    )


def run_compound_repair_plan(
    *,
    repo_root: Path,
    issue: str,
    steps: list[CompoundRepairStep],
    no_publish: bool,
) -> CompoundRepairPlanResult:
    results: list[RepairRuntimeResult] = []
    public_actions_attempted: list[str] = []
    for step in steps:
        result = run_registry_repair_cycle(
            repo_root=repo_root,
            issue=issue,
            gate_id=step.gate_id,
            command=step.command,
            artifacts=step.artifacts,
        )
        results.append(result)
        if result.final_status in {"already_green", "green_after_repair"}:
            continue
        if not no_publish:
            public_actions_attempted.append("fallback_publish_candidate")
        return CompoundRepairPlanResult(
            final_status="blocked_unresolved_compound_failure",
            steps=tuple(results),
            public_actions_attempted=tuple(public_actions_attempted),
        )
    return CompoundRepairPlanResult(
        final_status="green_after_compound_repair",
        steps=tuple(results),
        public_actions_attempted=tuple(public_actions_attempted),
    )


def python_gate_command(module: str, *args: str) -> list[str]:
    return [sys.executable, "-m", module, *args]
