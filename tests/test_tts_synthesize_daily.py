from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tools.tts import synthesize_daily


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
