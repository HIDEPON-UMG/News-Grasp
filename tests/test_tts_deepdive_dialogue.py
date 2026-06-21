from __future__ import annotations

from pathlib import Path

from tools.tts import deepdive_dialogue


def test_dialogue_roles_use_requested_aivis_model_uuids():
    assert deepdive_dialogue.ROLES["senior"].model_uuid == "47e53151-a378-46f3-abee-ce13aa07feb1"
    assert deepdive_dialogue.ROLES["junior"].model_uuid == "59f96896-64d2-4378-830a-4d5feb3d81aa"


def test_dialogue_uses_explicit_breathing_pause_settings():
    assert deepdive_dialogue.INTER_TURN_SILENCE_SECONDS >= 0.45
    assert deepdive_dialogue.ROLES["senior"].params["pauseLengthScale"] >= 1.3
    assert deepdive_dialogue.ROLES["junior"].params["pauseLengthScale"] >= 1.25


def test_dialogue_voice_settings_make_senior_slower_and_both_roles_more_expressive():
    assert deepdive_dialogue.ROLES["senior"].params["speedScale"] < 1.0
    assert deepdive_dialogue.ROLES["senior"].params["pitchScale"] == 0.10
    assert deepdive_dialogue.ROLES["junior"].params["pitchScale"] == 0.10
    assert deepdive_dialogue.ROLES["senior"].params["intonationScale"] >= 1.2
    assert deepdive_dialogue.ROLES["junior"].params["intonationScale"] >= 1.2
    assert deepdive_dialogue.ROLES["senior"].params["tempoDynamicsScale"] == 1.20
    assert deepdive_dialogue.ROLES["junior"].params["tempoDynamicsScale"] == 1.20


def test_parse_dialogue_keeps_only_script_lines():
    text = """---
title: sample
---
# Persona
若手: ゼロトラストって、社内を信じないという意味ですか。
先輩: 近いけど、正確には毎回確かめるという考え方だね。
- memo
"""

    turns = deepdive_dialogue.parse_dialogue(text)

    assert [(turn.role_key, turn.text) for turn in turns] == [
        ("junior", "ゼロトラストって、社内を信じないという意味ですか。"),
        ("senior", "近いけど、正確には毎回確かめるという考え方だね。"),
    ]


def test_validate_dialogue_requires_both_roles_and_reasonable_length():
    turns = [
        deepdive_dialogue.DialogueTurn("junior", "これは短い質問です。"),
        deepdive_dialogue.DialogueTurn("senior", "短い回答です。"),
    ]

    issues = deepdive_dialogue.validate_dialogue(turns)

    assert any("字数不足" in issue for issue in issues)


def test_synthesize_dialogue_uses_role_specific_style_ids(tmp_path, monkeypatch):
    script_path = tmp_path / "dialogue.md"
    script_path.write_text(
        "若手: AIエージェントの権限設計について質問です。\n"
        "先輩: 操作できる範囲と止め方を先に決める話だね。\n",
        encoding="utf-8",
    )
    build_dir = tmp_path / "build"
    resolved: list[str] = []
    synthesized: list[tuple[str, int]] = []

    monkeypatch.setattr(deepdive_dialogue, "BUILD_DIR", build_dir)
    monkeypatch.setattr(deepdive_dialogue.aivis_client, "ensure_engine", lambda: True)
    monkeypatch.setattr(deepdive_dialogue.aivis_client, "engine_started_by_this_process", lambda: False)
    monkeypatch.setattr(deepdive_dialogue, "validate_dialogue", lambda _turns: [])
    monkeypatch.setattr(deepdive_dialogue.synthesize_daily, "combine_wavs", lambda _wavs, out, silence_seconds=0.18: out.write_bytes(b"RIFF"))
    monkeypatch.setattr(deepdive_dialogue, "convert_voice_wav_to_delivery_mp3", lambda _wav, mp3: mp3.write_bytes(b"ID3") or 1.0)
    monkeypatch.setattr(deepdive_dialogue.synthesize_daily, "probe_duration_seconds", lambda _mp3: 305.0)

    def fake_resolve(uuid: str) -> int:
        resolved.append(uuid)
        return len(resolved) + 10

    def fake_synthesize(text: str, style_id: int, params=None) -> bytes:
        synthesized.append((text, style_id))
        return b"RIFF...."

    monkeypatch.setattr(deepdive_dialogue.aivis_client, "resolve_style_id", fake_resolve)
    monkeypatch.setattr(deepdive_dialogue.aivis_client, "synthesize", fake_synthesize)

    out = deepdive_dialogue.synthesize_dialogue(script_path, out_name="sample")

    assert out == build_dir / "sample.mp3"
    assert resolved == [
        "59f96896-64d2-4378-830a-4d5feb3d81aa",
        "47e53151-a378-46f3-abee-ce13aa07feb1",
    ]
    assert synthesized[0][1] != synthesized[1][1]
    assert out.read_bytes() == b"ID3"


def test_mix_voice_wav_with_bgm_loops_and_uses_daily_bgm_fade_settings(tmp_path, monkeypatch):
    voice = tmp_path / "voice.wav"
    bgm = tmp_path / "bgm.mp3"
    out = tmp_path / "out.mp3"
    voice.write_bytes(b"RIFF")
    bgm.write_bytes(b"ID3")
    calls = []

    monkeypatch.setattr(deepdive_dialogue.synthesize_daily, "_wav_duration_seconds", lambda _wav: 123.456)
    monkeypatch.setattr(
        deepdive_dialogue.synthesize_daily.proc,
        "quiet_run",
        lambda args, timeout: calls.append((args, timeout)),
    )

    deepdive_dialogue.mix_voice_wav_with_bgm(voice, bgm, out)

    args, timeout = calls[0]
    filter_complex = args[args.index("-filter_complex") + 1]
    assert args[args.index("-stream_loop") + 1] == "-1"
    assert deepdive_dialogue.DEFAULT_BGM_PATH.name == "office-daily-bgm-standalone.mp3"
    assert deepdive_dialogue.BGM_VOLUME_DB == 1.5
    assert f"volume={deepdive_dialogue.BGM_VOLUME_DB:.1f}dB" in filter_complex
    assert "highpass=f=110" in filter_complex
    assert "equalizer=f=170:t=q:w=0.9:g=-4.0" in filter_complex
    assert "afade=t=in:st=0:d=2" in filter_complex
    assert "afade=t=out:st=118.456:d=5" in filter_complex
    assert "amix=inputs=2:duration=first" in filter_complex
    assert timeout == deepdive_dialogue.synthesize_daily.FFMPEG_TIMEOUT_SEC


def test_convert_voice_wav_to_delivery_mp3_falls_back_when_bgm_missing(tmp_path, monkeypatch):
    wav = tmp_path / "voice.wav"
    mp3 = tmp_path / "out.mp3"
    wav.write_bytes(b"RIFF")
    called = []

    monkeypatch.setattr(deepdive_dialogue, "DEFAULT_BGM_PATH", tmp_path / "missing.mp3")

    def fake_convert(src: Path, dst: Path) -> float:
        called.append((src, dst))
        dst.write_bytes(b"ID3")
        return 1.25

    monkeypatch.setattr(deepdive_dialogue.synthesize_daily, "convert_wav_to_mp3", fake_convert)

    elapsed = deepdive_dialogue.convert_voice_wav_to_delivery_mp3(wav, mp3)

    assert elapsed == 1.25
    assert called == [(wav, mp3)]
    assert mp3.read_bytes() == b"ID3"
