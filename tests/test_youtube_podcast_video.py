from __future__ import annotations

from unittest.mock import patch


def test_build_video_uses_static_cover_and_bounded_ffmpeg(tmp_path, monkeypatch):
    from tools.youtube_podcast import build_video

    tts_dir = tmp_path / "build" / "tts"
    podcast_dir = tmp_path / "build" / "youtube-podcast"
    tts_dir.mkdir(parents=True)
    audio = tts_dir / "2026-06-18.mp3"
    cover = tmp_path / "cover.png"
    audio.write_bytes(b"ID3")
    cover.write_bytes(b"PNG")
    monkeypatch.setattr(build_video, "TTS_BUILD_DIR", tts_dir)
    monkeypatch.setattr(build_video, "BUILD_DIR", podcast_dir)
    monkeypatch.setenv("NEWS_GRASP_PODCAST_COVER", str(cover))

    with patch.object(build_video.proc, "quiet_run") as quiet_run, \
        patch.object(build_video, "probe_duration_seconds", return_value=421.0):
        result = build_video.build("2026-06-18")

    assert result is not None
    assert result["date"] == "2026-06-18"
    assert result["mp4_path"] == str(podcast_dir / "2026-06-18.mp4")
    assert result["cover_path"] == str(cover)
    args = quiet_run.call_args.args[0]
    assert args[:4] == ["ffmpeg", "-y", "-loop", "1"]
    assert str(cover) in [str(arg) for arg in args]
    assert str(audio) in [str(arg) for arg in args]
    assert "-c:v" in args
    assert args[args.index("-c:v") + 1] == "libx264"
    assert "-tune" in args
    assert args[args.index("-tune") + 1] == "stillimage"
    assert "-c:a" in args
    assert args[args.index("-c:a") + 1] == "aac"
    assert "-pix_fmt" in args
    assert args[args.index("-pix_fmt") + 1] == "yuv420p"
    assert "-shortest" in args
    assert args[-1] == podcast_dir / "2026-06-18.mp4"


def test_build_video_returns_none_when_cover_is_missing(tmp_path, monkeypatch):
    from tools.youtube_podcast import build_video

    tts_dir = tmp_path / "tts"
    tts_dir.mkdir()
    (tts_dir / "2026-06-18.mp3").write_bytes(b"ID3")
    monkeypatch.setattr(build_video, "TTS_BUILD_DIR", tts_dir)
    monkeypatch.setenv("NEWS_GRASP_PODCAST_COVER", str(tmp_path / "missing.png"))

    with patch.object(build_video.proc, "quiet_run") as quiet_run:
        assert build_video.build("2026-06-18") is None

    quiet_run.assert_not_called()


def test_build_video_deepdive_uses_dialogue_audio_cover_and_separate_output(tmp_path, monkeypatch):
    from tools.youtube_podcast import build_video

    tts_dir = tmp_path / "build" / "tts"
    deepdive_tts_dir = tts_dir / "deepdive"
    podcast_dir = tmp_path / "build" / "youtube-podcast"
    deepdive_podcast_dir = tmp_path / "build" / "youtube-podcast-deepdive"
    deepdive_tts_dir.mkdir(parents=True)
    audio = deepdive_tts_dir / "2026-06-21.mp3"
    cover = tmp_path / "deepdive-cover.png"
    audio.write_bytes(b"ID3")
    cover.write_bytes(b"PNG")
    monkeypatch.setattr(build_video, "TTS_BUILD_DIR", tts_dir)
    monkeypatch.setattr(build_video, "BUILD_DIR", podcast_dir)
    monkeypatch.setattr(build_video, "DEEPDIVE_BUILD_DIR", deepdive_podcast_dir)
    monkeypatch.setattr(build_video, "DEEPDIVE_COVER_PATH", cover)

    with patch.object(build_video.proc, "quiet_run") as quiet_run, \
        patch.object(build_video, "probe_duration_seconds", return_value=332.0):
        result = build_video.build("2026-06-21", kind="deepdive")

    assert result is not None
    assert result["kind"] == "deepdive"
    assert result["mp3_path"] == str(audio)
    assert result["cover_path"] == str(cover)
    assert result["mp4_path"] == str(deepdive_podcast_dir / "2026-06-21.mp4")
    args = quiet_run.call_args.args[0]
    assert str(audio) in [str(arg) for arg in args]
    assert str(cover) in [str(arg) for arg in args]
