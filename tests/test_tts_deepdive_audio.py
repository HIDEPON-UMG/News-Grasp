from __future__ import annotations

import hashlib

from tools.tts import deepdive_audio


def test_deepdive_audio_for_pages_uses_local_mp3_with_release_url(tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    mp3 = build_dir / "2026-06-21.mp3"
    mp3.write_bytes(b"deepdive-dialogue-audio")

    got = deepdive_audio.deepdive_audio_for_pages(
        "2026-06-21",
        latest_json=build_dir / "missing.json",
        build_dir=build_dir,
    )

    digest = hashlib.sha256(b"deepdive-dialogue-audio").hexdigest()[:12]
    assert got == {
        "deepdive_audio_date": "2026-06-21",
        "deepdive_audio_url": (
            "https://github.com/HIDEPON-UMG/News-Grasp/releases/download/"
            f"audio-deepdive/2026-06-21.mp3?v={digest}"
        ),
    }


def test_deepdive_audio_for_pages_hides_audio_for_archive_older_than_latest_two(tmp_path):
    digest_dir = tmp_path / "digest"
    build_dir = tmp_path / "build"
    digest_dir.mkdir()
    build_dir.mkdir()
    for day in ("2026-06-19", "2026-06-20", "2026-06-21"):
        (digest_dir / f"{day}-DeepDive.md").write_text("---\n", encoding="utf-8")
        (build_dir / f"{day}.mp3").write_bytes(day.encode("ascii"))

    hidden = deepdive_audio.deepdive_audio_for_pages(
        "2026-06-19",
        enforce_recent=True,
        digest_dir=digest_dir,
        latest_json=build_dir / "missing.json",
        build_dir=build_dir,
    )
    visible = deepdive_audio.deepdive_audio_for_pages(
        "2026-06-20",
        enforce_recent=True,
        digest_dir=digest_dir,
        latest_json=build_dir / "missing.json",
        build_dir=build_dir,
    )

    assert hidden == {"deepdive_audio_url": "", "deepdive_audio_date": ""}
    assert visible["deepdive_audio_date"] == "2026-06-20"
    assert "/audio-deepdive/2026-06-20.mp3" in visible["deepdive_audio_url"]


def test_latest_deepdive_dates_ignores_dialogue_scripts(tmp_path):
    digest_dir = tmp_path / "digest"
    digest_dir.mkdir()
    for name in (
        "2026-06-20-DeepDive.md",
        "2026-06-20-DeepDive-dialogue-sample.md",
        "2026-06-21-DeepDive.md",
        "2026-06-21-DeepDive-dialogue.md",
    ):
        (digest_dir / name).write_text("---\n", encoding="utf-8")

    assert deepdive_audio.latest_deepdive_dates(digest_dir, limit=2) == ["2026-06-20", "2026-06-21"]
