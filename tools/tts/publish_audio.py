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


REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_DIR = REPO_ROOT / "build" / "tts"
LATEST_AUDIO_JSON = BUILD_DIR / "latest_audio.json"
RELEASE_TAG = "audio-daily"
OWNER = "HIDEPON-UMG"
REPO = "News-Grasp"
GH_TIMEOUT_SEC = 120
_ASSET_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.mp3$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _warn(message: str) -> None:
    print(f"[tts][WARN] {message}", file=sys.stderr)


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
        _warn(f"gh release prepare failed: {exc}")
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


def write_latest_audio(day: str, url: str) -> None:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_AUDIO_JSON.write_text(
        json.dumps({"latest_audio_date": day, "latest_audio_url": url}, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )


def latest_audio_for_pages(day: str | None = None) -> dict[str, str]:
    if not LATEST_AUDIO_JSON.exists():
        return {"latest_audio_url": "", "latest_audio_date": ""}
    try:
        data = json.loads(LATEST_AUDIO_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"latest_audio_url": "", "latest_audio_date": ""}
    if day and data.get("latest_audio_date") != day:
        return {"latest_audio_url": "", "latest_audio_date": ""}
    return {
        "latest_audio_url": str(data.get("latest_audio_url") or ""),
        "latest_audio_date": str(data.get("latest_audio_date") or ""),
    }


def publish(day: str, mp3_path: Path | None = None) -> dict[str, str] | None:
    parsed_day = _parse_day(day)
    target = mp3_path or (BUILD_DIR / f"{day}.mp3")
    if not target.exists():
        _warn(f"mp3 not found: {target}")
        return None
    try:
        if not ensure_release():
            return None
        proc.quiet_run(["gh", "release", "upload", RELEASE_TAG, str(target), "--clobber"], timeout=GH_TIMEOUT_SEC)
        url = versioned_audio_url(day, target)
        if not _url_returns_200(url):
            return None
        rotate(today=parsed_day)
        write_latest_audio(day, url)
        print(f"[tts] audio published: {url}")
        return {"latest_audio_date": day, "latest_audio_url": url}
    except Exception as exc:
        _warn(f"audio publish failed: {exc}")
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="日次朗読 mp3 を GitHub Releases に公開します。")
    parser.add_argument("date", help="YYYY-MM-DD")
    args = parser.parse_args(argv)
    return 0 if publish(args.date) is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
