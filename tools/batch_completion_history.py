from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date, timedelta
import json
import os
from pathlib import Path
import re

from tools.daily_self_heal import live_runner_readiness_manifest_ok


_START_RE = re.compile(r"^\[(?P<timestamp>[^]]+)] news-grasp-runner\.ps1 start \((?P<attrs>.*)\)$")
_TERMINAL_GATE_RE = re.compile(r"(?:gate\s+FAILED|blocked_[a-z0-9_]+)", re.IGNORECASE)


def _parse_start_attrs(text: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for part in text.split(","):
        key, separator, value = part.strip().partition("=")
        if separator:
            attrs[key.strip()] = value.strip()
    return attrs


def parse_log_attempts(log_path: Path, issue_date: str) -> list[dict]:
    """1日ログを start marker 単位へ分け、scheduled と recovery を混同せず返す。"""
    lines = log_path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    starts: list[tuple[int, re.Match[str]]] = []
    for line_number, line in enumerate(lines, start=1):
        match = _START_RE.match(line)
        if match and match.group("timestamp").startswith(issue_date):
            starts.append((line_number, match))

    attempts: list[dict] = []
    for index, (line_start, match) in enumerate(starts):
        line_end = starts[index + 1][0] - 1 if index + 1 < len(starts) else len(lines)
        attrs = _parse_start_attrs(match.group("attrs"))
        resume_from_stage = attrs.get("resume_from_stage", "")
        recover = attrs.get("recover", "False").casefold() == "true"
        timestamp = match.group("timestamp")
        is_scheduled = index == 0 and timestamp[11:16] == "06:00" and not recover and not resume_from_stage
        run_id = attrs.get("run_id", "")
        range_lines = lines[line_start - 1 : line_end]
        timestamp_prefix = f"[{issue_date} "
        runner_lines = [line for line in range_lines if line.startswith(timestamp_prefix)]
        terminal_gate = next((line for line in runner_lines if _TERMINAL_GATE_RE.search(line)), "")
        terminal_state = "publish_complete" if any("publish_complete" in line.casefold() for line in runner_lines) else ""
        attempts.append(
            {
                "kind": "scheduled" if is_scheduled else "recovery",
                "run_id": run_id or f"legacy:{issue_date}:line{line_start}",
                "run_id_status": "present" if run_id else "legacy_missing",
                "timestamp": timestamp,
                "line_start": line_start,
                "line_end": line_end,
                "log_path": str(log_path),
                "recover": recover,
                "resume_from_stage": resume_from_stage,
                "terminal_gate": terminal_gate,
                "terminal_state": terminal_state,
            }
        )
    return attempts


@dataclass(frozen=True)
class DayCompletion:
    date: str
    status: str
    reason: str
    evidence: tuple[str, ...] = ()
    scheduled_attempt: dict | None = None
    recovery_attempts: tuple[dict, ...] = ()
    public_status: str = "unknown"
    incident_evidence: tuple[str, ...] = ()
    residuals: tuple[str, ...] = ()


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
    return live_runner_readiness_manifest_ok(readiness) if isinstance(readiness, dict) else False


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
                "publish_complete lacks live ops readiness: repo/live runner SHA + repo/live watcher SHA + repo/live bootstrap SHA + Runner 06:00 production action/NextRunTime/missed-run + direct runner pre-run bootstrap interlock/reexec + Bootstrap 05:55 smoke contract/fresh canary",
                evidence,
            )
        if state_status and state_status != "publish_complete":
            return DayCompletion(
                issue_date,
                "state_reconciliation_required",
                f"publish_complete manifest conflicts with runner state {state_status}",
                evidence,
            )
        readiness = publish_complete.get("live_runner_readiness")
        scheduled_attempt = dict(readiness.get("last_scheduled_attempt") or {}) if isinstance(readiness, dict) else {}
        if scheduled_attempt.get("status") == "failed" or publish_complete.get("scheduled_attempt_status") == "failed_then_recovered":
            return DayCompletion(
                issue_date,
                "recovered_after_failed_schedule",
                "06:00 scheduled attempt failed; recovery reached publish_complete and public Green",
                evidence,
                scheduled_attempt=scheduled_attempt,
                recovery_attempts=({"status": str(publish_complete.get("recovery_attempt_status") or "succeeded")},),
                public_status=str(publish_complete.get("public_status") or "green"),
                incident_evidence=tuple(str(path) for path in incident_paths),
            )
        return DayCompletion(
            issue_date,
            "complete",
            "publish-complete manifest + published_ok + distribution manifest",
            evidence,
            public_status=str(publish_complete.get("public_status") or "green"),
            incident_evidence=tuple(str(path) for path in incident_paths),
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


def _date_range(start_date: str, end_date: str) -> list[str]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    return [(start + timedelta(days=offset)).isoformat() for offset in range((end - start).days + 1)]


def build_weekly_audit(
    *,
    repo_root: Path,
    start_date: str,
    end_date: str,
    daily_inputs: dict[str, dict],
    log_dir: Path,
    evidence_source: str,
) -> dict:
    """凍結済み scheduler evidence と実ログ range を結合して週次監査を作る。"""
    days: list[dict] = []
    for issue_date in _date_range(start_date, end_date):
        frozen = dict(daily_inputs.get(issue_date) or {})
        log_path = log_dir / f"{issue_date}.log"
        attempts = parse_log_attempts(log_path, issue_date) if log_path.exists() else []
        scheduled_attempt = next((attempt for attempt in attempts if attempt["kind"] == "scheduled"), {})
        recovery_attempts = [attempt for attempt in attempts if attempt["kind"] == "recovery"]
        last_result = frozen.get("scheduled_last_task_result")
        if last_result == 0:
            scheduled_status = "succeeded"
        elif last_result is None:
            scheduled_status = "unknown"
        else:
            scheduled_status = "failed"
        scheduled = {
            **scheduled_attempt,
            "status": scheduled_status,
            "last_task_result": last_result,
            "evidence_source": evidence_source,
        }
        days.append(
            {
                "date": issue_date,
                "scheduled_attempt": scheduled,
                "six_forty_classification": str(frozen.get("six_forty_classification") or "unknown"),
                "first_terminal_gate": str(
                    frozen.get("first_terminal_gate") or scheduled_attempt.get("terminal_gate") or "unknown"
                ),
                "recovery_attempts": recovery_attempts,
                "recovery_status": str(frozen.get("recovery_status") or "unknown"),
                "public_status": str(frozen.get("public_status") or "unknown"),
                "incident_evidence": list(frozen.get("incident_evidence") or []),
                "residuals": list(frozen.get("residuals") or []),
            }
        )

    scheduled_statuses = [row["scheduled_attempt"]["status"] for row in days]
    regression = len(days) == 7 and all(status != "succeeded" for status in scheduled_statuses)
    return {
        "schema": "NEWS_GRASP_WEEKLY_BATCH_COMPLETENESS_AUDIT_V1",
        "start_date": start_date,
        "end_date": end_date,
        "evidence_source": evidence_source,
        "weekly_scheduled_attempt_status": "regression" if regression else "mixed_or_green",
        "weekly_issue_code": "weekly_scheduled_completion_regression" if regression else "none",
        "days": days,
    }


def weekly_audit_markdown(payload: dict) -> str:
    lines = [
        "# Weekly Batch Completeness Audit",
        "",
        f"- 期間: {payload['start_date']}..{payload['end_date']}",
        f"- scheduled weekly status: `{payload['weekly_scheduled_attempt_status']}`",
        f"- issue code: `{payload['weekly_issue_code']}`",
        f"- evidence source: `{payload['evidence_source']}`",
        "",
        "| date | 06:00 scheduled attempt | 6:40分類 | 初回 terminal gate | recovery | public | incident | 残存バグ |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in payload["days"]:
        scheduled = row["scheduled_attempt"]
        scheduled_text = f"{scheduled['status']} (LastTaskResult={scheduled.get('last_task_result')})"
        incident = "<br>".join(row["incident_evidence"]) or "なし"
        residuals = "<br>".join(row["residuals"]) or "なし"
        lines.append(
            "| {date} | {scheduled} | {six_forty} | {terminal} | {recovery} | {public} | {incident} | {residuals} |".format(
                date=row["date"],
                scheduled=scheduled_text,
                six_forty=row["six_forty_classification"],
                terminal=row["first_terminal_gate"],
                recovery=row["recovery_status"],
                public=row["public_status"],
                incident=incident,
                residuals=residuals,
            )
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="News-Grasp daily batch completion history audit.")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--weekly-input", type=Path)
    parser.add_argument("--log-dir", type=Path, default=Path.home() / "bin" / "news-grasp-logs")
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-markdown", type=Path)
    args = parser.parse_args(argv)

    if args.weekly_input:
        if not args.start_date or not args.end_date:
            parser.error("--weekly-input requires --start-date and --end-date")
        input_payload = _load_json(args.weekly_input)
        result = build_weekly_audit(
            repo_root=args.repo_root,
            start_date=args.start_date,
            end_date=args.end_date,
            daily_inputs=dict(input_payload.get("days") or {}),
            log_dir=args.log_dir,
            evidence_source=str(input_payload.get("evidence_source") or args.weekly_input),
        )
        if args.output_json:
            args.output_json.parent.mkdir(parents=True, exist_ok=True)
            args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.output_markdown:
            args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
            args.output_markdown.write_text(weekly_audit_markdown(result), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result["weekly_scheduled_attempt_status"] == "regression" else 0

    rows = audit(args.repo_root, days=args.days)
    if args.json:
        print(json.dumps([asdict(row) for row in rows], ensure_ascii=False, indent=2))
    else:
        for row in rows:
            print(f"{row.date}\t{row.status}\t{row.reason}")
    return 1 if not any(row.status == "complete" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
