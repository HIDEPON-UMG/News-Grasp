#!/usr/bin/env python3
"""Daily runner diagnosis, alerting, and publish verification helpers."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse


ALERT_STATUSES = {
    "content_failed",
    "exhausted",
    "failed",
    "fallback_ok",
    "no_run_detected",
    "publish_failed",
    "stale",
}


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def compare_files(repo_path: Path, live_path: Path) -> dict:
    repo_exists = repo_path.exists()
    live_exists = live_path.exists()
    repo_sha = sha256_file(repo_path) if repo_exists else None
    live_sha = sha256_file(live_path) if live_exists else None
    return {
        "repo_path": str(repo_path),
        "live_path": str(live_path),
        "repo_exists": repo_exists,
        "live_exists": live_exists,
        "repo_sha256": repo_sha,
        "live_sha256": live_sha,
        "synced": bool(repo_exists and live_exists and repo_sha == live_sha),
    }


def normalize_failure_signature(
    *, gate_id: str, error_code: str, artifact_identity: str = "", url_or_category: str = ""
) -> str:
    host_or_category = url_or_category.strip().lower()
    if "://" in host_or_category:
        host_or_category = urlparse(host_or_category).netloc.lower()
    parts = [
        gate_id.strip().lower(),
        error_code.strip().lower(),
        artifact_identity.strip().lower(),
        host_or_category,
    ]
    return "|".join(p or "-" for p in parts)


def classify_phase0(snapshot: dict) -> dict:
    scheduler = snapshot.get("scheduler") or snapshot.get("scheduled_task") or {}
    state = snapshot.get("state") or snapshot.get("runner") or {}
    repo_bin = snapshot.get("repo_bin") or snapshot.get("bin") or {}
    git = snapshot.get("git") or {}
    pages = snapshot.get("pages") or {}
    logs = snapshot.get("logs") or {}
    content = snapshot.get("content") or {}
    expected_date = snapshot.get("expected_date")
    last_result = scheduler.get("last_result", scheduler.get("last_task_result"))

    if not scheduler.get("exists", True):
        return {"root_cause": "scheduled_task_missing", "layer": "scheduler"}
    if scheduler.get("last_run_missing") or scheduler.get("days_since_last_run", 0) >= 1:
        return {"root_cause": "no_run_detected", "layer": "scheduler"}
    if not logs.get("runner_invoked", True):
        return {"root_cause": "runner_not_started", "layer": "runner"}
    if repo_bin and repo_bin.get("synced") is False:
        return {"root_cause": "bin_drift", "layer": "runner_sync"}
    if state.get("status") == "running" and (
        state.get("process_alive") is False
        or (expected_date and state.get("date") and state.get("date") != expected_date)
    ):
        return {"root_cause": "stale_runner", "layer": "watcher"}
    if git.get("dirty_required_files"):
        return {"root_cause": "uncommitted_required_changes", "layer": "git"}
    if git.get("local_head") and git.get("remote_head") and git["local_head"] != git["remote_head"]:
        return {"root_cause": "push_not_reflected", "layer": "git"}
    if git.get("push_failed"):
        return {"root_cause": "push_failed", "layer": "git"}
    if pages.get("deployment_success") is False or pages.get("public_sentinel_ok") is False:
        return {"root_cause": "pages_not_reflected", "layer": "pages"}
    if content.get("gate_failed"):
        return {
            "root_cause": "content_gate_failed",
            "layer": "content",
            "gate_id": content.get("gate_id", ""),
        }
    if last_result not in (None, 0):
        return {"root_cause": "runner_failed", "layer": "runner"}
    return {"root_cause": "no_issue_detected", "layer": "none"}


def evaluate_deadman(
    *,
    state: dict | None,
    now: datetime,
    expected_date: str,
    max_ok_age_hours: int,
) -> dict:
    state = state or {}
    status = str(state.get("status") or "no_run_detected")
    updated = _parse_dt(state.get("updated_at"))
    state_date = str(state.get("date") or "")

    if status in ALERT_STATUSES:
        return {"alert": True, "reason": status, "status": status}
    if status != "ok":
        return {"alert": True, "reason": "no_ok_state", "status": status}
    if state_date != expected_date:
        return {"alert": True, "reason": "ok_not_for_expected_date", "status": status}
    if updated is None:
        return {"alert": True, "reason": "ok_without_timestamp", "status": status}
    if now - updated > timedelta(hours=max_ok_age_hours):
        return {"alert": True, "reason": "ok_too_old", "status": status}
    return {"alert": False, "reason": "", "status": status}


def emit_alert(record: dict, *, alert_log: Path, marker_path: Path, webhook_url: str = "") -> dict:
    alert_log.parent.mkdir(parents=True, exist_ok=True)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    key = f"{record.get('date','')}|{record.get('reason','')}|{record.get('status','')}"
    if marker_path.exists():
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            marker = {}
        if marker.get("key") == key:
            return {"sent": False, "duplicate": True, "key": key}

    payload = {**record, "key": key, "alerted_at": datetime.now(timezone.utc).isoformat()}
    with alert_log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    marker_path.write_text(json.dumps({"key": key}, ensure_ascii=False, indent=2), encoding="utf-8")

    if webhook_url:
        data = json.dumps({"text": f"News-Grasp daily alert: {key}"}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as res:  # noqa: S310 - operator configured URL
            res.read()
    return {"sent": True, "duplicate": False, "key": key}


def _git_output(repo_root: Path, args: list[str]) -> str:
    cp = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if cp.returncode != 0:
        raise RuntimeError((cp.stderr or cp.stdout).strip())
    return cp.stdout.strip()


def _latest_audio_for_publish(repo_root: Path, date: str) -> dict[str, str] | None:
    latest_path = repo_root / "build" / "tts" / "latest_audio.json"
    if not latest_path.exists():
        return None
    try:
        data = json.loads(latest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if data.get("latest_audio_date") != date:
        return None
    url = str(data.get("latest_audio_url") or "")
    if not url:
        return {"latest_audio_date": date, "latest_audio_url": ""}
    return {"latest_audio_date": date, "latest_audio_url": url}


def _fetch_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=20) as res:  # noqa: S310 - fixed public URL from runner config
        return res.read().decode("utf-8-sig", errors="replace")


def _url_head_ok(url: str) -> bool:
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=20) as res:  # noqa: S310 - fixed public URL from runner config
        return int(getattr(res, "status", 200)) == 200


def verify_public_audio(*, repo_root: Path, date: str, public_base_url: str) -> dict:
    latest = _latest_audio_for_publish(repo_root, date)
    if latest is None:
        return {"checked": False, "ok": True, "reason": "no_audio_for_date"}
    audio_url = latest.get("latest_audio_url", "")
    if not audio_url:
        return {"checked": True, "ok": False, "reason": "audio_url_missing", "latest_audio_date": date}
    try:
        if not _url_head_ok(audio_url):
            return {"checked": True, "ok": False, "reason": "audio_url_not_200", "latest_audio_url": audio_url}
        base = public_base_url.rstrip("/") + "/"
        pages = {
            "home": base,
            "summary": urljoin(base, f"{date}/summary/"),
        }
        missing_from = [
            name
            for name, url in pages.items()
            if audio_url not in _fetch_text(url)
        ]
    except (urllib.error.URLError, TimeoutError, UnicodeDecodeError) as exc:
        return {
            "checked": True,
            "ok": False,
            "reason": "public_audio_verification_failed",
            "detail": str(exc),
            "latest_audio_url": audio_url,
        }
    if missing_from:
        return {
            "checked": True,
            "ok": False,
            "reason": "public_audio_missing",
            "missing_from": missing_from,
            "latest_audio_url": audio_url,
        }
    return {"checked": True, "ok": True, "latest_audio_url": audio_url}


def _load_podcast_row(state_path: Path, date: str) -> dict:
    if not state_path.exists():
        return {}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"_state_error": "podcast_state_corrupt"}
    row = data.get(date)
    return row if isinstance(row, dict) else {}


def _fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=20) as res:  # noqa: S310 - fixed public URL
        return json.loads(res.read().decode("utf-8-sig"))


def _title_from_watch_html(html: str, expected: str) -> str:
    match = re.search(r"<title>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if match:
        title = re.sub(r"\s+", " ", match.group(1)).strip()
        suffix = " - YouTube"
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
        if title:
            return title
    if expected in html:
        return expected
    return ""


def verify_podcast(
    *,
    date: str,
    state_path: Path,
    wait_sec: int = 0,
    poll_sec: int = 30,
    expected_title: str | None = None,
) -> dict:
    expected = expected_title or f"News-Grasp Daily News Briefing {date}"
    deadline = time.monotonic() + max(0, wait_sec)
    last: dict = {}
    while True:
        row = _load_podcast_row(state_path, date)
        if row.get("_state_error"):
            return {"ok": False, "reason": row["_state_error"], "state": str(state_path)}
        video_id = str(row.get("videoId") or "")
        playlist_id = str(row.get("playlistId") or "")
        status = str(row.get("status") or row.get("privacyStatus") or "")
        if not video_id:
            return {"ok": False, "reason": "public_podcast_missing", "state": str(state_path)}
        if status and status != "public":
            last = {"ok": False, "reason": "podcast_pending", "videoId": video_id, "status": status}
        else:
            try:
                watch_url = f"https://www.youtube.com/watch?v={quote(video_id)}"
                oembed_url = f"https://www.youtube.com/oembed?url={quote(watch_url, safe='')}&format=json"
                verification = "oembed_watch_playlist"
                try:
                    oembed = _fetch_json(oembed_url)
                    actual_title = str(oembed.get("title") or "")
                    if actual_title != expected:
                        return {
                            "ok": False,
                            "reason": "podcast_title_mismatch",
                            "videoId": video_id,
                            "expected_title": expected,
                            "actual_title": actual_title,
                        }
                except urllib.error.HTTPError as exc:
                    if exc.code != 401:
                        raise
                    actual_title = ""
                    verification = "watch_playlist_fallback"
                watch_html = _fetch_text(watch_url)
                if not actual_title:
                    actual_title = _title_from_watch_html(watch_html, expected)
                if expected not in watch_html and video_id not in watch_html:
                    last = {"ok": False, "reason": "podcast_watch_missing", "videoId": video_id}
                elif actual_title and actual_title != expected:
                    return {
                        "ok": False,
                        "reason": "podcast_title_mismatch",
                        "videoId": video_id,
                        "expected_title": expected,
                        "actual_title": actual_title,
                    }
                elif playlist_id:
                    playlist_url = f"https://www.youtube.com/playlist?list={quote(playlist_id)}"
                    playlist_html = _fetch_text(playlist_url)
                    if video_id not in playlist_html:
                        last = {
                            "ok": False,
                            "reason": "podcast_playlist_missing",
                            "videoId": video_id,
                            "playlistId": playlist_id,
                        }
                    else:
                        primary_playlist_id = str(row.get("primaryPodcastPlaylistId") or "")
                        if primary_playlist_id and primary_playlist_id != playlist_id:
                            primary_playlist_url = f"https://www.youtube.com/playlist?list={quote(primary_playlist_id)}"
                            primary_playlist_html = _fetch_text(primary_playlist_url)
                            if video_id not in primary_playlist_html:
                                last = {
                                    "ok": False,
                                    "reason": "primary_podcast_playlist_missing",
                                    "videoId": video_id,
                                    "playlistId": primary_playlist_id,
                                }
                                if time.monotonic() >= deadline:
                                    return last
                                time.sleep(max(1, poll_sec))
                                continue
                        return {
                            "ok": True,
                            "reason": "",
                            "videoId": video_id,
                            "playlistId": playlist_id,
                            "primaryPodcastPlaylistId": primary_playlist_id,
                            "title": actual_title,
                            "verification": verification,
                        }
                else:
                    return {
                        "ok": False,
                        "reason": "podcast_playlist_missing",
                        "videoId": video_id,
                        "playlistId": "",
                    }
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                last = {"ok": False, "reason": "podcast_pending", "videoId": video_id, "detail": str(exc)}
        if time.monotonic() >= deadline:
            return last or {"ok": False, "reason": "public_podcast_missing", "state": str(state_path)}
        time.sleep(max(1, poll_sec))


def verify_publish(
    *,
    repo_root: Path,
    date: str,
    remote: str,
    branch: str,
    public_base_url: str,
    wait_sec: int,
    poll_sec: int,
    require_podcast: bool = False,
    podcast_state_path: Path | None = None,
) -> dict:
    local_head = _git_output(repo_root, ["rev-parse", "HEAD"])
    remote_head = _git_output(repo_root, ["ls-remote", remote, f"refs/heads/{branch}"]).split()[0]
    if local_head != remote_head:
        return {"ok": False, "reason": "remote_head_mismatch", "local_head": local_head, "remote_head": remote_head}

    status_url = urljoin(public_base_url.rstrip("/") + "/", "publish-status.json")
    deadline = time.monotonic() + max(0, wait_sec)
    last_error = ""
    while True:
        try:
            with urllib.request.urlopen(status_url, timeout=20) as res:  # noqa: S310 - fixed public URL from runner config
                status = json.loads(res.read().decode("utf-8-sig"))
            if status.get("result") == "published_ok" and status.get("date") == date:
                audio = verify_public_audio(repo_root=repo_root, date=date, public_base_url=public_base_url)
                if audio["ok"]:
                    podcast = {"checked": False, "ok": True, "reason": "podcast_not_required"}
                    if require_podcast:
                        podcast = verify_podcast(
                            date=date,
                            state_path=podcast_state_path or repo_root / "build" / "youtube-podcast" / "uploads.json",
                            wait_sec=wait_sec,
                            poll_sec=poll_sec,
                        )
                        if not podcast["ok"]:
                            return {
                                "ok": False,
                                "reason": podcast["reason"],
                                "local_head": local_head,
                                "remote_head": remote_head,
                                "url": status_url,
                                "audio": audio,
                                "podcast": podcast,
                            }
                    return {
                        "ok": True,
                        "reason": "",
                        "local_head": local_head,
                        "remote_head": remote_head,
                        "url": status_url,
                        "audio": audio,
                        "podcast": podcast,
                    }
                return {
                    "ok": False,
                    "reason": audio["reason"],
                    "local_head": local_head,
                    "remote_head": remote_head,
                    "url": status_url,
                    "audio": audio,
                }
            last_error = f"publish-status mismatch: {status!r}"
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            last_error = str(exc)
        if time.monotonic() >= deadline:
            return {
                "ok": False,
                "reason": "public_sentinel_missing",
                "detail": last_error,
                "local_head": local_head,
                "remote_head": remote_head,
                "url": status_url,
            }
        time.sleep(max(1, poll_sec))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="News-Grasp daily self-healing helpers")
    sub = parser.add_subparsers(dest="cmd", required=True)

    checksum = sub.add_parser("checksum")
    checksum.add_argument("--repo-path", type=Path, required=True)
    checksum.add_argument("--live-path", type=Path, required=True)

    phase0 = sub.add_parser("phase0")
    phase0.add_argument("--snapshot-json", type=Path, required=True)

    deadman = sub.add_parser("deadman")
    deadman.add_argument("--state-file", type=Path, required=True)
    deadman.add_argument("--date", required=True)
    deadman.add_argument("--max-ok-age-hours", type=int, default=27)
    deadman.add_argument("--alert-log", type=Path, required=True)
    deadman.add_argument("--marker", type=Path, required=True)
    deadman.add_argument("--webhook-env", default="NEWS_GRASP_ALERT_WEBHOOK_URL")

    verify = sub.add_parser("verify-publish")
    verify.add_argument("--repo-root", type=Path, required=True)
    verify.add_argument("--date", required=True)
    verify.add_argument("--remote", default="origin")
    verify.add_argument("--branch", default="main")
    verify.add_argument("--public-base-url", default="https://hidepon-umg.github.io/News-Grasp/")
    verify.add_argument("--wait-sec", type=int, default=600)
    verify.add_argument("--poll-sec", type=int, default=30)
    verify.add_argument("--require-podcast", action="store_true")
    verify.add_argument("--podcast-state", type=Path, default=None)

    podcast = sub.add_parser("verify-podcast")
    podcast.add_argument("--date", required=True)
    podcast.add_argument("--state", type=Path, default=Path("build") / "youtube-podcast" / "uploads.json")
    podcast.add_argument("--wait-sec", type=int, default=1200)
    podcast.add_argument("--poll-sec", type=int, default=30)
    podcast.add_argument("--expected-title", default=None)

    args = parser.parse_args(argv)
    if args.cmd == "checksum":
        result = compare_files(args.repo_path, args.live_path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["synced"] else 1
    if args.cmd == "phase0":
        snapshot = json.loads(args.snapshot_json.read_text(encoding="utf-8"))
        print(json.dumps(classify_phase0(snapshot), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "deadman":
        state = json.loads(args.state_file.read_text(encoding="utf-8")) if args.state_file.exists() else {}
        decision = evaluate_deadman(
            state=state,
            now=datetime.now(timezone.utc),
            expected_date=args.date,
            max_ok_age_hours=args.max_ok_age_hours,
        )
        if decision["alert"]:
            result = emit_alert(
                {"date": args.date, **decision},
                alert_log=args.alert_log,
                marker_path=args.marker,
                webhook_url=os.environ.get(args.webhook_env, ""),
            )
            print(json.dumps({**decision, **result}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps(decision, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "verify-publish":
        result = verify_publish(
            repo_root=args.repo_root,
            date=args.date,
            remote=args.remote,
            branch=args.branch,
            public_base_url=args.public_base_url,
            wait_sec=args.wait_sec,
            poll_sec=args.poll_sec,
            require_podcast=args.require_podcast,
            podcast_state_path=args.podcast_state,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    if args.cmd == "verify-podcast":
        result = verify_podcast(
            date=args.date,
            state_path=args.state,
            wait_sec=args.wait_sec,
            poll_sec=args.poll_sec,
            expected_title=args.expected_title,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    raise AssertionError(args.cmd)


if __name__ == "__main__":
    sys.exit(main())
