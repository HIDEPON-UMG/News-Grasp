from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from tools.config import BASE_URL
from tools.tts import proc
from tools.news_grasp_audio_projection import normalize_audio_projection, write_audio_projection


REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_DIR = REPO_ROOT / "build" / "tts" / "deepdive"
DIGEST_DIR = REPO_ROOT / "digest" / "DeepDive"
LATEST_JSON = BUILD_DIR / "latest_audio.json"
DEFAULT_LATEST_JSON = LATEST_JSON
RELEASE_TAG = "audio-deepdive"
RELEASE_REPO = "HIDEPON-UMG/News-Grasp"
GH_TIMEOUT_SEC = 120
_HTTP_ERROR_RE = re.compile(r"\bHTTP\s+(?P<code>502|503)\b", re.IGNORECASE)
_LAST_PUBLISH_FAILURE: dict[str, Any] | None = None
_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_ARTICLE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-DeepDive\.md$")


def _warn(message: str) -> None:
    print(f"[tts][WARN] {message}", file=sys.stderr)


def classify_publish_failure(
    exc: Exception,
    *,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """GitHub Release publish failure を runner 用 typed payload に変換する。"""
    detail = str(exc)
    text = detail.casefold()
    http_match = _HTTP_ERROR_RE.search(detail)
    external = (
        http_match is not None
        or "bad gateway" in text
        or "error creating policy" in text
        or "service unavailable" in text
        or "timed out" in text
        or "deepdive audio url verification failed" in text
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
            "source_command": "gh release upload audio-deepdive",
            "detail": detail,
            "observed_at": observed_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        }
    return {
        "ok": False,
        "status": "publish_failed",
        "gate_id": "github-release-upload",
        "issue_code": "deepdive_audio_publish_local_failure",
        "external_kind": "",
        "external_system": "",
        "observed_error_code": "",
        "source_command": "python -m tools.tts.deepdive_audio",
        "detail": detail,
        "observed_at": observed_at or datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def _record_publish_failure(exc: Exception) -> None:
    global _LAST_PUBLISH_FAILURE
    _LAST_PUBLISH_FAILURE = classify_publish_failure(exc)
    _warn(f"DeepDive audio publish failed: {exc}")


def versioned_deepdive_audio_url(date: str, mp3_path: Path) -> str:
    digest = hashlib.sha256(mp3_path.read_bytes()).hexdigest()[:12]
    return (
        f"https://github.com/{RELEASE_REPO}/releases/download/"
        f"{RELEASE_TAG}/{date}.mp3?v={digest}"
    )


def _candidate_mp3_paths(date: str, build_dir: Path = BUILD_DIR) -> list[Path]:
    return [
        build_dir / f"{date}.mp3",
        build_dir / f"{date}-deepdive-dialogue-sample.mp3",
    ]


def _local_audio_for_pages(date: str | None, *, build_dir: Path = BUILD_DIR) -> dict[str, str]:
    if not date:
        return {"deepdive_audio_url": "", "deepdive_audio_date": ""}
    for mp3_path in _candidate_mp3_paths(date, build_dir):
        if mp3_path.exists():
            return {
                "deepdive_audio_url": versioned_deepdive_audio_url(date, mp3_path),
                "deepdive_audio_date": date,
            }
    return {"deepdive_audio_url": "", "deepdive_audio_date": ""}


def latest_deepdive_dates(digest_dir: Path = DIGEST_DIR, *, limit: int = 2) -> list[str]:
    if not digest_dir.exists():
        return []
    dates = []
    for md_path in sorted(digest_dir.glob("*.md")):
        if not _ARTICLE_RE.match(md_path.name):
            continue
        match = _DATE_RE.match(md_path.name)
        if match:
            dates.append(match.group(1))
    return dates[-limit:]


def deepdive_audio_for_pages(
    date: str | None,
    *,
    enforce_recent: bool = False,
    digest_dir: Path = DIGEST_DIR,
    latest_json: Path = LATEST_JSON,
    build_dir: Path = BUILD_DIR,
) -> dict[str, str]:
    if not date:
        return {"deepdive_audio_url": "", "deepdive_audio_date": ""}
    if enforce_recent and date not in latest_deepdive_dates(digest_dir):
        return {"deepdive_audio_url": "", "deepdive_audio_date": ""}
    if not latest_json.exists():
        return _local_audio_for_pages(date, build_dir=build_dir)
    try:
        data = json.loads(latest_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _local_audio_for_pages(date, build_dir=build_dir)
    observed_date = data.get("issueDate", data.get("deepdive_audio_date"))
    observed_url = data.get("publicUrl", data.get("deepdive_audio_url"))
    if observed_date != date:
        return _local_audio_for_pages(date, build_dir=build_dir)
    return {
        "deepdive_audio_url": str(observed_url or ""),
        "deepdive_audio_date": str(observed_date or ""),
    }


def deepdive_audio_asset_url(date: str) -> str:
    return f"{BASE_URL}/deepdive/{date}/"


def ensure_release() -> bool:
    try:
        view = proc.quiet_run(
            ["gh", "release", "view", RELEASE_TAG, "--json", "tagName"],
            check=False,
            timeout=GH_TIMEOUT_SEC,
        )
        if view.returncode == 0:
            return True
        proc.quiet_run(
            [
                "gh",
                "release",
                "create",
                RELEASE_TAG,
                "--title",
                "DeepDive Dialogue Audio",
                "--notes",
                "DeepDive 解説対談音声の保管",
            ],
            timeout=GH_TIMEOUT_SEC,
        )
        return True
    except Exception as exc:
        _record_publish_failure(exc)
        return False


def _url_returns_200(url: str) -> bool:
    try:
        request = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status == 200
    except Exception as exc:
        _warn(f"DeepDive audio URL verification failed: {exc}")
        return False


def write_latest_audio(
    day: str,
    url: str,
    *,
    run_id: str = "",
    run_intent: str = "scheduled_production_direct",
) -> None:
    """DeepDive音声をdailyと同形のV2 schemaへ書く。"""
    projection = normalize_audio_projection(
        {"deepdive_audio_date": day, "deepdive_audio_url": url, "status": "verified"},
        audio_type="deepdive",
        run_id=run_id or f"direct-{day}-unbound",
        run_intent=run_intent,
        source_artifact=f"build/tts/deepdive/{day}.mp3",
        public_page_href=url,
    )
    if LATEST_JSON != DEFAULT_LATEST_JSON:
        LATEST_JSON.parent.mkdir(parents=True, exist_ok=True)
        LATEST_JSON.write_text(
            json.dumps(projection, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    else:
        write_audio_projection(REPO_ROOT, projection)


def publish(
    day: str,
    mp3_path: Path | None = None,
    *,
    dry_run: bool = False,
    run_id: str = "",
) -> dict[str, str] | None:
    global _LAST_PUBLISH_FAILURE
    _LAST_PUBLISH_FAILURE = None
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        raise ValueError(f"invalid DeepDive audio date: {day}")
    target = mp3_path or (BUILD_DIR / f"{day}.mp3")
    if not target.exists():
        _record_publish_failure(FileNotFoundError(f"DeepDive mp3 not found: {target}"))
        return None
    try:
        if dry_run:
            url = versioned_deepdive_audio_url(day, target)
            write_latest_audio(day, url, run_id=run_id) if run_id else write_latest_audio(day, url)
            print(f"[tts] DeepDive audio publish dry-run: {url}")
            return {"deepdive_audio_date": day, "deepdive_audio_url": url}
        if not ensure_release():
            return None
        proc.quiet_run(["gh", "release", "upload", RELEASE_TAG, str(target), "--clobber"], timeout=GH_TIMEOUT_SEC)
        url = versioned_deepdive_audio_url(day, target)
        if not _url_returns_200(url):
            _record_publish_failure(RuntimeError(f"DeepDive audio URL verification failed: {url}"))
            return None
        write_latest_audio(day, url, run_id=run_id) if run_id else write_latest_audio(day, url)
        print(f"[tts] DeepDive audio published: {url}")
        return {"deepdive_audio_date": day, "deepdive_audio_url": url}
    except Exception as exc:
        _record_publish_failure(exc)
        return None


def main(argv: list[str] | None = None) -> int:
    global _LAST_PUBLISH_FAILURE
    parser = argparse.ArgumentParser(description="DeepDive 対談 mp3 を GitHub Releases に公開します。")
    parser.add_argument("date", help="YYYY-MM-DD")
    parser.add_argument("--mp3", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="GitHub Releases へ upload せず latest_audio.json だけ検証用に更新する。")
    parser.add_argument("--json", action="store_true", help="publish 結果または typed failure を JSON で出力する。")
    parser.add_argument("--run-id", default="", help="direct runtime run ID。")
    args = parser.parse_args(argv)
    _LAST_PUBLISH_FAILURE = None
    result = (
        publish(args.date, args.mp3, dry_run=args.dry_run, run_id=args.run_id)
        if args.run_id
        else publish(args.date, args.mp3, dry_run=args.dry_run)
    )
    if result is not None:
        if args.json:
            print(json.dumps({"ok": True, "status": "published_ok", **result}, ensure_ascii=False))
        return 0
    failure = _LAST_PUBLISH_FAILURE or {
        "ok": False,
        "status": "publish_failed",
        "gate_id": "github-release-upload",
        "issue_code": "deepdive_audio_publish_local_failure",
        "detail": "publish returned no result without typed failure evidence",
    }
    if args.json:
        print(json.dumps(failure, ensure_ascii=False))
    return 71 if failure.get("status") == "blocked_external_readiness" else 1


if __name__ == "__main__":
    raise SystemExit(main())
