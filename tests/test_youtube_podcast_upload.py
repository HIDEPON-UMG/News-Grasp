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


def test_prepare_uploads_private_without_playlist_insert(tmp_path, monkeypatch):
    """push 前 prepare は private upload だけで、playlist にはまだ出さない。"""
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
        def upload_video(self, mp4_path, metadata, *, privacy_status):
            calls.append(("upload_video", privacy_status))
            assert mp4_path == mp4
            return "video-1"

        def ensure_playlist(self):  # pragma: no cover - 呼ばれたら失敗
            raise AssertionError("prepare must not touch playlist")

        def add_video_to_playlist(self, **_kwargs):  # pragma: no cover - 呼ばれたら失敗
            raise AssertionError("prepare must not touch playlist")

    result = upload_episode.prepare("2026-06-18", client=FakeClient())

    assert result["status"] == "private"
    assert result["phase"] == "prepared"
    assert result["videoId"] == "video-1"
    assert calls == [("upload_video", "private")]


def test_finalize_publicizes_prepared_video_and_adds_playlist(tmp_path, monkeypatch):
    """Web 公開確認後 finalize で public 化し、playlist 追加する。"""
    from tools.youtube_podcast import upload_episode

    build_dir = tmp_path / "build" / "youtube-podcast"
    state_dir = tmp_path / ".news-grasp"
    build_dir.mkdir(parents=True)
    mp4 = build_dir / "2026-06-18.mp4"
    mp4.write_bytes(b"mp4")
    monkeypatch.setattr(upload_episode, "BUILD_DIR", build_dir)
    monkeypatch.setattr(upload_episode, "LOCAL_STATE_DIR", state_dir)
    (build_dir / "uploads.json").write_text(
        json.dumps(
            {
                "2026-06-18": {
                    "phase": "prepared",
                    "status": "private",
                    "videoId": "video-1",
                    "mp4_sha256": upload_episode.sha256_file(mp4),
                }
            }
        ),
        encoding="utf-8",
    )

    calls: list[tuple[str, str | None]] = []

    class FakeClient:
        def ensure_playlist(self):
            calls.append(("ensure_playlist", None))
            return "playlist-1"

        def update_video_privacy(self, *, video_id, privacy_status):
            calls.append(("update_video_privacy", f"{video_id}:{privacy_status}"))
            return {"id": video_id, "status": {"privacyStatus": privacy_status}}

        def add_video_to_playlist(self, *, video_id, playlist_id):
            calls.append(("add_video_to_playlist", f"{video_id}:{playlist_id}"))
            return "playlist-item-1"

        def upload_video(self, *_args, **_kwargs):  # pragma: no cover - 呼ばれたら失敗
            raise AssertionError("finalize must not re-upload")

    result = upload_episode.finalize("2026-06-18", client=FakeClient())

    assert result["status"] == "public"
    assert result["phase"] == "finalized"
    assert result["playlistId"] == "playlist-1"
    assert calls == [
        ("ensure_playlist", None),
        ("update_video_privacy", "video-1:public"),
        ("add_video_to_playlist", "video-1:playlist-1"),
    ]


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


def test_deepdive_metadata_and_upload_state_are_separate(tmp_path, monkeypatch):
    from tools.youtube_podcast import upload_episode

    build_dir = tmp_path / "build" / "youtube-podcast"
    deepdive_build_dir = tmp_path / "build" / "youtube-podcast-deepdive"
    deepdive_build_dir.mkdir(parents=True)
    (deepdive_build_dir / "2026-06-21.mp4").write_bytes(b"mp4")
    md_dir = tmp_path / "digest" / "DeepDive"
    md_dir.mkdir(parents=True)
    (md_dir / "2026-06-21-DeepDive.md").write_text(
        "---\n"
        "title: アクセンチュア急落、AI変革の時間差\n"
        "theme: AccentureのFY2026 Q3決算をどう読むか\n"
        "---\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(upload_episode, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(upload_episode, "BUILD_DIR", build_dir)
    monkeypatch.setattr(upload_episode, "DEEPDIVE_BUILD_DIR", deepdive_build_dir)

    metadata = upload_episode.build_metadata("2026-06-21", kind="deepdive")
    result = upload_episode.publish("2026-06-21", dry_run=True, kind="deepdive")

    assert metadata["title"] == "News-Grasp DeepDive Dialogue 2026-06-21"
    assert "アクセンチュア急落、AI変革の時間差" in metadata["description"]
    assert "解説対談" in metadata["description"]
    assert "DeepDive" in metadata["tags"]
    assert result["mp4_path"] == str(deepdive_build_dir / "2026-06-21.mp4")
    assert result["metadata"]["title"] == metadata["title"]
