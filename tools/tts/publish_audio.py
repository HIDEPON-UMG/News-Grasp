from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from datetime import date as date_type
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from tools.tts import proc
from tools.news_grasp_audio_projection import normalize_audio_projection, write_audio_projection


REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_DIR = REPO_ROOT / "build" / "tts"
LATEST_AUDIO_JSON = BUILD_DIR / "latest_audio.json"
DEFAULT_LATEST_AUDIO_JSON = LATEST_AUDIO_JSON
RELEASE_TAG = "audio-daily"
OWNER = "HIDEPON-UMG"
REPO = "News-Grasp"
GH_TIMEOUT_SEC = 120
_ASSET_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.mp3$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HTTP_ERROR_RE = re.compile(r"\bHTTP\s+(?P<code>502|503)\b", re.IGNORECASE)
_LAST_PUBLISH_FAILURE: dict[str, Any] | None = None


def _warn(message: str) -> None:
    print(f"[tts][WARN] {message}", file=sys.stderr)


def classify_publish_failure(
    exc: Exception,
    *,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """GitHub Release publish failureをrunnerが消費できるtyped payloadへ変換する。"""
    detail = str(exc)
    text = detail.casefold()
    http_match = _HTTP_ERROR_RE.search(detail)
    external = (
        http_match is not None
        or "bad gateway" in text
        or "error creating policy" in text
        or "service unavailable" in text
        or "timed out" in text
        or "audio url verification failed" in text
    )
    if external:
        observed_error_code = http_match.group("code") if http_match else (
            "timeout" if "timed out" in text else "unavailable"
        )
        return {
            "ok": False,
            "status": "blocked_external_readiness",
            "gate_id": "github-release-upload",
            "issue_code": "github_release_upload_transient",
            "external_kind": "service_unavailable",
            "external_system": "github-release",
            "observed_error_code": observed_error_code,
            "source_command": "gh release upload audio-daily",
            "detail": detail,
            "observed_at": observed_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        }
    return {
        "ok": False,
        "status": "publish_failed",
        "gate_id": "github-release-upload",
        "issue_code": "audio_publish_local_failure",
        "external_kind": "",
        "external_system": "",
        "observed_error_code": "",
        "source_command": "python -m tools.tts.publish_audio",
        "detail": detail,
        "observed_at": observed_at or datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def _record_publish_failure(
    exc: Exception,
    *,
    observed_at: str | None = None,
) -> None:
    global _LAST_PUBLISH_FAILURE
    _LAST_PUBLISH_FAILURE = classify_publish_failure(exc, observed_at=observed_at)
    _warn(f"audio publish failed: {exc}")


def _parse_day(day: str) -> date_type:
    if not _DATE_RE.match(day):
        raise ValueError(f"invalid audio date: {day}")
    return datetime.strptime(day, "%Y-%m-%d").date()


def audio_url(day: str, *, owner: str = OWNER, repo: str = REPO) -> str:
    _parse_day(day)
    return f"https://github.com/{owner}/{repo}/releases/download/{RELEASE_TAG}/{day}.mp3"


def versioned_audio_url(day: str, mp3_path: Path, *, owner: str = OWNER, repo: str = REPO) -> str:
    digest = hashlib.sha256(mp3_path.read_bytes()).hexdigest()[:12]
    return f"{audio_url(day, owner=owner, repo=repo)}?v={digest}"


def ensure_release() -> bool:
    try:
        view = proc.quiet_run(
            ["gh", "release", "view", RELEASE_TAG, "--json", "tagName"],
            check=False,
            timeout=GH_TIMEOUT_SEC,
        )
        if view.returncode == 0:
            return True
        proc.quiet_run([
            "gh",
            "release",
            "create",
            RELEASE_TAG,
            "--title",
            "Daily Audio",
            "--notes",
            "日次朗読音声の保管",
        ], timeout=GH_TIMEOUT_SEC)
        return True
    except Exception as exc:
        _record_publish_failure(exc)
        return False


def _load_assets() -> list[dict[str, Any]]:
    result = proc.quiet_run(
        ["gh", "release", "view", RELEASE_TAG, "--json", "assets"],
        check=True,
        timeout=GH_TIMEOUT_SEC,
    )
    return list(json.loads(result.stdout or "{}").get("assets") or [])


def rotate(
    *,
    today: date_type,
    assets: list[dict[str, Any]] | None = None,
    keep_days: int = 31,
    quiet_run: Callable[..., Any] = proc.quiet_run,
) -> list[str]:
    cutoff = today - timedelta(days=keep_days)
    asset_rows = assets if assets is not None else _load_assets()
    deleted: list[str] = []
    for asset in asset_rows:
        name = str(asset.get("name") or "")
        match = _ASSET_DATE_RE.match(name)
        if not match:
            continue
        asset_day = datetime.strptime(match.group(1), "%Y-%m-%d").date()
        if asset_day < cutoff:
            quiet_run(["gh", "release", "delete-asset", RELEASE_TAG, name, "-y"], timeout=GH_TIMEOUT_SEC)
            deleted.append(name)
    return deleted


def _url_returns_200(url: str) -> bool:
    try:
        request = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status == 200
    except Exception as exc:
        _warn(f"audio URL verification failed: {exc}")
        return False


def write_latest_audio(
    day: str,
    url: str,
    *,
    run_id: str = "",
    run_intent: str = "scheduled_production_direct",
) -> None:
    """日次音声をV2 canonical pathだけへ書く。"""
    projection = normalize_audio_projection(
        {"latest_audio_date": day, "latest_audio_url": url, "status": "verified"},
        audio_type="daily",
        run_id=run_id or f"direct-{day}-unbound",
        run_intent=run_intent,
        source_artifact=f"build/tts/{day}.mp3",
        public_page_href=url,
    )
    if LATEST_AUDIO_JSON != DEFAULT_LATEST_AUDIO_JSON:
        LATEST_AUDIO_JSON.parent.mkdir(parents=True, exist_ok=True)
        LATEST_AUDIO_JSON.write_text(
            json.dumps(projection, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    else:
        write_audio_projection(REPO_ROOT, projection)


def latest_audio_for_pages(day: str | None = None) -> dict[str, str]:
    canonical = BUILD_DIR / "daily" / "latest_audio.json"
    source = canonical if canonical.exists() else LATEST_AUDIO_JSON
    if not source.exists():
        return {"latest_audio_url": "", "latest_audio_date": ""}
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"latest_audio_url": "", "latest_audio_date": ""}
    observed_day = data.get("issueDate", data.get("latest_audio_date"))
    observed_url = data.get("publicUrl", data.get("latest_audio_url"))
    if day and observed_day != day:
        return {"latest_audio_url": "", "latest_audio_date": ""}
    return {
        "latest_audio_url": str(observed_url or ""),
        "latest_audio_date": str(observed_day or ""),
    }


def publish(
    day: str,
    mp3_path: Path | None = None,
    *,
    dry_run: bool = False,
    run_id: str = "",
) -> dict[str, str] | None:
    global _LAST_PUBLISH_FAILURE
    _LAST_PUBLISH_FAILURE = None
    parsed_day = _parse_day(day)
    target = mp3_path or (BUILD_DIR / f"{day}.mp3")
    if not target.exists():
        _record_publish_failure(FileNotFoundError(f"mp3 not found: {target}"))
        return None
    try:
        if dry_run:
            url = versioned_audio_url(day, target)
            write_latest_audio(day, url, run_id=run_id) if run_id else write_latest_audio(day, url)
            print(f"[tts] audio publish dry-run: {url}")
            return {"latest_audio_date": day, "latest_audio_url": url}
        if not ensure_release():
            return None
        proc.quiet_run(["gh", "release", "upload", RELEASE_TAG, str(target), "--clobber"], timeout=GH_TIMEOUT_SEC)
        url = versioned_audio_url(day, target)
        if not _url_returns_200(url):
            _record_publish_failure(RuntimeError(f"audio URL verification failed: {url}"))
            return None
        rotate(today=parsed_day)
        write_latest_audio(day, url, run_id=run_id) if run_id else write_latest_audio(day, url)
        print(f"[tts] audio published: {url}")
        return {"latest_audio_date": day, "latest_audio_url": url}
    except Exception as exc:
        _record_publish_failure(exc)
        return None


def main(argv: list[str] | None = None) -> int:
    global _LAST_PUBLISH_FAILURE
    parser = argparse.ArgumentParser(description="日次朗読 mp3 を GitHub Releases に公開します。")
    parser.add_argument("date", help="YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="GitHub Releases へ upload せず latest_audio.json だけ検証用に更新する。")
    parser.add_argument("--json", action="store_true", help="publish結果またはtyped failureをJSONで出力する。")
    parser.add_argument("--run-id", default="", help="direct runtime run ID。")
    args = parser.parse_args(argv)
    _LAST_PUBLISH_FAILURE = None
    if args.run_id:
        result = publish(args.date, dry_run=args.dry_run, run_id=args.run_id)
    else:
        result = publish(args.date, dry_run=True) if args.dry_run else publish(args.date)
    if result is not None:
        if args.json:
            print(json.dumps({"ok": True, "status": "published_ok", **result}, ensure_ascii=False))
        return 0
    failure = _LAST_PUBLISH_FAILURE or {
        "ok": False,
        "status": "publish_failed",
        "gate_id": "github-release-upload",
        "issue_code": "audio_publish_local_failure",
        "detail": "publish returned no result without typed failure evidence",
    }
    if args.json:
        print(json.dumps(failure, ensure_ascii=False))
    return 71 if failure.get("status") == "blocked_external_readiness" else 1


if __name__ == "__main__":
    raise SystemExit(main())
