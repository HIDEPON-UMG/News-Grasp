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
ASSET_DIR = REPO_ROOT / "assets" / "audio"
DEFAULT_BGM_PATH = ASSET_DIR / "news-grasp-bgm.wav"
BGM_VOLUME_DB = -9.5
BGM_LOOP_CROSSFADE_SECONDS = 1.0
MIN_SECONDS = 6 * 60
MAX_SECONDS = 10 * 60
FFMPEG_TIMEOUT_SEC = 180
FFPROBE_TIMEOUT_SEC = 30
MAX_SYNTHESIS_SECONDS = 15 * 60
INTER_CHUNK_SILENCE_SECONDS = 0.28


def _warn(message: str) -> None:
    print(f"[tts][WARN] {message}", file=sys.stderr)


def split_text(text: str, max_chars: int = 220) -> list[str]:
    parts = [p.strip() for p in re.split(r"(?<=[。！？!?])\s*|\n+", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for part in parts:
        if len(part) <= max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(part)
            continue
        candidate = f"{current}{part}" if not current else f"{current}\n{part}"
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = part
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _silence_frames(params: wave._wave_params, seconds: float) -> bytes:
    if seconds <= 0:
        return b""
    nframes = int(params.framerate * seconds)
    return b"\x00" * nframes * params.nchannels * params.sampwidth


def combine_wavs(wavs: list[bytes], out_path: Path, *, silence_seconds: float = INTER_CHUNK_SILENCE_SECONDS) -> None:
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
        silence = _silence_frames(params, silence_seconds)
        for index, frame in enumerate(frames):
            if index:
                writer.writeframes(silence)
            writer.writeframes(frame)


def probe_duration_seconds(mp3_path: Path) -> float | None:
    try:
        result = proc.quiet_run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                mp3_path,
            ],
            check=True,
            timeout=FFPROBE_TIMEOUT_SEC,
        )
        data = json.loads(result.stdout or "{}")
        return float(data["format"]["duration"])
    except Exception as exc:
        _warn(f"ffprobe duration check failed: {exc}")
        return None


def convert_wav_to_mp3(wav_path: Path, mp3_path: Path) -> float:
    start = time.monotonic()
    proc.quiet_run(
        ["ffmpeg", "-y", "-i", wav_path, "-ac", "1", "-b:a", "80k", mp3_path],
        timeout=FFMPEG_TIMEOUT_SEC,
    )
    return time.monotonic() - start


def _wav_duration_seconds(wav_path: Path) -> float:
    with wave.open(str(wav_path), "rb") as reader:
        if reader.getframerate() <= 0:
            raise RuntimeError(f"invalid wav framerate: {wav_path}")
        return reader.getnframes() / reader.getframerate()


def _read_int16_mono_wav(wav_path: Path) -> tuple[wave._wave_params, list[int]]:
    with wave.open(str(wav_path), "rb") as reader:
        params = reader.getparams()
        if params.nchannels != 1 or params.sampwidth != 2:
            raise RuntimeError(f"BGM wav must be mono 16-bit PCM: {wav_path}")
        frames = reader.readframes(reader.getnframes())
    samples = [
        int.from_bytes(frames[index:index + 2], "little", signed=True)
        for index in range(0, len(frames), 2)
    ]
    if not samples:
        raise RuntimeError(f"BGM wav is empty: {wav_path}")
    return params, samples


def _write_looped_bgm_bed(
    bgm_wav: Path,
    out_wav: Path,
    *,
    duration_seconds: float,
    crossfade_seconds: float = BGM_LOOP_CROSSFADE_SECONDS,
) -> None:
    params, source = _read_int16_mono_wav(bgm_wav)
    target_frames = max(int(round(duration_seconds * params.framerate)), 1)
    crossfade_frames = min(
        int(round(crossfade_seconds * params.framerate)),
        len(source) // 3,
    )
    bed = source[:]
    while len(bed) < target_frames:
        if crossfade_frames > 0:
            overlap = min(crossfade_frames, len(bed), len(source))
            start = len(bed) - overlap
            for index in range(overlap):
                ratio = (index + 1) / (overlap + 1)
                bed[start + index] = int(
                    bed[start + index] * (1.0 - ratio) + source[index] * ratio
                )
            bed.extend(source[overlap:])
        else:
            bed.extend(source)
    bed = bed[:target_frames]
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_wav), "wb") as writer:
        writer.setparams(params)
        frames = b"".join(sample.to_bytes(2, "little", signed=True) for sample in bed)
        writer.writeframes(frames)


