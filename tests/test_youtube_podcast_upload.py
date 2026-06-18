from __future__ import annotations

import json


def test_metadata_uses_daily_briefing_title_and_summary_sources(tmp_path, monkeypatch):
    from tools.youtube_podcast import upload_episode

    digest_summary = tmp_path / "digest" / "Summary"
    digest_summary.mkdir(parents=True)
    (digest_summary / "2026-06-18-audio-script.md").write_text(
        "# News-Grasp 朗読\n\n今日はAIと金融のニュースを整理します。\n\n出典: https://example.com/a\n",
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "publish-status.json").write_text(
        json.dumps({"issue_date": "2026-06-18", "published_ok": True}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(upload_episode, "REPO_ROOT", tmp_path)

    metadata = upload_episode.build_metadata("2026-06-18")

    assert metadata["title"] == "News-Grasp Daily News Briefing 2026-06-18"
    assert "今日はAIと金融のニュースを整理します。" in metadata["description"]
    assert "https://example.com/a" in metadata["description"]
    assert "News-Grasp" in metadata["tags"]
    assert "ニュース" in metadata["tags"]


def test_publish_skips_api_when_same_date_is_already_public(tmp_path, monkeypatch):
    from tools.youtube_podcast import upload_episode

    build_dir = tmp_path / "build" / "youtube-podcast"
    build_dir.mkdir(parents=True)
    mp4 = build_dir / "2026-06-18.mp4"
    mp4.write_bytes(b"mp4")
    (build_dir / "uploads.json").write_text(
        json.dumps(
            {
                "2026-06-18": {
                    "status": "public",
                    "videoId": "video-1",
                    "playlistItemId": "playlist-item-1",
                    "mp4_sha256": upload_episode.sha256_file(mp4),
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(upload_episode, "BUILD_DIR", build_dir)

    class FakeClient:
        def upload_video(self, *_args, **_kwargs):  # pragma: no cover - 呼ばれたら失敗
            raise AssertionError("upload_video must not be called")

    result = upload_episode.publish("2026-06-18", client=FakeClient(), dry_run=False)

    assert result["skipped"] is True
    assert result["videoId"] == "video-1"


def test_publish_calls_video_upload_then_playlist_insert(tmp_path, monkeypatch):
    from tools.youtube_podcast import upload_episode

    build_dir = tmp_path / "build" / "youtube-podcast"
    state_dir = tmp_path / ".news-grasp"
    build_dir.mkdir(parents=True)
    mp4 = build_dir / "2026-06-18.mp4"
    mp4.write_bytes(b"mp4")
    monkeypatch.setattr(upload_episode, "BUILD_DIR", build_dir)
    monkeypatch.setattr(upload_episode, "LOCAL_STATE_DIR", state_dir)
    monkeypatch.setattr(upload_episode, "build_metadata", lambda _day: {"title": "t", "description": "d", "tags": ["x"]})

    calls: list[tuple[str, str | None]] = []

    class FakeClient:
        def ensure_playlist(self):
            calls.append(("ensure_playlist", None))
            return "playlist-1"

        def upload_video(self, mp4_path, metadata, *, privacy_status):
            calls.append(("upload_video", privacy_status))
            assert mp4_path == mp4
            assert metadata["title"] == "t"
            return "video-1"

        def add_video_to_playlist(self, *, video_id, playlist_id):
            calls.append(("add_video_to_playlist", f"{video_id}:{playlist_id}"))
            return "playlist-item-1"

    result = upload_episode.publish("2026-06-18", client=FakeClient(), dry_run=False)

    assert result["videoId"] == "video-1"
    assert calls == [
        ("ensure_playlist", None),
        ("upload_video", "public"),
        ("add_video_to_playlist", "video-1:playlist-1"),
    ]
    state = json.loads((build_dir / "uploads.json").read_text(encoding="utf-8"))
    assert state["2026-06-18"]["status"] == "public"


def test_dry_run_does_not_call_youtube_api(tmp_path, monkeypatch):
    from tools.youtube_podcast import upload_episode

    build_dir = tmp_path / "build" / "youtube-podcast"
    build_dir.mkdir(parents=True)
    (build_dir / "2026-06-18.mp4").write_bytes(b"mp4")
    monkeypatch.setattr(upload_episode, "BUILD_DIR", build_dir)
    monkeypatch.setattr(upload_episode, "build_metadata", lambda _day: {"title": "t", "description": "d", "tags": []})

    result = upload_episode.publish("2026-06-18", dry_run=True)

    assert result["dry_run"] is True
    assert result["videoId"] == ""
