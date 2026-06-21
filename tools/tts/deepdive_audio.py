from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

from tools.config import BASE_URL
from tools.tts import proc


REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_DIR = REPO_ROOT / "build" / "tts" / "deepdive"
DIGEST_DIR = REPO_ROOT / "digest" / "DeepDive"
LATEST_JSON = BUILD_DIR / "latest_audio.json"
RELEASE_TAG = "audio-deepdive"
RELEASE_REPO = "HIDEPON-UMG/News-Grasp"
GH_TIMEOUT_SEC = 120
_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_ARTICLE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-DeepDive\.md$")


def _warn(message: str) -> None:
    print(f"[tts][WARN] {message}", file=sys.stderr)


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
    if data.get("deepdive_audio_date") != date:
        return _local_audio_for_pages(date, build_dir=build_dir)
    return {
        "deepdive_audio_url": str(data.get("deepdive_audio_url") or ""),
        "deepdive_audio_date": str(data.get("deepdive_audio_date") or ""),
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
        _warn(f"DeepDive audio release prepare failed: {exc}")
        return False


def _url_returns_200(url: str) -> bool:
    try:
        request = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status == 200
    except Exception as exc:
        _warn(f"DeepDive audio URL verification failed: {exc}")
        return False


def write_latest_audio(day: str, url: str) -> None:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_JSON.write_text(
        json.dumps({"deepdive_audio_date": day, "deepdive_audio_url": url}, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )


def publish(day: str, mp3_path: Path | None = None) -> dict[str, str] | None:
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        raise ValueError(f"invalid DeepDive audio date: {day}")
    target = mp3_path or (BUILD_DIR / f"{day}.mp3")
    if not target.exists():
        _warn(f"DeepDive mp3 not found: {target}")
        return None
    try:
        if not ensure_release():
            return None
        proc.quiet_run(["gh", "release", "upload", RELEASE_TAG, str(target), "--clobber"], timeout=GH_TIMEOUT_SEC)
        url = versioned_deepdive_audio_url(day, target)
        if not _url_returns_200(url):
            return None
        write_latest_audio(day, url)
        print(f"[tts] DeepDive audio published: {url}")
        return {"deepdive_audio_date": day, "deepdive_audio_url": url}
    except Exception as exc:
        _warn(f"DeepDive audio publish failed: {exc}")
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DeepDive 対談 mp3 を GitHub Releases に公開します。")
    parser.add_argument("date", help="YYYY-MM-DD")
    parser.add_argument("--mp3", type=Path, default=None)
    args = parser.parse_args(argv)
    return 0 if publish(args.date, args.mp3) is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