def mix_voice_wav_with_bgm(
    voice_wav: Path,
    bgm_wav: Path,
    mp3_out: Path,
    *,
    bgm_volume_db: float = BGM_VOLUME_DB,
) -> None:
    duration = _wav_duration_seconds(voice_wav)
    fade_out_start = max(duration - 5.0, 0.0)
    with tempfile.TemporaryDirectory(prefix="news-grasp-bgm-") as tmp:
        bgm_bed = Path(tmp) / "looped-bgm-bed.wav"
        _write_looped_bgm_bed(bgm_wav, bgm_bed, duration_seconds=duration)
        filter_complex = (
            f"[1:a]volume={bgm_volume_db:.1f}dB,"
            "afade=t=in:st=0:d=2,"
            f"afade=t=out:st={fade_out_start:.3f}:d=5[bgm];"
            "[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
            "alimiter=limit=0.95[out]"
        )
        mp3_out.parent.mkdir(parents=True, exist_ok=True)
        proc.quiet_run(
            [
                "ffmpeg",
                "-y",
                "-i",
                voice_wav,
                "-i",
                bgm_bed,
                "-filter_complex",
                filter_complex,
                "-map",
                "[out]",
                "-ac",
                "1",
                "-b:a",
                "80k",
                mp3_out,
            ],
            timeout=FFMPEG_TIMEOUT_SEC,
        )


def convert_voice_wav_to_delivery_mp3(wav_path: Path, mp3_path: Path) -> float:
    start = time.monotonic()
    if not DEFAULT_BGM_PATH.exists():
        _warn(f"BGM not found, plain voice mp3: {DEFAULT_BGM_PATH}")
        return convert_wav_to_mp3(wav_path, mp3_path)
    try:
        mix_voice_wav_with_bgm(wav_path, DEFAULT_BGM_PATH, mp3_path)
        return time.monotonic() - start
    except Exception as exc:
        _warn(f"BGM mix failed, fallback to plain voice mp3: {exc}")
        return convert_wav_to_mp3(wav_path, mp3_path)


def synthesize(date: str) -> Path | None:
    script_path = BUILD_DIR / f"{date}.script.txt"
    if not script_path.exists():
        _warn(f"normalized script not found: {script_path}")
        return None
    if not aivis_client.ensure_engine():
        if aivis_client.engine_started_by_this_process():
            aivis_client.shutdown_started_engine()
        return None
    try:
        style_id = aivis_client.resolve_style_id()
        text = script_path.read_text(encoding="utf-8")
        chunks = split_text(text)
        started_at = time.monotonic()
        wavs: list[bytes] = []
        for chunk in chunks:
            if time.monotonic() - started_at > MAX_SYNTHESIS_SECONDS:
                _warn(f"TTS synthesis time budget exceeded: {MAX_SYNTHESIS_SECONDS}s")
                return None
            wavs.append(aivis_client.synthesize(chunk, style_id))
        with tempfile.TemporaryDirectory(prefix="news-grasp-tts-") as tmp:
            wav_path = Path(tmp) / f"{date}.wav"
            combine_wavs(wavs, wav_path)
            mp3_path = BUILD_DIR / f"{date}.mp3"
            BUILD_DIR.mkdir(parents=True, exist_ok=True)
            elapsed = convert_voice_wav_to_delivery_mp3(wav_path, mp3_path)
            print(f"[tts] ffmpeg mp3 conversion: {elapsed:.2f}s")
        duration = probe_duration_seconds(mp3_path)
        if duration is not None and not (MIN_SECONDS <= duration <= MAX_SECONDS):
            _warn(f"mp3 duration out of target range: {duration:.1f}s")
        print(f"[tts] mp3 built: {mp3_path}")
        return mp3_path
    except Exception as exc:
        _warn(f"TTS synthesis failed: {exc}")
        return None
    finally:
        if aivis_client.engine_started_by_this_process():
            aivis_client.shutdown_started_engine()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="正規化済み原稿から AivisSpeech mp3 を生成します。")
    parser.add_argument("date", help="YYYY-MM-DD")
    args = parser.parse_args(argv)
    synthesize(args.date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
