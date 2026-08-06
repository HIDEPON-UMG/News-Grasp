from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any
from unittest.mock import patch

from tools import audit_recovery_control
from tools import daily_self_heal
from tests.helpers.current_launcher_task_adapter import observe_scheduled_task_action


ISSUE_DATE = "2026-08-01"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _run(argv: list[str], *, cwd: Path) -> str:
    completed = subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=60,
        creationflags=CREATE_NO_WINDOW,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"DCP04_LOCAL_FIXTURE_COMMAND_FAILED:{argv}:{completed.stderr}"
        )
    return completed.stdout.strip()


def _prepare_local_git_fixture(
    *, repo: Path, isolation_root: Path, perspective: str
) -> tuple[Path, Path, str]:
    runtime_parent = isolation_root / "artifacts" / "red-runtime"
    runtime_parent.mkdir(parents=True, exist_ok=True)
    fixture_root = repo.resolve()
    provenance_path = fixture_root / "data" / "deepdive-provenance" / f"{ISSUE_DATE}.json"
    existing_provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    article = (
        fixture_root / "digest" / "DeepDive" / f"{ISSUE_DATE}-DeepDive.md"
    ).resolve()
    article_sha = hashlib.sha256(article.read_bytes()).hexdigest()
    needs_binding_commit = (
        existing_provenance.get("articlePath") != str(article)
        or existing_provenance.get("articleSha256") != article_sha
    )
    if needs_binding_commit:
        existing_provenance["articlePath"] = str(article)
        existing_provenance["articleSha256"] = article_sha
        existing_provenance["sourceSetSha256"] = (
            daily_self_heal.deepdive_quality._canonical_sha256(
                existing_provenance["sources"]
            )
        )
        existing_provenance["manifestSha256"] = (
            daily_self_heal.deepdive_quality.canonical_manifest_sha256(
                existing_provenance
            )
        )
        _write_json(provenance_path, existing_provenance)

    base_head = _run(["git", "rev-parse", "HEAD"], cwd=fixture_root)
    distribution_path = fixture_root / "data" / "distribution" / f"{ISSUE_DATE}.json"
    distribution = json.loads(distribution_path.read_text(encoding="utf-8"))
    if needs_binding_commit:
        distribution["pre_publish_commit"] = base_head
        distribution["publish_commit"] = ""
        distribution["publish_commit_resolution"] = "post_push_verify"
        distribution["same_publish_contract"] = (
            "pre_publish_commit_must_equal_verified_publish_commit"
        )
        _write_json(distribution_path, distribution)

    summary_html = (
        fixture_root / "docs" / ISSUE_DATE / "summary" / "index.html"
    ).read_text(encoding="utf-8-sig", errors="replace")
    match = re.search(
        rf'https://[^"\'< >\s]+/{re.escape(ISSUE_DATE)}\.mp3[^"\'< >\s]*',
        summary_html,
    )
    if match is None:
        raise RuntimeError("DCP04_HISTORICAL_AUDIO_URL_MISSING")
    audio_url = match.group(0)
    _write_json(
        fixture_root / "build" / "tts" / "latest_audio.json",
        {"latest_audio_date": ISSUE_DATE, "latest_audio_url": audio_url},
    )
    _write_json(
        fixture_root / "build" / "tts" / "deepdive" / "latest_audio.json",
        {
            "latest_audio_date": ISSUE_DATE,
            "latest_audio_url": f"https://sealed.invalid/audio/{ISSUE_DATE}-deepdive.mp3",
        },
    )
    for relative in (
        f"build/youtube-podcast/{ISSUE_DATE}.mp4",
        f"build/youtube-podcast-deepdive/{ISSUE_DATE}.mp4",
    ):
        target = fixture_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"isolated-dcp04-fixture")
    _write_json(
        fixture_root / "build" / "youtube-podcast" / "uploads.json",
        {
            ISSUE_DATE: {
                "status": "public",
                "videoId": "dcp04-primary",
                "playlistId": "dcp04-primary-list",
            }
        },
    )
    _write_json(
        fixture_root / "build" / "youtube-podcast-deepdive" / "uploads.json",
        {
            ISSUE_DATE: {
                "status": "public",
                "videoId": "dcp04-deepdive",
                "playlistId": "dcp04-deepdive-list",
            }
        },
    )
    notification = fixture_root / "build" / "notification" / f"{ISSUE_DATE}.json"
    _write_json(
        notification,
        {
            "status": "no_subscribers",
            "ok": True,
            "date": ISSUE_DATE,
            "subscription_count": 0,
            "sent_count": 0,
        },
    )

    if needs_binding_commit:
        _run(
            ["git", "add", str(provenance_path), str(distribution_path)],
            cwd=fixture_root,
        )
        _run(
            [
                "git",
                "-c",
                "user.name=News-Grasp Red Fixture",
                "-c",
                "user.email=red-fixture@invalid.local",
                "commit",
                "--quiet",
                "-m",
                "test: bind isolated DCP04 fixture",
                "--",
                str(provenance_path),
                str(distribution_path),
            ],
            cwd=fixture_root,
        )
    head = _run(["git", "rev-parse", "HEAD"], cwd=fixture_root)
    return fixture_root, notification, head


