from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


JST = timezone(timedelta(hours=9), "JST")


CONDITION_IDS = (
    "entry_control_plane",
    "input_inventory",
    "model_route_authority",
    "artifact_generation_contract",
    "quality_repair_routing",
    "dry_public_boundary",
    "production_completion_authority",
    "bounded_slo_control",
    "post_publish_issue_boundary",
    "external_dependency_boundary",
)


@dataclass(frozen=True)
class Row:
    condition_id: str
    status: str
    failure_destination: str
    evidence: list[str]
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "conditionId": self.condition_id,
            "status": self.status,
            "failureDestination": self.failure_destination,
            "evidence": self.evidence,
            "reason": self.reason,
        }


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return ""


def _status(ok: bool, *, missing_reason: str = "") -> tuple[str, str]:
    if ok:
        return "green", "deterministic predicates matched"
    return "red", missing_reason or "deterministic predicate failed"


def _has_all(text: str, fragments: tuple[str, ...]) -> bool:
    return all(fragment in text for fragment in fragments)


def _run_json(command: list[str], *, cwd: Path) -> tuple[int, dict[str, Any] | None, str]:
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60,
        check=False,
    )
    try:
        return completed.returncode, json.loads(completed.stdout), completed.stdout
    except json.JSONDecodeError:
        return completed.returncode, None, completed.stdout


def _live_task_conflicts() -> tuple[str, list[str]]:
    if sys.platform != "win32":
        return "yellow", ["live scheduled task check skipped on non-Windows"]
    script = r"""
$names = @('News-Grasp Production','News-Grasp Bootstrap','News-Grasp Deadman','News-Grasp Runner')
$rows = foreach($name in $names) {
  $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
  if($null -ne $task) {
    [pscustomobject]@{ TaskName=$task.TaskName; State=[string]$task.State; Enabled=[bool]($task.State -ne 'Disabled') }
  }
}
$rows | ConvertTo-Json -Depth 3
"""
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=20,
        check=False,
    )
    if completed.returncode != 0:
        return "yellow", [f"scheduled task query failed rc={completed.returncode}"]
    try:
        value = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError:
        return "yellow", ["scheduled task query returned non-json"]
    rows = value if isinstance(value, list) else [value]
    enabled = [str(row.get("TaskName")) for row in rows if row.get("Enabled") is True]
    if enabled:
        return "red", [f"duplicate scheduler enabled: {', '.join(enabled)}"]
    return "green", ["News-Grasp scheduled tasks disabled or absent"]


