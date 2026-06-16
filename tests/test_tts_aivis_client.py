from __future__ import annotations

import inspect

import pytest

from tools.tts import aivis_client


def test_live_aivis_smoke_is_excluded_from_static_runner_gate():
    """実 AivisSpeech 依存 test は runner の pytest-static gate に混入させない。"""
    source = inspect.getsource(test_aivis_client_resolves_style_and_synthesizes_short_wav_when_engine_is_up)
    assert "@pytest.mark.network" in source.split("def ", 1)[0]


def test_aivis_default_params_match_reviewed_voice_settings():
    assert aivis_client.DEFAULT_PARAMS["speedScale"] == 1.0
    assert aivis_client.DEFAULT_PARAMS["pitchScale"] == 0.0
    assert aivis_client.DEFAULT_PARAMS["intonationScale"] == 1.1
    assert aivis_client.DEFAULT_PARAMS["tempoDynamicsScale"] == 1.2
    assert aivis_client.DEFAULT_PARAMS["volumeScale"] == 1.0
    assert aivis_client.DEFAULT_PARAMS["pauseLengthScale"] == 1.1
    assert aivis_client.DEFAULT_PARAMS["outputStereo"] is False


@pytest.mark.network
def test_aivis_client_resolves_style_and_synthesizes_short_wav_when_engine_is_up():
    if not aivis_client.is_engine_up():
        pytest.skip("AivisSpeech engine is not running")

    style_id = aivis_client.resolve_style_id()
    wav = aivis_client.synthesize("こんにちは。", style_id)

    assert isinstance(style_id, int)
    assert wav.startswith(b"RIFF")
    assert len(wav) > 1024
