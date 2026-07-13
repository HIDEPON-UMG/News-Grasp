from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from tools.tts import proc
from tools.tts.synthesize_daily import probe_duration_seconds


REPO_ROOT = Path(__file__).resolve().parents[2]
TTS_BUILD_DIR = REPO_ROOT / "build" / "tts"
BUILD_DIR = REPO_ROOT / "build" / "youtube-podcast"
DEEPDIVE_BUILD_DIR = REPO_ROOT / "build" / "youtube-podcast-deepdive"
DEEPDIVE_COVER_PATH = REPO_ROOT / "assets" / "podcast" / "deepdive-dialogue-cover.png"
DEFAULT_COVER_PATH = REPO_ROOT / "assets" / "podcast" / "daily-podcast-cover.png"
FFMPEG_TIMEOUT_SEC = 180
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _warn(message: str) -> None:
    print(f"[youtube-podcast][WARN] {message}", file=sys.stderr)


def _cover_path() -> Path:
    return Path(os.environ.get("NEWS_GRASP_PODCAST_COVER") or DEFAULT_COVER_PATH)


def _deepdive_cover_path() -> Path:
    return Path(os.environ.get("NEWS_GRASP_DEEPDIVE_PODCAST_COVER") or DEEPDIVE_COVER_PATH)


def _valid_date(day: str) -> bool:
    return bool(_DATE_RE.match(day))


def _paths_for_kind(day: str, kind: str) -> tuple[Path, Path, Path]:
    if kind == "daily":
        return TTS_BUILD_DIR / f"{day}.mp3", _cover_path(), BUILD_DIR
    if kind == "deepdive":
        return TTS_BUILD_DIR / "deepdive" / f"{day}.mp3", _deepdive_cover_path(), DEEPDIVE_BUILD_DIR
    raise ValueError(f"invalid podcast kind: {kind}")


def build(
    day: str,
    *,
    kind: str = "daily",
    cover_path: Path | None = None,
    audio_path: Path | None = None,
) -> dict[str, Any] | None:
    if not _valid_date(day):
        _warn(f"invalid date: {day}")
        return None
    try:
        default_audio, default_cover, build_dir = _paths_for_kind(day, kind)
    except ValueError as exc:
        _warn(str(exc))
        return None
    audio = audio_path or default_audio
    cover = cover_path or default_cover
    if not audio.exists():
        _warn(f"mp3 not found: {audio}")
        return None
    if not cover.exists():
        _warn(f"cover image not found: {cover}")
        return None

    build_dir.mkdir(parents=True, exist_ok=True)
    mp4 = build_dir / f"{day}.mp4"
    args = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        cover,
        "-i",
        audio,
        "-c:v",
        "libx264",
        "-vf",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-tune",
        "stillimage",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-pix_fmt",
        "yuv420p",
        "-shortest",
        mp4,
    ]
    try:
        proc.quiet_run(args, timeout=FFMPEG_TIMEOUT_SEC)
    except Exception as exc:
        _warn(f"ffmpeg mp4 build failed: {exc}")
        return None

    duration = probe_duration_seconds(audio)
    result = {
        "date": day,
        "kind": kind,
        "mp3_path": str(audio),
        "cover_path": str(cover),
        "mp4_path": str(mp4),
        "duration": duration,
    }
    print(json.dumps(result, ensure_ascii=False))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="日次 mp3 と固定カバー画像から YouTube Podcast 用 mp4 を生成します。")
    parser.add_argument("date", help="YYYY-MM-DD")
    parser.add_argument("--kind", choices=["daily", "deepdive"], default="daily", help="daily=日次朗読 / deepdive=DeepDive解説対談")
    parser.add_argument("--cover", type=Path, default=None, help="固定カバー画像。既定は NEWS_GRASP_PODCAST_COVER または repo 管理の daily cover。")
    args = parser.parse_args(argv)
    return 0 if build(args.date, kind=args.kind, cover_path=args.cover) is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
