from __future__ import annotations

import hashlib
import json

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
            f"audio-deepdive/2026-06-21-{digest}.mp3?v={digest}"
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
    digest = hashlib.sha256(b"2026-06-20").hexdigest()[:12]
    assert f"/audio-deepdive/2026-06-20-{digest}.mp3?v={digest}" in visible["deepdive_audio_url"]


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


def test_deepdive_audio_dry_run_writes_latest_audio_without_gh_upload(tmp_path, monkeypatch):
    """NoPublish E2E 用 dry-run は gh release upload を呼ばず latest_audio を作る。"""
    mp3 = tmp_path / "2026-06-21.mp3"
    mp3.write_bytes(b"deepdive-dry-run-audio")
    latest_json = tmp_path / "latest_audio.json"
    calls: list[list[str]] = []

    def fake_quiet_run(args, **kwargs):
        calls.append(list(args))
        raise AssertionError("dry-run must not call gh")

    monkeypatch.setattr(deepdive_audio, "BUILD_DIR", tmp_path)
    monkeypatch.setattr(deepdive_audio, "LATEST_JSON", latest_json)
    monkeypatch.setattr(deepdive_audio.proc, "quiet_run", fake_quiet_run)

    assert deepdive_audio.main(["2026-06-21", "--dry-run"]) == 0
    assert calls == []
    payload = latest_json.read_text(encoding="utf-8")
    assert "2026-06-21" in payload
    digest = hashlib.sha256(b"deepdive-dry-run-audio").hexdigest()[:12]
    assert f"audio-deepdive/2026-06-21-{digest}.mp3?v={digest}" in payload


def test_deepdive_audio_classifies_github_503_as_typed_external():
    payload = deepdive_audio.classify_publish_failure(
        RuntimeError("HTTP 503: Service Unavailable"),
        observed_at="2026-07-24T06:12:00+09:00",
    )

    assert payload == {
        "ok": False,
        "status": "blocked_external_readiness",
        "gate_id": "github-release-upload",
        "issue_code": "github_release_upload_transient",
        "external_kind": "service_unavailable",
        "external_system": "github-release",
        "observed_error_code": "503",
        "source_command": "gh release upload audio-deepdive",
        "detail": "HTTP 503: Service Unavailable",
        "observed_at": "2026-07-24T06:12:00+09:00",
    }


def test_deepdive_audio_main_returns_external_exit_code_and_json(tmp_path, monkeypatch, capsys):
    mp3 = tmp_path / "2026-07-24.mp3"
    mp3.write_bytes(b"deepdive-audio")

    def fail_upload(args, **kwargs):
        if args[:4] == ["gh", "release", "view", "audio-deepdive"]:
            return type("Result", (), {"returncode": 0})()
        raise RuntimeError("HTTP 502: Bad Gateway")

    monkeypatch.setattr(deepdive_audio.proc, "quiet_run", fail_upload)

    rc = deepdive_audio.main(["2026-07-24", "--mp3", str(mp3), "--json"])

    assert rc == 71
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "blocked_external_readiness"
    assert payload["issue_code"] == "github_release_upload_transient"
    assert payload["external_system"] == "github-release"
