from __future__ import annotations

import io
import wave
from pathlib import Path
from unittest.mock import patch

from tools.tts import synthesize_daily


def _wav_bytes(frames: bytes, *, framerate: int = 1000, channels: int = 1, sampwidth: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(sampwidth)
        writer.setframerate(framerate)
        writer.writeframes(frames)
    return buf.getvalue()


def test_split_text_keeps_sentence_boundaries_for_breathing_pause():
    chunks = synthesize_daily.split_text("一文目です。二文目です。三文目です。")

    assert chunks == ["一文目です。", "二文目です。", "三文目です。"]


def test_inter_chunk_silence_is_fixed_to_reviewed_breathing_pause():
    assert synthesize_daily.INTER_CHUNK_SILENCE_SECONDS == 0.28


def test_combine_wavs_inserts_short_silence_between_chunks(tmp_path):
    first = _wav_bytes(b"\x01\x00" * 3)
    second = _wav_bytes(b"\x02\x00" * 2)
    out = tmp_path / "combined.wav"

    synthesize_daily.combine_wavs([first, second], out, silence_seconds=0.003)

    with wave.open(str(out), "rb") as reader:
        assert reader.getframerate() == 1000
        assert reader.getsampwidth() == 2
        frames = reader.readframes(reader.getnframes())

    assert frames == (b"\x01\x00" * 3) + (b"\x00\x00" * 3) + (b"\x02\x00" * 2)


def test_convert_wav_to_mp3_returns_elapsed_seconds_and_uses_quiet_run(tmp_path):
    wav = tmp_path / "in.wav"
    mp3 = tmp_path / "out.mp3"
    wav.write_bytes(b"RIFF")

    with patch.object(synthesize_daily.time, "monotonic", side_effect=[100.0, 112.5]), \
        patch.object(synthesize_daily.proc, "quiet_run") as quiet_run:
        elapsed = synthesize_daily.convert_wav_to_mp3(wav, mp3)

    assert elapsed == 12.5
    quiet_run.assert_called_once_with(
        ["ffmpeg", "-y", "-i", wav, "-ac", "1", "-b:a", "80k", mp3],
        timeout=synthesize_daily.FFMPEG_TIMEOUT_SEC,
    )


def test_probe_duration_uses_timeout_bounded_ffprobe(tmp_path):
    mp3 = tmp_path / "out.mp3"
    mp3.write_bytes(b"ID3")

    with patch.object(synthesize_daily.proc, "quiet_run") as quiet_run:
        quiet_run.return_value.stdout = '{"format":{"duration":"420.0"}}'
        duration = synthesize_daily.probe_duration_seconds(mp3)

    assert duration == 420.0
    quiet_run.assert_called_once()
    assert quiet_run.call_args.kwargs["timeout"] == synthesize_daily.FFPROBE_TIMEOUT_SEC


def test_synthesize_stops_before_unbounded_chunk_loop(tmp_path, monkeypatch):
    build_dir = tmp_path / "tts"
    build_dir.mkdir()
    (build_dir / "2026-06-17.script.txt").write_text("本文", encoding="utf-8")

    calls: list[str] = []
    monkeypatch.setattr(synthesize_daily, "BUILD_DIR", build_dir)
    monkeypatch.setattr(synthesize_daily.aivis_client, "ensure_engine", lambda: True)
    monkeypatch.setattr(synthesize_daily.aivis_client, "resolve_style_id", lambda: 1)
    monotonic_values = iter([0.0, synthesize_daily.MAX_SYNTHESIS_SECONDS + 1.0])
    monkeypatch.setattr(synthesize_daily, "split_text", lambda _text: ["a", "b"])
    monkeypatch.setattr(synthesize_daily.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(
        synthesize_daily.aivis_client,
        "synthesize",
        lambda chunk, _style_id: calls.append(chunk) or b"RIFF",
    )

    assert synthesize_daily.synthesize("2026-06-17") is None
    assert calls == []


def test_synthesize_shuts_down_engine_started_by_this_process_on_success(tmp_path, monkeypatch):
    build_dir = tmp_path / "tts"
    build_dir.mkdir()
    (build_dir / "2026-06-17.script.txt").write_text("本文です。", encoding="utf-8")
    calls: list[str] = []

    monkeypatch.setattr(synthesize_daily, "BUILD_DIR", build_dir)
    monkeypatch.setattr(synthesize_daily.aivis_client, "ensure_engine", lambda: True)
    monkeypatch.setattr(synthesize_daily.aivis_client, "resolve_style_id", lambda: 1)
    monkeypatch.setattr(synthesize_daily, "split_text", lambda _text: ["本文です。"])
    monkeypatch.setattr(synthesize_daily.aivis_client, "synthesize", lambda _chunk, _style_id: _wav_bytes(b"\x01\x00"))
    monkeypatch.setattr(synthesize_daily, "convert_wav_to_mp3", lambda _wav, mp3: mp3.write_bytes(b"ID3") or 1.0)
    monkeypatch.setattr(synthesize_daily, "probe_duration_seconds", lambda _mp3: 420.0)
    monkeypatch.setattr(synthesize_daily.aivis_client, "engine_started_by_this_process", lambda: True)
    monkeypatch.setattr(synthesize_daily.aivis_client, "shutdown_started_engine", lambda: calls.append("shutdown") or True)

    assert synthesize_daily.synthesize("2026-06-17") == build_dir / "2026-06-17.mp3"
    assert calls == ["shutdown"]


def test_synthesize_shuts_down_engine_started_by_this_process_on_failure(tmp_path, monkeypatch):
    build_dir = tmp_path / "tts"
    build_dir.mkdir()
    (build_dir / "2026-06-17.script.txt").write_text("本文です。", encoding="utf-8")
    calls: list[str] = []

    monkeypatch.setattr(synthesize_daily, "BUILD_DIR", build_dir)
    monkeypatch.setattr(synthesize_daily.aivis_client, "ensure_engine", lambda: True)
    monkeypatch.setattr(synthesize_daily.aivis_client, "resolve_style_id", lambda: 1)
    monkeypatch.setattr(synthesize_daily, "split_text", lambda _text: ["本文です。"])
    monkeypatch.setattr(synthesize_daily.aivis_client, "synthesize", lambda _chunk, _style_id: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(synthesize_daily.aivis_client, "engine_started_by_this_process", lambda: True)
    monkeypatch.setattr(synthesize_daily.aivis_client, "shutdown_started_engine", lambda: calls.append("shutdown") or True)

    assert synthesize_daily.synthesize("2026-06-17") is None
    assert calls == ["shutdown"]


def test_synthesize_keeps_preexisting_engine_running(tmp_path, monkeypatch):
    build_dir = tmp_path / "tts"
    build_dir.mkdir()
    (build_dir / "2026-06-17.script.txt").write_text("本文です。", encoding="utf-8")
    calls: list[str] = []

    monkeypatch.setattr(synthesize_daily, "BUILD_DIR", build_dir)
    monkeypatch.setattr(synthesize_daily.aivis_client, "ensure_engine", lambda: True)
    monkeypatch.setattr(synthesize_daily.aivis_client, "resolve_style_id", lambda: 1)
    monkeypatch.setattr(synthesize_daily, "split_text", lambda _text: ["本文です。"])
    monkeypatch.setattr(synthesize_daily.aivis_client, "synthesize", lambda _chunk, _style_id: _wav_bytes(b"\x01\x00"))
    monkeypatch.setattr(synthesize_daily, "convert_wav_to_mp3", lambda _wav, mp3: mp3.write_bytes(b"ID3") or 1.0)
    monkeypatch.setattr(synthesize_daily, "probe_duration_seconds", lambda _mp3: 420.0)
    monkeypatch.setattr(synthesize_daily.aivis_client, "engine_started_by_this_process", lambda: False)
    monkeypatch.setattr(synthesize_daily.aivis_client, "shutdown_started_engine", lambda: calls.append("shutdown") or True)

    assert synthesize_daily.synthesize("2026-06-17") == build_dir / "2026-06-17.mp3"
    assert calls == []


def test_synthesize_shuts_down_owned_engine_when_engine_readiness_fails(tmp_path, monkeypatch):
    build_dir = tmp_path / "tts"
    build_dir.mkdir()
    (build_dir / "2026-06-17.script.txt").write_text("本文です。", encoding="utf-8")
    calls: list[str] = []

    monkeypatch.setattr(synthesize_daily, "BUILD_DIR", build_dir)
    monkeypatch.setattr(synthesize_daily.aivis_client, "ensure_engine", lambda: False)
    monkeypatch.setattr(synthesize_daily.aivis_client, "engine_started_by_this_process", lambda: True)
    monkeypatch.setattr(synthesize_daily.aivis_client, "shutdown_started_engine", lambda: calls.append("shutdown") or True)

    assert synthesize_daily.synthesize("2026-06-17") is None
    assert calls == ["shutdown"]