def evaluate(
    *,
    repo_root: Path,
    issue_date: str,
    installed_automation: Path | None = None,
    check_live_tasks: bool = False,
    run_red_suite_coverage: bool = False,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    template = _read(repo_root / "automation" / "news-grasp-6-40" / "automation.toml.template")
    skill = _read(repo_root / "automation" / "skills" / "news-grasp-direct-mainline" / "SKILL.md")
    title_control = _read(repo_root / "tools" / "news_grasp_title_control.py")
    completion_guard = _read(repo_root / "tools" / "news_grasp_completion_guard.py")
    automation_guard = _read(repo_root / "automation" / "news-grasp-6-40" / "completion_guard.py")
    publish_inventory = _read(repo_root / "tools" / "publish_inventory.py")
    daily_quality = _read(repo_root / "tools" / "validate_daily_quality.py")
    deepdive_quality = _read(repo_root / "tools" / "deepdive_quality.py")
    model_policy = _read(repo_root / "tools" / "model_policy.py")
    completion_guard_exists = (
        repo_root / "automation" / "news-grasp-6-40" / "completion_guard.py"
    ).exists()

    installed_text = _read(installed_automation) if installed_automation else ""
    entry_ok = _has_all(
        template,
        (
            'name = "News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開"',
            'rrule = "RRULE:FREQ=DAILY;BYHOUR=6;BYMINUTE=0;BYSECOND=0"',
            'model = "gpt-5.6-luna"',
            'reasoning_effort = "max"',
            "automation は監査バッチではありません",
            "$news-grasp-direct-mainline",
            "既存 News-Grasp runner も使いません",
        ),
    ) and "stdout_projection" not in template
    entry_status, entry_reason = _status(entry_ok, missing_reason="temporary mainline automation template drift")
    entry_evidence = ["automation/news-grasp-6-40/automation.toml.template"]
    if installed_text:
        installed_ok = _has_all(
            installed_text,
            (
                'name = "News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開"',
                'rrule = "RRULE:FREQ=DAILY;BYHOUR=6;BYMINUTE=0;BYSECOND=0"',
                'model = "gpt-5.6-luna"',
                'reasoning_effort = "max"',
                "$news-grasp-direct-mainline",
            ),
        )
        entry_evidence.append(str(installed_automation))
        if not installed_ok and entry_status == "green":
            entry_status, entry_reason = "red", "installed automation drift"
    if check_live_tasks:
        task_status, task_evidence = _live_task_conflicts()
        entry_evidence.extend(task_evidence)
        if task_status == "red":
            entry_status, entry_reason = "red", "duplicate scheduled task conflict"
        elif task_status == "yellow" and entry_status == "green":
            entry_status, entry_reason = "yellow", "live scheduled task status unverified"

    rows: list[Row] = [
        Row("entry_control_plane", entry_status, "fix_now", entry_evidence, entry_reason)
    ]

    input_ok = _has_all(
        publish_inventory + skill,
        (
            "def scheduled_category_ids",
            "scheduled_category_ids(issue_date)",
            "CATEGORY_PATHS",
        ),
    )
    st, reason = _status(input_ok, missing_reason="scheduled category inventory contract missing")
    rows.append(Row("input_inventory", st, "fix_now", ["tools/publish_inventory.py", "automation/skills/news-grasp-direct-mainline/SKILL.md"], reason))

    model_ok = _has_all(template + skill + model_policy, ("gpt-5.6-luna", "gpt-5.6-sol", "reasoning max", "reasoning high"))
    st, reason = _status(model_ok, missing_reason="model route authority contract missing")
    rows.append(Row("model_route_authority", st, "fix_now", ["automation/news-grasp-6-40/automation.toml.template", "tools/model_policy.py"], reason))

    artifact_ok = _has_all(
        template + skill + publish_inventory,
        (
            "required_digest_artifacts",
            "required_published_artifacts",
            "required_distribution_artifacts",
            "Daily audio script",
            "DeepDive audio",
            "distribution manifest",
            "docs/publish-status.json",
        ),
    )
    st, reason = _status(artifact_ok, missing_reason="artifact generation contract missing")
    rows.append(Row("artifact_generation_contract", st, "fix_now", ["automation/skills/news-grasp-direct-mainline/SKILL.md", "tools/publish_inventory.py"], reason))

    quality_status = "green"
    quality_reason = "deterministic predicates matched"
    quality_evidence = ["automation/skills/news-grasp-direct-mainline/SKILL.md", "tools/validate_daily_quality.py", "tools/deepdive_quality.py"]
    if not _has_all(skill + daily_quality + deepdive_quality, ("--require-deepdive", "provenance", "dialogue", "rendered")):
        quality_status, quality_reason = "red", "direct shared-quality route drift"
    if run_red_suite_coverage:
        rc, value, output = _run_json([sys.executable, "-m", "tools.deepdive_red_suite_coverage"], cwd=repo_root)
        quality_evidence.append("python -m tools.deepdive_red_suite_coverage")
        if rc != 0 or not isinstance(value, dict) or value.get("status") != "Green":
            quality_status, quality_reason = "red", f"red suite coverage failed rc={rc}: {output[:200]}"
    rows.append(Row("quality_repair_routing", quality_status, "fix_now", quality_evidence, quality_reason))

    dry_ok = _has_all(skill + template, ("NoPublish", "fallback", "旧 runner", "public incomplete")) and not any(
        command in template
        for command in ("news_grasp_runner.py", "news_grasp_nopublish.py", "news-grasp-runner.ps1")
    )
    st, reason = _status(dry_ok, missing_reason="direct public boundary missing")
    rows.append(Row("dry_public_boundary", st, "fix_now", ["automation/skills/news-grasp-direct-mainline/SKILL.md", "automation/news-grasp-6-40/automation.toml.template"], reason))

    completion_ok = _has_all(
        template + skill + completion_guard + automation_guard,
        (
            "direct_public_v1",
            "evaluate_direct_public",
            "validate_daily_quality",
            "--require-deepdive",
            "fallback",
            "NoPublish",
            "publish_commit",
            "post_publish_issue_list",
        ),
    ) and completion_guard_exists
    st, reason = _status(completion_ok, missing_reason="production completion authority missing")
    rows.append(Row("production_completion_authority", st, "recover_now", ["automation/news-grasp-6-40/completion_guard.py", "tools/news_grasp_completion_guard.py"], reason))

    slo_ok = _has_all(template + skill, ("45 分", "75 分", "90 分", "exact public successor"))
    st, reason = _status(slo_ok, missing_reason="bounded SLO control missing")
    rows.append(Row("bounded_slo_control", st, "recover_now", ["automation/news-grasp-6-40/automation.toml.template", "automation/skills/news-grasp-direct-mainline/SKILL.md"], reason))

    post_ok = _has_all(template + skill, ("post_publish_issue_list", "公開作業を止めない"))
    st, reason = _status(post_ok, missing_reason="post-publish issue boundary missing")
    post_ok = post_ok and _has_all(title_control, ("post_publish_issue_list", "updated", "already_ok", "unavailable", "failed", "skipped"))
    if not post_ok:
        st, reason = "red", "post-publish issue boundary missing"
    rows.append(Row("post_publish_issue_boundary", st, "post_publish_issue", ["automation/news-grasp-6-40/automation.toml.template", "automation/skills/news-grasp-direct-mainline/SKILL.md", "tools/news_grasp_title_control.py"], reason))

    external_ok = _has_all(template + skill, ("OAuth", "2FA", "quota", "外部障害", "surfaceだけ"))
    st, reason = _status(external_ok, missing_reason="external blocker boundary missing")
    rows.append(Row("external_dependency_boundary", st, "external_blocker", ["automation/news-grasp-6-40/automation.toml.template", "automation/skills/news-grasp-direct-mainline/SKILL.md"], reason))

    status_set = {row.status for row in rows}
    if "red" in status_set:
        viability = "viability_red"
    elif "yellow" in status_set:
        viability = "viability_yellow"
    else:
        viability = "viability_green"
    return {
        "schemaVersion": "NEWS_GRASP_COMPLETION_VIABILITY_V1",
        "issueDate": issue_date,
        "repoRoot": str(repo_root),
        "createdAt": datetime.now(JST).isoformat(),
        "viability": viability,
        "rows": [row.as_dict() for row in rows],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--issue-date", default=datetime.now(JST).date().isoformat())
    parser.add_argument("--installed-automation", type=Path, default=Path.home() / ".codex" / "automations" / "news-grasp-6-40" / "automation.toml")
    parser.add_argument("--check-live-tasks", action="store_true")
    parser.add_argument("--run-red-suite-coverage", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = evaluate(
        repo_root=args.repo_root,
        issue_date=args.issue_date,
        installed_automation=args.installed_automation,
        check_live_tasks=args.check_live_tasks,
        run_red_suite_coverage=args.run_red_suite_coverage,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result["viability"] == "viability_green" else 2


if __name__ == "__main__":
    raise SystemExit(main())