class _SealedResponse:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def __enter__(self) -> "_SealedResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def _sealed_urlopen(repo: Path, url_or_request: object, **_kwargs: Any) -> _SealedResponse:
    url = str(getattr(url_or_request, "full_url", url_or_request))
    if url.endswith("publish-status.json"):
        return _SealedResponse(
            json.dumps({"result": "published_ok", "date": ISSUE_DATE}).encode()
        )
    if url.endswith("/sw.js"):
        return _SealedResponse((repo / "docs" / "sw.js").read_bytes())
    if "youtube.com/oembed" in url:
        decoded = urllib.parse.unquote(url)
        title = (
            f"News-Grasp DeepDive Dialogue {ISSUE_DATE}"
            if "dcp04-deepdive" in decoded
            else f"News-Grasp Daily News Briefing {ISSUE_DATE}"
        )
        return _SealedResponse(json.dumps({"title": title}).encode())
    if "youtube.com/watch" in url:
        video_id = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get(
            "v", [""]
        )[0]
        title = (
            f"News-Grasp DeepDive Dialogue {ISSUE_DATE}"
            if "deepdive" in video_id
            else f"News-Grasp Daily News Briefing {ISSUE_DATE}"
        )
        return _SealedResponse(
            f"<title>{title} - YouTube</title>{video_id}".encode()
        )
    if "youtube.com/playlist" in url:
        video_id = "dcp04-deepdive" if "deepdive" in url else "dcp04-primary"
        return _SealedResponse(video_id.encode())
    if url.rstrip("/").endswith(ISSUE_DATE + "/summary"):
        return _SealedResponse(
            (repo / "docs" / ISSUE_DATE / "summary" / "index.html").read_bytes()
        )
    if url.rstrip("/").endswith("News-Grasp"):
        return _SealedResponse((repo / "docs" / "index.html").read_bytes())
    return _SealedResponse(b"")


def _task_details(live_bootstrap: Path, task_name: str) -> dict[str, Any]:
    if task_name == "News-Grasp Bootstrap":
        return {
            "ok": True,
            "state": "Ready",
            "action_summary": (
                f'powershell.exe -File "{live_bootstrap}" -Start -SmokeTest '
                "-PollSeconds 1 -TimeoutMinutes 2 -StateFile ng-smoke-state.json "
                "-LogDir ng-smoke-logs"
            ),
            "triggers": [{"enabled": True, "start_boundary": "2026-08-05T05:55:00"}],
            "last_task_result": 0,
            "last_run_time": "2026-08-05T05:55:00",
            "next_run_time": "2026-08-06T05:55:00",
            "number_of_missed_runs": 0,
        }
    return {
        "ok": True,
        "state": "Ready",
        "action_summary": f'powershell.exe -File "{live_bootstrap}" -Start',
        "triggers": [{"enabled": True, "start_boundary": "2026-08-05T06:00:00"}],
        "last_task_result": 76,
        "last_run_time": "2026-08-05T06:00:01",
        "next_run_time": "2026-08-06T06:00:00",
        "number_of_missed_runs": 0,
    }


