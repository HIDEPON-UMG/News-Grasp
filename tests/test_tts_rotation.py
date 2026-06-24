from __future__ import annotations

import hashlib
from datetime import date, timedelta

from tools.tts import publish_audio


def test_publish_audio_rejects_invalid_date_for_paths_and_urls():
    try:
        publish_audio.audio_url("../2026-06-16")
    except ValueError as exc:
        assert "invalid audio date" in str(exc)
    else:
        raise AssertionError("invalid date should be rejected")


def test_rotate_deletes_only_assets_older_than_31_days():
    today = date(2026, 6, 16)
    assets = [
        {"name": f"{(today - timedelta(days=days)).isoformat()}.mp3"}
        for days in range(0, 36)
    ]
    calls: list[list[str]] = []

    def fake_quiet_run(args, **kwargs):
        calls.append(list(args))
        return None

    deleted = publish_audio.rotate(
        today=today,
        assets=assets,
        keep_days=31,
        quiet_run=fake_quiet_run,
    )

    assert deleted == [
        "2026-05-15.mp3",
        "2026-05-14.mp3",
        "2026-05-13.mp3",
        "2026-05-12.mp3",
    ]
    kept = {f"{(today - timedelta(days=days)).isoformat()}.mp3" for days in range(0, 32)}
    deleted_names = {call[-2] for call in calls}
    assert deleted_names.isdisjoint(kept)
    assert all(call[:3] == ["gh", "release", "delete-asset"] for call in calls)


def test_rotate_uses_timeout_bounded_gh_delete():
    today = date(2026, 6, 16)
    assets = [{"name": "2026-05-01.mp3"}]
    calls: list[dict[str, object]] = []

    def fake_quiet_run(args, **kwargs):
        calls.append({"args": list(args), "kwargs": dict(kwargs)})
        return None

    publish_audio.rotate(today=today, assets=assets, quiet_run=fake_quiet_run)

    assert calls
    assert all(call["kwargs"].get("timeout") == publish_audio.GH_TIMEOUT_SEC for call in calls)


def test_publish_uses_timeout_bounded_gh_commands(tmp_path, monkeypatch):
    mp3 = tmp_path / "2026-06-16.mp3"
    mp3.write_bytes(b"ID3")
    calls: list[dict[str, object]] = []

    def fake_quiet_run(args, **kwargs):
        calls.append({"args": list(args), "kwargs": dict(kwargs)})
        class Result:
            returncode = 0
            stdout = '{"assets":[]}'
        return Result()

    monkeypatch.setattr(publish_audio.proc, "quiet_run", fake_quiet_run)
    monkeypatch.setattr(publish_audio, "_url_returns_200", lambda _url: True)
    monkeypatch.setattr(publish_audio, "write_latest_audio", lambda _day, _url: None)
    expected_hash = hashlib.sha256(b"ID3").hexdigest()[:12]

    assert publish_audio.publish("2026-06-16", mp3) == {
        "latest_audio_url": (
            "https://github.com/HIDEPON-UMG/News-Grasp/releases/download/"
            f"audio-daily/2026-06-16.mp3?v={expected_hash}"
        ),
        "latest_audio_date": "2026-06-16",
    }
    gh_calls = [call for call in calls if call["args"][0] == "gh"]
    assert gh_calls
    assert all(call["kwargs"].get("timeout") == publish_audio.GH_TIMEOUT_SEC for call in gh_calls)


def test_audio_url_uses_confirmed_owner_and_repo():
    assert publish_audio.audio_url("2026-06-16") == (
        "https://github.com/HIDEPON-UMG/News-Grasp/releases/download/audio-daily/2026-06-16.mp3"
    )


def test_latest_audio_url_uses_mp3_content_hash_cache_buster(tmp_path):
    mp3 = tmp_path / "2026-06-16.mp3"
    mp3.write_bytes(b"corrected-audio")
    expected_hash = hashlib.sha256(b"corrected-audio").hexdigest()[:12]

    url = publish_audio.versioned_audio_url("2026-06-16", mp3)

    assert url == (
        "https://github.com/HIDEPON-UMG/News-Grasp/releases/download/"
        f"audio-daily/2026-06-16.mp3?v={expected_hash}"
    )


def test_publish_audio_main_returns_nonzero_when_publish_fails(monkeypatch):
    monkeypatch.setattr(publish_audio, "publish", lambda _date: None)

    assert publish_audio.main(["2026-06-17"]) == 1


def test_publish_audio_main_returns_zero_when_publish_succeeds(monkeypatch):
    monkeypatch.setattr(
        publish_audio,
        "publish",
        lambda _date: {"latest_audio_date": "2026-06-17", "latest_audio_url": "https://example.com/audio.mp3"},
    )

    assert publish_audio.main(["2026-06-17"]) == 0


def test_publish_audio_dry_run_writes_latest_audio_without_gh_upload(tmp_path, monkeypatch):
    """NoPublish E2E 用 dry-run は gh release upload を呼ばず latest_audio を作る。"""
    mp3 = tmp_path / "2026-06-17.mp3"
    mp3.write_bytes(b"dry-run-audio")
    latest_json = tmp_path / "latest_audio.json"
    calls: list[list[str]] = []

    def fake_quiet_run(args, **kwargs):
        calls.append(list(args))
        raise AssertionError("dry-run must not call gh")

    monkeypatch.setattr(publish_audio, "BUILD_DIR", tmp_path)
    monkeypatch.setattr(publish_audio, "LATEST_AUDIO_JSON", latest_json)
    monkeypatch.setattr(publish_audio.proc, "quiet_run", fake_quiet_run)

    assert publish_audio.main(["2026-06-17", "--dry-run"]) == 0
    assert calls == []
    payload = latest_json.read_text(encoding="utf-8")
    assert "2026-06-17" in payload
    assert "audio-daily/2026-06-17.mp3" in payload
