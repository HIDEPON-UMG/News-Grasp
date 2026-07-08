from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date, timedelta
import json
import os
from pathlib import Path


@dataclass(frozen=True)
class DayCompletion:
    date: str
    status: str
    reason: str
    evidence: tuple[str, ...] = ()


def _load_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _state_for_date(repo_root: Path, issue_date: str) -> tuple[dict, Path]:
    candidates: list[Path] = []
    env_state = os.environ.get("NEWS_GRASP_RUNNER_STATE_FILE", "").strip()
    if env_state:
        candidates.append(Path(env_state))
    candidates.extend(
        [
            Path.home() / "bin" / "news-grasp-runner-state.json",
            repo_root / "state" / "news-grasp-runner-state.json",
            repo_root / "news-grasp-runner-state.json",
            repo_root / "build" / "runner-state" / f"{issue_date}.json",
        ]
    )
    for path in candidates:
        payload = _load_json(path)
        if payload and str(payload.get("date") or issue_date) == issue_date:
            return payload, path
    return {}, candidates[0]


def _publish_complete_for_date(repo_root: Path, issue_date: str) -> tuple[dict, Path]:
    path = repo_root / "build" / "publish-complete" / f"{issue_date}.json"
    payload = _load_json(path)
    if payload and str(payload.get("date") or issue_date) == issue_date:
        return payload, path
    return {}, path


def _publish_complete_ok(payload: dict, issue_date: str) -> bool:
    return bool(payload.get("ok")) and str(payload.get("date") or issue_date) == issue_date


def _live_readiness_ok(payload: dict) -> bool:
    readiness = payload.get("live_runner_readiness")
    if not isinstance(readiness, dict) or not readiness.get("ok"):
        return False
    repo_runner = readiness.get("repo_runner") if isinstance(readiness.get("repo_runner"), dict) else {}
    live_runner = readiness.get("live_runner") if isinstance(readiness.get("live_runner"), dict) else {}
    scheduled_task = readiness.get("scheduled_task") if isinstance(readiness.get("scheduled_task"), dict) else {}
    canary = readiness.get("canary") if isinstance(readiness.get("canary"), dict) else {}
    repo_sha = str(repo_runner.get("sha256") or "")
    live_sha = str(live_runner.get("sha256") or "")
    return bool(
        repo_sha
        and live_sha
        and repo_sha == live_sha
        and scheduled_task.get("targets_live_runner")
        and canary.get("ok") is True
        and str(canary.get("status") or "") == "smoke_ok"
    )


def classify_day(repo_root: Path, issue_date: str) -> DayCompletion:
    repo_root = repo_root.resolve()
    state, state_path = _state_for_date(repo_root, issue_date)
    publish_complete, publish_complete_path = _publish_complete_for_date(repo_root, issue_date)
    publish_path = repo_root / "docs" / "publish-status.json"
    distribution_path = repo_root / "data" / "distribution" / f"{issue_date}.json"
    log_paths = sorted((repo_root / "logs").glob(f"*{issue_date}*.log")) if (repo_root / "logs").exists() else []
    incident_paths = (
        sorted((repo_root / "docs" / "incidents").glob(f"{issue_date}*"))
        if (repo_root / "docs" / "incidents").exists()
        else []
    )
    publish = _load_json(publish_path)
    distribution = _load_json(distribution_path)
    evidence = tuple(
        str(path)
        for path in (
            state_path,
            publish_complete_path,
            publish_path,
            distribution_path,
            *log_paths[:3],
            *incident_paths[:3],
        )
        if path.exists()
    )

    state_status = str(state.get("status") or "")
    publish_result = str(publish.get("result") or "")
    if state_status == "fallback_ok" or publish_result == "published_fallback_with_notice":
        return DayCompletion(
            issue_date,
            "forbidden_fallback",
            "通常日次 fallback 完走扱いは禁止",
            evidence,
        )
    if _publish_complete_ok(publish_complete, issue_date) and publish_result == "published_ok" and distribution:
        if not _live_readiness_ok(publish_complete):
            return DayCompletion(
                issue_date,
                "completion_overclaim",
                "publish_complete lacks live runner readiness: repo/live SHA + Scheduled Task target + smoke canary",
                evidence,
            )
        if state_status and state_status != "publish_complete":
            return DayCompletion(
                issue_date,
                "state_reconciliation_required",
                f"publish_complete manifest conflicts with runner state {state_status}",
                evidence,
            )
        return DayCompletion(
            issue_date,
            "complete",
            "publish-complete manifest + published_ok + distribution manifest",
            evidence,
        )
    if state_status == "publish_complete" and publish_result == "published_ok" and distribution:
        return DayCompletion(
            issue_date,
            "completion_overclaim",
            "legacy publish_complete lacks live runner readiness manifest",
            evidence,
        )
    if state_status.startswith("blocked_external") or state_status in {"blocked_external_readiness"}:
        return DayCompletion(issue_date, "typed_external", state_status, evidence)
    if state_status in {
        "blocked_internal_quality_gate",
        "blocked_repair_handler_unimplemented",
        "repair_context_scope_mismatch",
        "blocked_deterministic_repair_not_applicable",
        "repair_handler_output_scope_violation",
        "blocked_unknown_repair_class",
        "publish_failed",
        "distribution_failed",
        "failed",
        "error",
    } or state_status.startswith("blocked_"):
        return DayCompletion(issue_date, "typed_red", state_status or "blocked", evidence)
    if not state and not publish and not distribution and not log_paths and not incident_paths:
        return DayCompletion(issue_date, "no_run", "no state, publish, distribution, log, or incident evidence", evidence)
    return DayCompletion(issue_date, "unverified", state_status or publish_result or "incomplete evidence", evidence)


def audit(repo_root: Path, *, days: int = 14, today: date | None = None) -> list[DayCompletion]:
    today = today or date.today()
    return [classify_day(repo_root, (today - timedelta(days=offset)).isoformat()) for offset in range(days)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="News-Grasp daily batch completion history audit.")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    rows = audit(args.repo_root, days=args.days)
    if args.json:
        print(json.dumps([asdict(row) for row in rows], ensure_ascii=False, indent=2))
    else:
        for row in rows:
            print(f"{row.date}\t{row.status}\t{row.reason}")
    return 1 if not any(row.status == "complete" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
