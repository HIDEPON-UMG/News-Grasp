from __future__ import annotations

import json

import pytest


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


def test_metadata_strips_tts_outline_comment_from_daily_description(tmp_path, monkeypatch):
    from tools.youtube_podcast import upload_episode

    digest_summary = tmp_path / "digest" / "Summary"
    digest_summary.mkdir(parents=True)
    (digest_summary / "2026-07-04-audio-script.md").write_text(
        "# News-Grasp 朗読\n\n"
        "<!-- tts-outline\n"
        "中心論点: テスト用の設計メモ\n"
        "-->\n\n"
        "7月4日土曜日、朝のニュースをお伝えします。\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(upload_episode, "REPO_ROOT", tmp_path)

    metadata = upload_episode.build_metadata("2026-07-04")

    assert "<!--" not in metadata["description"]
    assert "tts-outline" not in metadata["description"]
    assert "7月4日土曜日、朝のニュースをお伝えします。" in metadata["description"]


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


def test_youtube_client_marks_uploaded_and_finalized_videos_embeddable(tmp_path, monkeypatch):
    import sys
    import types

    from tools.youtube_podcast import upload_episode

    google_module = types.ModuleType("googleapiclient")
    http_module = types.ModuleType("googleapiclient.http")

    class FakeMediaFileUpload:
        def __init__(self, filename, **kwargs):
            self.filename = filename
            self.kwargs = kwargs

    http_module.MediaFileUpload = FakeMediaFileUpload
    google_module.http = http_module
    monkeypatch.setitem(sys.modules, "googleapiclient", google_module)
    monkeypatch.setitem(sys.modules, "googleapiclient.http", http_module)

    bodies: list[dict] = []

    class FakeRequest:
        def __init__(self, response):
            self.response = response

        def execute(self):
            return self.response

    class FakeVideos:
        def insert(self, **kwargs):
            bodies.append(kwargs["body"])
            return FakeRequest({"id": "video-1"})

        def update(self, **kwargs):
            bodies.append(kwargs["body"])
            return FakeRequest({"id": kwargs["body"]["id"], "status": kwargs["body"]["status"]})

    class FakeService:
        def videos(self):
            return FakeVideos()

    mp4 = tmp_path / "episode.mp4"
    mp4.write_bytes(b"mp4")
    client = upload_episode.YouTubePodcastClient(FakeService())

    client.upload_video(mp4, {"title": "t", "description": "d"}, privacy_status="private")
    client.update_video_privacy(video_id="video-1", privacy_status="public")

    assert bodies[0]["status"]["privacyStatus"] == "private"
    assert bodies[0]["status"]["embeddable"] is True
    assert bodies[1]["status"]["privacyStatus"] == "public"
    assert bodies[1]["status"]["embeddable"] is True


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


def test_prepare_dry_run_cli_does_not_call_youtube_api(tmp_path, monkeypatch):
    """NoPublish runner は upload_episode --prepare --dry-run で private upload も避ける。"""
    from tools.youtube_podcast import upload_episode

    build_dir = tmp_path / "build" / "youtube-podcast"
    build_dir.mkdir(parents=True)
    (build_dir / "2026-06-18.mp4").write_bytes(b"mp4")
    monkeypatch.setattr(upload_episode, "BUILD_DIR", build_dir)
    monkeypatch.setattr(upload_episode, "build_metadata", lambda _day, kind="daily": {"title": "t", "description": "d", "tags": []})

    assert upload_episode.main(["2026-06-18", "--prepare", "--dry-run"]) == 0
    assert not (build_dir / "uploads.json").exists()


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


def test_deepdive_finalize_adds_video_to_deepdive_and_primary_podcast_playlists(tmp_path, monkeypatch):
    """DeepDive 対談は専用 playlist と News-Grasp Podcast 本体の両方へ載せる。"""
    from tools.youtube_podcast import upload_episode

    deepdive_build_dir = tmp_path / "build" / "youtube-podcast-deepdive"
    state_dir = tmp_path / ".news-grasp"
    deepdive_build_dir.mkdir(parents=True)
    mp4 = deepdive_build_dir / "2026-06-21.mp4"
    mp4.write_bytes(b"mp4")
    monkeypatch.setattr(upload_episode, "DEEPDIVE_BUILD_DIR", deepdive_build_dir)
    monkeypatch.setattr(upload_episode, "LOCAL_STATE_DIR", state_dir)
    (deepdive_build_dir / "uploads.json").write_text(
        json.dumps(
            {
                "2026-06-21": {
                    "phase": "prepared",
                    "status": "private",
                    "videoId": "deepdive-video-1",
                    "mp4_sha256": upload_episode.sha256_file(mp4),
                }
            }
        ),
        encoding="utf-8",
    )

    calls: list[tuple[str, str]] = []

    class FakeClient:
        def ensure_playlist(self, kind="daily"):
            calls.append(("ensure_playlist", kind))
            return "playlist-primary" if kind == "daily" else "playlist-deepdive"

        def update_video_privacy(self, *, video_id, privacy_status):
            calls.append(("update_video_privacy", f"{video_id}:{privacy_status}"))
            return {"id": video_id, "status": {"privacyStatus": privacy_status}}

        def add_video_to_playlist(self, *, video_id, playlist_id):
            calls.append(("add_video_to_playlist", f"{video_id}:{playlist_id}"))
            return f"item-{playlist_id}"

        def upload_video(self, *_args, **_kwargs):  # pragma: no cover - 呼ばれたら失敗
            raise AssertionError("finalize must not re-upload")

    result = upload_episode.finalize("2026-06-21", client=FakeClient(), kind="deepdive")

    assert result["playlistId"] == "playlist-deepdive"
    assert result["playlistItemId"] == "item-playlist-deepdive"
    assert result["primaryPodcastPlaylistId"] == "playlist-primary"
    assert result["primaryPodcastPlaylistItemId"] == "item-playlist-primary"
    assert calls == [
        ("ensure_playlist", "deepdive"),
        ("update_video_privacy", "deepdive-video-1:public"),
        ("add_video_to_playlist", "deepdive-video-1:playlist-deepdive"),
        ("ensure_playlist", "daily"),
        ("add_video_to_playlist", "deepdive-video-1:playlist-primary"),
    ]


def test_deepdive_finalize_repairs_public_video_missing_primary_podcast_playlist(tmp_path, monkeypatch):
    """既に公開済みの DeepDive でも primary Podcast 所属だけ欠けていれば補修する。"""
    from tools.youtube_podcast import upload_episode

    deepdive_build_dir = tmp_path / "build" / "youtube-podcast-deepdive"
    state_dir = tmp_path / ".news-grasp"
    deepdive_build_dir.mkdir(parents=True)
    mp4 = deepdive_build_dir / "2026-07-01.mp4"
    mp4.write_bytes(b"mp4")
    monkeypatch.setattr(upload_episode, "DEEPDIVE_BUILD_DIR", deepdive_build_dir)
    monkeypatch.setattr(upload_episode, "LOCAL_STATE_DIR", state_dir)
    (deepdive_build_dir / "uploads.json").write_text(
        json.dumps(
            {
                "2026-07-01": {
                    "phase": "finalized",
                    "status": "public",
                    "videoId": "deepdive-video-1",
                    "playlistId": "playlist-deepdive",
                    "playlistItemId": "item-playlist-deepdive",
                    "mp4_sha256": upload_episode.sha256_file(mp4),
                }
            }
        ),
        encoding="utf-8",
    )

    calls: list[tuple[str, str]] = []

    class FakeClient:
        def ensure_playlist(self, kind="daily"):
            calls.append(("ensure_playlist", kind))
            return "playlist-primary" if kind == "daily" else "playlist-deepdive"

        def update_video_privacy(self, *, video_id, privacy_status):
            calls.append(("update_video_privacy", f"{video_id}:{privacy_status}"))
            return {"id": video_id, "status": {"privacyStatus": privacy_status}}

        def add_video_to_playlist(self, *, video_id, playlist_id):
            calls.append(("add_video_to_playlist", f"{video_id}:{playlist_id}"))
            return f"item-{playlist_id}"

        def upload_video(self, *_args, **_kwargs):  # pragma: no cover - 呼ばれたら失敗
            raise AssertionError("finalize must not re-upload")

    result = upload_episode.finalize("2026-07-01", client=FakeClient(), kind="deepdive")

    assert result["skipped"] is False
    assert result["videoId"] == "deepdive-video-1"
    assert result["playlistItemId"] == "item-playlist-deepdive"
    assert result["primaryPodcastPlaylistId"] == "playlist-primary"
    assert result["primaryPodcastPlaylistItemId"] == "item-playlist-primary"
    assert calls == [
        ("update_video_privacy", "deepdive-video-1:public"),
        ("ensure_playlist", "daily"),
        ("add_video_to_playlist", "deepdive-video-1:playlist-primary"),
    ]


def test_audit_playlist_uniqueness_rejects_e2e_leftovers(tmp_path, monkeypatch):
    """公開 E2E 後に同日重複や Deleted video item が残れば Green にしない。"""
    from tools.youtube_podcast import upload_episode

    daily_build_dir = tmp_path / "build" / "youtube-podcast"
    deepdive_build_dir = tmp_path / "build" / "youtube-podcast-deepdive"
    daily_build_dir.mkdir(parents=True)
    deepdive_build_dir.mkdir(parents=True)
    monkeypatch.setattr(upload_episode, "BUILD_DIR", daily_build_dir)
    monkeypatch.setattr(upload_episode, "DEEPDIVE_BUILD_DIR", deepdive_build_dir)
    (daily_build_dir / "uploads.json").write_text(
        json.dumps(
            {
                "2026-06-29": {
                    "status": "public",
                    "videoId": "daily-current",
                    "playlistId": "playlist-primary",
                }
            }
        ),
        encoding="utf-8",
    )
    (deepdive_build_dir / "uploads.json").write_text(
        json.dumps(
            {
                "2026-06-29": {
                    "status": "public",
                    "videoId": "deepdive-current",
                    "playlistId": "playlist-deepdive",
                    "primaryPodcastPlaylistId": "playlist-primary",
                }
            }
        ),
        encoding="utf-8",
    )

    class FakeClient:
        def list_playlist_items(self, *, playlist_id):
            if playlist_id == "playlist-primary":
                return [
                    {"playlistItemId": "item-daily", "videoId": "daily-current", "title": "News-Grasp Daily News Briefing 2026-06-29"},
                    {"playlistItemId": "item-deepdive", "videoId": "deepdive-current", "title": "News-Grasp DeepDive Dialogue 2026-06-29"},
                    {"playlistItemId": "item-deleted", "videoId": "old-daily", "title": "Deleted video"},
                ]
            if playlist_id == "playlist-deepdive":
                return [
                    {"playlistItemId": "item-deepdive-main", "videoId": "deepdive-current", "title": "News-Grasp DeepDive Dialogue 2026-06-29"},
                    {"playlistItemId": "item-deepdive-old", "videoId": "old-deepdive", "title": "Deleted video"},
                ]
            raise AssertionError(playlist_id)

    result = upload_episode.audit_playlist_uniqueness("2026-06-29", client=FakeClient())

    assert result["ok"] is False
    assert {issue["reason"] for issue in result["issues"]} == {
        "podcast_playlist_deleted_video_item",
    }
    assert not any(issue.get("videoId") == "deepdive-current" for issue in result["issues"])
    assert {surface["kind"] for surface in result["surfaces"]} == {"daily", "deepdive", "deepdive-primary"}


def test_same_day_older_marker_recovery_never_regresses_newer_active_projection(
    tmp_path,
    monkeypatch,
):
    """同日旧runのprovider marker復旧は履歴だけを更新し、activeを後退させない。"""

    from tools.youtube_podcast import upload_episode

    day = "2026-09-03"
    build_dir = tmp_path / "build" / "youtube-podcast"
    state_dir = tmp_path / ".news-grasp"
    build_dir.mkdir(parents=True)
    mp4 = build_dir / f"{day}.mp4"
    mp4.write_bytes(b"same-day-fixture-mp4")
    monkeypatch.setattr(upload_episode, "BUILD_DIR", build_dir)
    monkeypatch.setattr(upload_episode, "LOCAL_STATE_DIR", state_dir)
    monkeypatch.setattr(
        upload_episode,
        "_build_metadata",
        lambda _day, _kind: {"title": f"fixture {_day}", "description": "fixture", "tags": []},
    )

    payload_identity = upload_episode.sha256_file(mp4)
    old_run = "run-old-20260903"
    old_bundle = "bundle-old-20260903"
    new_run = "run-new-20260903"
    new_bundle = "bundle-new-20260903"

    def operation_kwargs(run_id, bundle_id, operation_id):
        return {
            "run_id": run_id,
            "bundle_id": bundle_id,
            "operation_id": operation_id,
            "payload_identity": payload_identity,
            "operation_marker": upload_episode.build_operation_marker(
                run_id=run_id,
                bundle_id=bundle_id,
                operation_id=operation_id,
                payload_identity=payload_identity,
            ),
        }

    class FakeClient:
        def __init__(self):
            self.marker_results = {}
            self.video_privacy = {}
            self.playlist_memberships = {}
            self.upload_calls = []
            self.provider_mutations = []

        def find_videos_by_operation_marker(self, operation_marker):
            return list(self.marker_results.get(operation_marker, []))

        def upload_video(self, _mp4_path, metadata, *, privacy_status):
            video_id = f"video-{len(self.upload_calls) + 1}"
            marker = str(metadata["description"]).splitlines()[-1]
            self.marker_results[marker] = [
                {"videoId": video_id, "markerCount": 1, "title": metadata["title"]}
            ]
            self.video_privacy[video_id] = privacy_status
            self.upload_calls.append((video_id, marker, privacy_status))
            return video_id

        def get_video_privacy_status(self, *, video_id):
            return self.video_privacy[video_id]

        def update_video_privacy(self, *, video_id, privacy_status):
            self.video_privacy[video_id] = privacy_status
            self.provider_mutations.append(("privacy", video_id, privacy_status))
            return {"id": video_id, "status": {"privacyStatus": privacy_status}}

        def ensure_playlist(self, kind="daily"):
            return f"playlist-{kind}"

        def list_playlist_items(self, *, playlist_id):
            return [
                {
                    "playlistItemId": item_id,
                    "videoId": video_id,
                }
                for (current_playlist, video_id), item_id in self.playlist_memberships.items()
                if current_playlist == playlist_id
            ]

        def add_video_to_playlist(self, *, video_id, playlist_id):
            item_id = f"item-{video_id}-{playlist_id}"
            self.playlist_memberships[(playlist_id, video_id)] = item_id
            self.provider_mutations.append(("playlist", video_id, playlist_id))
            return item_id

        def delete_playlist_item(self, playlist_item_id):  # pragma: no cover - protocol completeness
            return {"id": playlist_item_id}

    client = FakeClient()
    old_prepare = operation_kwargs(old_run, old_bundle, "youtube_daily_prepare")
    new_prepare = operation_kwargs(new_run, new_bundle, "youtube_daily_prepare")
    old_finalize = operation_kwargs(old_run, old_bundle, "youtube_daily_finalize")

    # 先行旧runは正常prepareでactive projectionを作り、新runの正常prepareだけが
    # 同日active pointerを進められることを固定する。
    old_prepared = upload_episode.prepare(day, client=client, **old_prepare)
    old_key = upload_episode._upload_history_key(
        run_id=old_run,
        bundle_id=old_bundle,
        kind="daily",
        payload_identity=payload_identity,
    )
    state = json.loads((build_dir / "uploads.json").read_text(encoding="utf-8"))
    assert state["activeUploadKeys"][day] == old_key
    assert state[day]["videoId"] == old_prepared["videoId"]

    new_prepared = upload_episode.prepare(day, client=client, **new_prepare)
    new_key = upload_episode._upload_history_key(
        run_id=new_run,
        bundle_id=new_bundle,
        kind="daily",
        payload_identity=payload_identity,
    )
    assert new_prepared["videoId"] != old_prepared["videoId"]
    state = json.loads((build_dir / "uploads.json").read_text(encoding="utf-8"))
    assert state["activeUploadKeys"][day] == new_key
    assert state[day]["runId"] == new_run
    assert state[day]["videoId"] == new_prepared["videoId"]

    # 旧prepare markerの再照合は旧historyだけを更新し、同日newer activeを維持する。
    old_reconciled_prepare = upload_episode.prepare(day, client=client, **old_prepare)
    assert old_reconciled_prepare["skipped"] is True
    assert old_reconciled_prepare["videoId"] == old_prepared["videoId"]
    state = json.loads((build_dir / "uploads.json").read_text(encoding="utf-8"))
    assert state["activeUploadKeys"][day] == new_key
    assert state[day]["videoId"] == new_prepared["videoId"]

    # 旧finalizeはprovider privacy/playlistを一度だけ確定し、履歴rowをpublicへ
    # 進めるが、active day projectionはnewer rowから後退させない。
    client.marker_results[old_finalize["operation_marker"]] = [
        {"videoId": old_prepared["videoId"], "markerCount": 1, "title": "old"}
    ]
    old_finalized = upload_episode.finalize(day, client=client, **old_finalize)
    assert old_finalized["videoId"] == old_prepared["videoId"]
    assert old_finalized["status"] == "public"
    state = json.loads((build_dir / "uploads.json").read_text(encoding="utf-8"))
    assert state["activeUploadKeys"][day] == new_key
    assert state[day]["videoId"] == new_prepared["videoId"]
    assert state["uploadHistoryV2"][old_key]["status"] == "public"

    # finalize後のreconcileもprovider markerを再送せず、同じ旧historyだけを
    # 再照合する。uploadは最初の旧/new prepareの2回から増えない。
    old_reconciled_finalize = upload_episode.reconcile(
        day,
        client=client,
        phase="finalize",
        **old_finalize,
    )
    assert old_reconciled_finalize["reconciled"] is True
    assert old_reconciled_finalize["videoId"] == old_prepared["videoId"]
    state = json.loads((build_dir / "uploads.json").read_text(encoding="utf-8"))
    assert state["activeUploadKeys"][day] == new_key
    assert state[day]["runId"] == new_run
    assert state[day]["videoId"] == new_prepared["videoId"]
    assert len(client.upload_calls) == 2


@pytest.mark.parametrize("crash_step", ("privacy", "playlist"), ids=("privacy", "playlist"))
def test_youtube_finalize_resume_after_provider_crash_repeats_no_applied_step(
    tmp_path,
    monkeypatch,
    crash_step,
):
    """provider適用直後の停止は、適用済みstepを再実行せず不足分だけ補う。"""

    from tools.youtube_podcast import upload_episode

    day = "2026-09-04"
    build_dir = tmp_path / "build" / "youtube-podcast"
    state_dir = tmp_path / ".news-grasp"
    build_dir.mkdir(parents=True)
    mp4 = build_dir / f"{day}.mp4"
    mp4.write_bytes(b"provider-crash-resume-fixture")
    monkeypatch.setattr(upload_episode, "BUILD_DIR", build_dir)
    monkeypatch.setattr(upload_episode, "LOCAL_STATE_DIR", state_dir)
    monkeypatch.setattr(
        upload_episode,
        "_build_metadata",
        lambda _day, _kind: {"title": f"fixture {_day}", "description": "fixture", "tags": []},
    )

    payload_identity = upload_episode.sha256_file(mp4)
    run_id = "run-youtube-crash-20260904"
    bundle_id = "bundle-youtube-crash-20260904"

    def operation_kwargs(operation_id):
        return {
            "run_id": run_id,
            "bundle_id": bundle_id,
            "operation_id": operation_id,
            "payload_identity": payload_identity,
            "operation_marker": upload_episode.build_operation_marker(
                run_id=run_id,
                bundle_id=bundle_id,
                operation_id=operation_id,
                payload_identity=payload_identity,
            ),
        }

    class ProviderCrash(BaseException):
        pass

    class FakeClient:
        def __init__(self):
            self.marker_results = {}
            self.video_privacy = {}
            self.playlist_memberships = {}
            self.upload_calls = []
            self.provider_mutations = []
            self.crashed = False

        def find_videos_by_operation_marker(self, operation_marker):
            return list(self.marker_results.get(operation_marker, []))

        def upload_video(self, _mp4_path, metadata, *, privacy_status):
            video_id = "video-crash-resume-1"
            marker = str(metadata["description"]).splitlines()[-1]
            self.marker_results[marker] = [
                {"videoId": video_id, "markerCount": 1, "title": metadata["title"]}
            ]
            self.video_privacy[video_id] = privacy_status
            self.upload_calls.append((video_id, marker, privacy_status))
            return video_id

        def get_video_privacy_status(self, *, video_id):
            return self.video_privacy[video_id]

        def update_video_privacy(self, *, video_id, privacy_status):
            self.video_privacy[video_id] = privacy_status
            self.provider_mutations.append(("privacy", video_id, privacy_status))
            if crash_step == "privacy" and not self.crashed:
                self.crashed = True
                raise ProviderCrash("privacy_applied_before_process_crash")
            return {"id": video_id, "status": {"privacyStatus": privacy_status}}

        def ensure_playlist(self, kind="daily"):
            return f"playlist-{kind}"

        def list_playlist_items(self, *, playlist_id):
            return [
                {"playlistItemId": item_id, "videoId": video_id}
                for (current_playlist, video_id), item_id in self.playlist_memberships.items()
                if current_playlist == playlist_id
            ]

        def add_video_to_playlist(self, *, video_id, playlist_id):
            item_id = f"item-{video_id}-{playlist_id}"
            self.playlist_memberships[(playlist_id, video_id)] = item_id
            self.provider_mutations.append(("playlist", video_id, playlist_id))
            if crash_step == "playlist" and not self.crashed:
                self.crashed = True
                raise ProviderCrash("playlist_applied_before_process_crash")
            return item_id

    client = FakeClient()
    upload_episode.prepare(
        day,
        client=client,
        **operation_kwargs("youtube_daily_prepare"),
    )

    with pytest.raises(ProviderCrash):
        upload_episode.finalize(
            day,
            client=client,
            **operation_kwargs("youtube_daily_finalize"),
        )

    assert len(client.upload_calls) == 1
    assert sum(item[0] == "privacy" for item in client.provider_mutations) == 1
    assert sum(item[0] == "playlist" for item in client.provider_mutations) == (
        1 if crash_step == "playlist" else 0
    )
    crashed_state = json.loads((build_dir / "uploads.json").read_text(encoding="utf-8"))
    assert crashed_state[day]["phase"] == "prepared"
    assert crashed_state[day]["status"] == "private"

    resumed = upload_episode.finalize(
        day,
        client=client,
        **operation_kwargs("youtube_daily_finalize"),
    )

    assert resumed["status"] == "public"
    assert resumed["phase"] == "finalized"
    assert len(client.upload_calls) == 1
    assert sum(item[0] == "privacy" for item in client.provider_mutations) == 1
    assert sum(item[0] == "playlist" for item in client.provider_mutations) == 1
    final_state = json.loads((build_dir / "uploads.json").read_text(encoding="utf-8"))
    assert final_state[day]["status"] == "public"
    assert final_state[day]["videoId"] == "video-crash-resume-1"
