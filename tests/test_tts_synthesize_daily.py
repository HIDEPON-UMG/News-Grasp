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
    quiet_run.assert_called_once_with(["ffmpeg", "-y", "-i", wav, "-ac", "1", "-b:a", "80k", mp3])
