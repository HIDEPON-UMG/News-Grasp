from __future__ import annotations

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


def test_audio_url_uses_confirmed_owner_and_repo():
    assert publish_audio.audio_url("2026-06-16") == (
        "https://github.com/HIDEPON-UMG/News-Grasp/releases/download/audio-daily/2026-06-16.mp3"
    )
