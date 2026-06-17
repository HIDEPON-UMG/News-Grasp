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


def _wav_samples(wav_path: Path) -> list[int]:
    with wave.open(str(wav_path), "rb") as reader:
        frames = reader.readframes(reader.getnframes())
    return [
        int.from_bytes(frames[i:i + 2], "little", signed=True)
        for i in range(0, len(frames), 2)
    ]


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


def test_mix_voice_wav_with_bgm_uses_bounded_ffmpeg_filter(tmp_path):
    voice = tmp_path / "voice.wav"
    bgm = tmp_path / "bgm.wav"
    mp3 = tmp_path / "out.mp3"
    voice.write_bytes(_wav_bytes(b"\x01\x00" * 1000, framerate=1000))
    bgm.write_bytes(_wav_bytes(b"\xff\x3f" * 1000, framerate=1000))

    with patch.object(synthesize_daily.proc, "quiet_run") as quiet_run:
        synthesize_daily.mix_voice_wav_with_bgm(voice, bgm, mp3)

    quiet_run.assert_called_once()
    args = quiet_run.call_args.args[0]
    assert args[:4] == ["ffmpeg", "-y", "-i", voice]
    assert "-stream_loop" not in args
    filter_complex = args[args.index("-filter_complex") + 1]
    assert "volume=-9.5dB" in filter_complex
    assert "afade=t=in:st=0:d=2" in filter_complex
    assert "afade=t=out" in filter_complex
    assert "amix=inputs=2" in filter_complex
    assert "normalize=0" in filter_complex
    assert "alimiter" in filter_complex
    assert "-ac" in args
    assert args[args.index("-ac") + 1] == "1"
    assert quiet_run.call_args.kwargs["timeout"] == synthesize_daily.FFMPEG_TIMEOUT_SEC


def test_looped_bgm_bed_crossfades_loop_boundaries(tmp_path):
    bgm = tmp_path / "hard-boundary.wav"
    bed = tmp_path / "bed.wav"
    # 末尾が大きな負、先頭が大きな正。単純ループなら境界クリックが出る素材。
    bgm.write_bytes(_wav_bytes((b"\xff\x3f" * 20) + (b"\x01\xc0" * 20), framerate=40))

    synthesize_daily._write_looped_bgm_bed(
        bgm,
        bed,
        duration_seconds=2.0,
        crossfade_seconds=0.25,
    )

    samples = _wav_samples(bed)
    loop_boundary = 40
    raw_jump = 32766
    actual_jump = abs(samples[loop_boundary] - samples[loop_boundary - 1])
    assert actual_jump < raw_jump * 0.35


def test_mix_voice_wav_with_bgm_actual_output_contains_bgm_when_voice_is_silent(tmp_path):
    voice = tmp_path / "silent.wav"
    bgm = tmp_path / "bgm.wav"
    mp3 = tmp_path / "out.mp3"
    voice.write_bytes(_wav_bytes(b"\x00\x00" * 10_000, framerate=10_000))
    bgm.write_bytes(_wav_bytes(b"\xff\x3f" * 10_000, framerate=10_000))

    synthesize_daily.mix_voice_wav_with_bgm(voice, bgm, mp3)

    assert mp3.stat().st_size > 1000
    duration = synthesize_daily.probe_duration_seconds(mp3)
    assert duration is not None
    assert 0.9 <= duration <= 1.2


def test_mix_voice_wav_with_bgm_loops_bgm_until_voice_ends(tmp_path):
    voice = tmp_path / "long-silent.wav"
    bgm = tmp_path / "short-bgm.wav"
    mp3 = tmp_path / "looped.mp3"
    decoded = tmp_path / "decoded.wav"
    voice.write_bytes(_wav_bytes(b"\x00\x00" * 30_000, framerate=10_000))
    bgm.write_bytes(_wav_bytes(b"\xff\x3f" * 5_000, framerate=10_000))

    synthesize_daily.mix_voice_wav_with_bgm(voice, bgm, mp3)
    synthesize_daily.proc.quiet_run(
        ["ffmpeg", "-y", "-i", mp3, "-ac", "1", decoded],
        timeout=synthesize_daily.FFMPEG_TIMEOUT_SEC,
    )
    with wave.open(str(decoded), "rb") as reader:
        reader.setpos(int(reader.getframerate() * 2.2))
        tail = reader.readframes(int(reader.getframerate() * 0.5))
    tail_samples = [
        int.from_bytes(tail[i:i + 2], "little", signed=True)
        for i in range(0, len(tail), 2)
    ]

    assert max(abs(sample) for sample in tail_samples) > 500


def test_delivery_mp3_falls_back_to_plain_voice_when_bgm_is_missing(tmp_path, monkeypatch):
    voice = tmp_path / "voice.wav"
    mp3 = tmp_path / "out.mp3"
    voice.write_bytes(_wav_bytes(b"\x01\x00" * 1000, framerate=1000))
    monkeypatch.setattr(synthesize_daily, "DEFAULT_BGM_PATH", tmp_path / "missing-bgm.wav")

    with patch.object(synthesize_daily, "convert_wav_to_mp3", return_value=1.0) as plain, \
        patch.object(synthesize_daily, "mix_voice_wav_with_bgm") as mix:
        synthesize_daily.convert_voice_wav_to_delivery_mp3(voice, mp3)

    mix.assert_not_called()
    plain.assert_called_once_with(voice, mp3)


def test_delivery_mp3_falls_back_to_plain_voice_when_bgm_mix_fails(tmp_path, monkeypatch):
    voice = tmp_path / "voice.wav"
    bgm = tmp_path / "news-grasp-bgm.wav"
    mp3 = tmp_path / "out.mp3"
    voice.write_bytes(_wav_bytes(b"\x01\x00" * 1000, framerate=1000))
    bgm.write_bytes(b"bgm")
    monkeypatch.setattr(synthesize_daily, "DEFAULT_BGM_PATH", bgm)

    with patch.object(synthesize_daily, "mix_voice_wav_with_bgm", side_effect=RuntimeError("boom")) as mix, \
        patch.object(synthesize_daily, "convert_wav_to_mp3", return_value=1.0) as plain:
        synthesize_daily.convert_voice_wav_to_delivery_mp3(voice, mp3)

    mix.assert_called_once_with(voice, bgm, mp3)
    plain.assert_called_once_with(voice, mp3)


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
