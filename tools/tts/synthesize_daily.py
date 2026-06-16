from __future__ import annotations

import argparse
import io
import json
import re
import sys
import tempfile
import time
import wave
from pathlib import Path

from tools.tts import aivis_client, proc


REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_DIR = REPO_ROOT / "build" / "tts"
MIN_SECONDS = 6 * 60
MAX_SECONDS = 10 * 60


def _warn(message: str) -> None:
    print(f"[tts][WARN] {message}", file=sys.stderr)


def split_text(text: str, max_chars: int = 220) -> list[str]:
    parts = [p.strip() for p in re.split(r"(?<=[。！？!?])\s*|\n+", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for part in parts:
        candidate = f"{current}{part}" if not current else f"{current}\n{part}"
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = part
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def combine_wavs(wavs: list[bytes], out_path: Path) -> None:
    params = None
    frames: list[bytes] = []
    for wav_bytes in wavs:
        with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
            if params is None:
                params = reader.getparams()
            elif reader.getparams()[:3] != params[:3]:
                raise RuntimeError("wav format mismatch while combining chunks")
            frames.append(reader.readframes(reader.getnframes()))
    if params is None:
        raise RuntimeError("no wav chunks to combine")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as writer:
        writer.setparams(params)
        for frame in frames:
            writer.writeframes(frame)


def probe_duration_seconds(mp3_path: Path) -> float | None:
    try:
        result = proc.quiet_run([
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            mp3_path,
        ], check=True)
        data = json.loads(result.stdout or "{}")
        return float(data["format"]["duration"])
    except Exception as exc:
        _warn(f"ffprobe duration check failed: {exc}")
        return None


def convert_wav_to_mp3(wav_path: Path, mp3_path: Path) -> float:
    start = time.monotonic()
    proc.quiet_run(["ffmpeg", "-y", "-i", wav_path, "-ac", "1", "-b:a", "80k", mp3_path])
    return time.monotonic() - start


def synthesize(date: str) -> Path | None:
    script_path = BUILD_DIR / f"{date}.script.txt"
    if not script_path.exists():
        _warn(f"normalized script not found: {script_path}")
        return None
    if not aivis_client.ensure_engine():
        return None
    try:
        style_id = aivis_client.resolve_style_id()
        text = script_path.read_text(encoding="utf-8")
        chunks = split_text(text)
        wavs = [aivis_client.synthesize(chunk, style_id) for chunk in chunks]
        with tempfile.TemporaryDirectory(prefix="news-grasp-tts-") as tmp:
            wav_path = Path(tmp) / f"{date}.wav"
            combine_wavs(wavs, wav_path)
            mp3_path = BUILD_DIR / f"{date}.mp3"
            BUILD_DIR.mkdir(parents=True, exist_ok=True)
            elapsed = convert_wav_to_mp3(wav_path, mp3_path)
            print(f"[tts] ffmpeg mp3 conversion: {elapsed:.2f}s")
        duration = probe_duration_seconds(mp3_path)
        if duration is not None and not (MIN_SECONDS <= duration <= MAX_SECONDS):
            _warn(f"mp3 duration out of target range: {duration:.1f}s")
        print(f"[tts] mp3 built: {mp3_path}")
        return mp3_path
    except Exception as exc:
        _warn(f"TTS synthesis failed: {exc}")
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="正規化済み原稿から AivisSpeech mp3 を生成します。")
    parser.add_argument("date", help="YYYY-MM-DD")
    args = parser.parse_args(argv)
    synthesize(args.date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