def observe_current_completion_consumer(
    *, repo: Path, isolation_root: Path, perspective: str
) -> dict[str, Any]:
    fixture_root, notification, fixture_head = _prepare_local_git_fixture(
        repo=repo, isolation_root=isolation_root, perspective=perspective
    )
    live_root = Path(
        tempfile.mkdtemp(
            prefix=f"dcp04-live-{perspective}-",
            dir=str(isolation_root / "artifacts" / "red-runtime"),
        )
    )
    live_root.mkdir(parents=True, exist_ok=True)
    live_paths: dict[str, Path] = {}
    for name in (
        "news-grasp-runner.ps1",
        "watch-news-grasp-runner.ps1",
        "news-grasp-bootstrap.ps1",
    ):
        source = fixture_root / "scripts" / "ops" / name
        target = live_root / name
        shutil.copy2(source, target)
        live_paths[name] = target

    runner_state_path = fixture_root / "ops" / "news-grasp-runner-state.json"
    producer_run_id = f"dcp04-{perspective}"
    producer_run_intent = "ScheduledRecoveryFull"
    lineage_process = subprocess.run(
        [
            "pwsh.exe",
            "-NoProfile",
            "-File",
            str(fixture_root / "scripts" / "ops" / "news-grasp-lineage.ps1"),
            "-ArtifactRoot",
            str(fixture_root),
            "-OpsRoot",
            str(runner_state_path.parent),
            "-IssueDate",
            ISSUE_DATE,
            "-RunIntent",
            producer_run_intent,
            "-RunId",
            producer_run_id,
        ],
        cwd=fixture_root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if lineage_process.returncode != 0:
        raise AssertionError(
            f"DCP04_PRODUCER_LINEAGE_PROCESS_FAILED:{lineage_process.stderr}"
        )
    producer_lineage = json.loads(lineage_process.stdout)
    _write_json(
        runner_state_path,
        {
            "date": ISSUE_DATE,
            "status": "publish_complete",
            "exit_code": 0,
            "run_intent": producer_run_intent,
            "run_id": producer_run_id,
            **producer_lineage,
        },
    )
    transport_calls: list[str] = []
    producer_calls: list[dict[str, Any]] = []
    readiness_calls: list[dict[str, Any]] = []
    original_git_output = daily_self_heal._git_output
    original_producer = daily_self_heal.verify_publish_complete
    original_readiness = daily_self_heal.verify_live_runner_readiness

    def sealed_git_output(root: Path, args: list[str]) -> str:
        if args and args[0] == "ls-remote":
            transport_calls.append("git_ls_remote")
            return f"{fixture_head}\trefs/heads/main"
        return original_git_output(root, args)

    def traced_readiness(**kwargs: Any) -> dict[str, Any]:
        readiness_calls.append(dict(kwargs))
        return original_readiness(**kwargs)

    def traced_producer(**kwargs: Any) -> dict[str, Any]:
        value = original_producer(**kwargs)
        producer_calls.append({"kwargs": dict(kwargs), "result": value})
        return value

    original_repo_root = audit_recovery_control.CANONICAL_REPO_ROOT
    original_state_path = audit_recovery_control.CANONICAL_RUNNER_STATE_PATH
    try:
        audit_recovery_control.CANONICAL_REPO_ROOT = fixture_root
        audit_recovery_control.CANONICAL_RUNNER_STATE_PATH = runner_state_path
        with (
            patch.object(
                audit_recovery_control,
                "_validate_artifact_executable_tree",
                return_value=fixture_head,
            ),
            patch.object(daily_self_heal, "_git_output", side_effect=sealed_git_output),
            patch.object(
                daily_self_heal,
                "wait_for_deploy_workflow",
                side_effect=lambda **_kwargs: (
                    transport_calls.append("github_deploy_workflow")
                    or {"ok": True, "reason": "", "head_sha": fixture_head}
                ),
            ),
            patch.object(
                daily_self_heal,
                "verify_pages_build",
                side_effect=lambda **_kwargs: (
                    transport_calls.append("github_pages_build")
                    or {"ok": True, "reason": "", "commit": fixture_head}
                ),
            ),
            patch.object(
                daily_self_heal.urllib.request,
                "urlopen",
                side_effect=lambda value, **kwargs: (
                    transport_calls.append("http")
                    or _sealed_urlopen(fixture_root, value, **kwargs)
                ),
            ),
            patch.object(
                daily_self_heal,
                "_scheduled_task_details",
                side_effect=lambda **kwargs: _task_details(
                    live_paths["news-grasp-bootstrap.ps1"],
                    str(kwargs.get("task_name") or ""),
                ),
            ),
            patch.object(
                daily_self_heal,
                "_default_live_runner_path",
                return_value=live_paths["news-grasp-runner.ps1"],
            ),
            patch.object(
                daily_self_heal,
                "_default_live_watcher_path",
                return_value=live_paths["watch-news-grasp-runner.ps1"],
            ),
            patch.object(
                daily_self_heal,
                "_default_live_bootstrap_path",
                return_value=live_paths["news-grasp-bootstrap.ps1"],
            ),
            patch.object(
                daily_self_heal,
                "_run_live_startup_canary",
                side_effect=lambda **_kwargs: (
                    transport_calls.append(
                        "startup_canary_subprocess_sealed_no_authority_consumption"
                    )
                    or {
                        "ok": True,
                        "status": "smoke_ok",
                        "fixtureIsolation": True,
                        "authorityConsumed": False,
                    }
                ),
            ),
            patch.object(
                daily_self_heal,
                "verify_live_runner_readiness",
                side_effect=traced_readiness,
            ),
            patch.object(
                daily_self_heal,
                "verify_publish_complete",
                side_effect=traced_producer,
            ),
        ):
            completion = audit_recovery_control._verify_same_date_completion(
                issue_date=ISSUE_DATE,
                payload={"verificationWaitSec": 0, "verificationPollSec": 1},
                expected_run_intent="ScheduledRecoveryFull",
            )
    finally:
        audit_recovery_control.CANONICAL_REPO_ROOT = original_repo_root
        audit_recovery_control.CANONICAL_RUNNER_STATE_PATH = original_state_path

    if not producer_calls:
        raise AssertionError("DCP04_ACTUAL_PRODUCER_NOT_CALLED")
    producer_manifest = dict(producer_calls[-1]["result"])
    if not isinstance(completion, dict) or producer_manifest.get("ok") is not True:
        raise AssertionError(
            f"DCP04_ACTUAL_COMPLETION_BASELINE_RED:{producer_manifest}"
        )
    body = dict(completion)
    body.pop("receiptSha256", None)
    if perspective == "adversarial":
        body.update(
            {
                "artifactRoot": str(fixture_root / "substituted-artifacts"),
                "opsRoot": str(fixture_root / "ops"),
                "dailyRootId": "daily-root-producer-a",
                "rootOperationId": "root-operation-verifier-b",
                "producerRunIntent": "ScheduledProduction",
                "verifierRunIntent": "ScheduledRecoveryFull",
            }
        )
    elif perspective == "recovery":
        body.update(
            {
                "artifactRoot": str(fixture_root),
                "opsRoot": str(fixture_root / "ops"),
                "dailyRootId": "daily-root-verifier",
                "producerDailyRootId": "daily-root-recovery-other",
                "rootOperationId": "root-operation-verifier",
                "producerRootOperationId": "root-operation-recovery-other",
                "producerRunIntent": "ScheduledRecoveryFull",
                "verifierRunIntent": "ScheduledRecoveryFull",
            }
        )
    sealed_completion = audit_recovery_control._sealed(body)
    task_action = observe_scheduled_task_action()
    with patch.object(
        audit_recovery_control, "CANONICAL_REPO_ROOT", fixture_root
    ), patch.object(
        audit_recovery_control,
        "CANONICAL_RUNNER_STATE_PATH",
        runner_state_path,
    ):
        accepted = audit_recovery_control.same_date_completion_green(
            ISSUE_DATE, sealed_completion
        )
    observation = {
        "schemaVersion": "CURRENT_COMPLETION_CONSUMER_OBSERVATION_V3",
        "returnCode": 0,
        "accepted": accepted,
        "result": sealed_completion,
        "manifest": sealed_completion,
        "producerManifest": producer_manifest,
        "producerManifestSha256": _sha(producer_manifest),
        "transportCalls": transport_calls,
        "producerCallCount": len(producer_calls),
        "producerLineageProcessReturnCode": lineage_process.returncode,
        "producerLineageSource": str(
            fixture_root / "scripts" / "ops" / "news-grasp-lineage.ps1"
        ),
        "readinessConsumerCallCount": len(readiness_calls),
        "qualityConsumer": "actual_subprocess_tools.validate_daily_quality",
        "deepdiveConsumer": "actual_deepdive_quality.audit_issue",
        "gitLocalConsumer": "actual_git_rev_parse_archive_tree_ancestor",
        "scheduledTaskAction": task_action,
        "input": {
            "producerManifestSha256": _sha(producer_manifest),
            "taskAction": task_action.get("result"),
            "completionCandidate": sealed_completion,
        },
        "consumerSources": [
            {
                "path": str(repo / "tools" / "daily_self_heal.py"),
                "symbol": "verify_publish_complete",
            },
            {
                "path": str(repo / "tools" / "audit_recovery_control.py"),
                "symbol": "_verify_same_date_completion+same_date_completion_green",
            },
        ],
    }
    return observation
