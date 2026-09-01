from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


DAILY_URL = "https://github.com/HIDEPON-UMG/News-Grasp/releases/download/audio-daily/2026-09-01.mp3"
DEEPDIVE_URL = "https://github.com/HIDEPON-UMG/News-Grasp/releases/download/audio-deepdive/2026-09-01.mp3"


def test_r05_daily_and_deepdive_normalize_to_one_v2_schema() -> None:
    """R05: V1はmemory上で読むだけにし、両音声を同一V2 schemaへ投影する。"""
    api = importlib.import_module("tools.news_grasp_audio_projection")
    daily = api.normalize_audio_projection(
        {"latest_audio_date": "2026-09-01", "latest_audio_url": DAILY_URL},
        audio_type="daily",
        run_id="direct-test",
        run_intent="scheduled_production_direct",
    )
    deepdive = api.normalize_audio_projection(
        {"deepdive_audio_date": "2026-09-01", "deepdive_audio_url": DEEPDIVE_URL},
        audio_type="deepdive",
        run_id="direct-test",
        run_intent="scheduled_production_direct",
    )
    assert daily["schemaVersion"] == deepdive["schemaVersion"] == "NEWS_GRASP_AUDIO_PROJECTION_V2"
    assert daily["audioType"] == "daily"
    assert deepdive["audioType"] == "deepdive"
    assert api.canonical_audio_path("daily").as_posix() == "build/tts/daily/latest_audio.json"
    assert api.canonical_audio_path("deepdive").as_posix() == "build/tts/deepdive/latest_audio.json"


def test_existing_v1_receipt_is_bound_without_v1_rewrite(tmp_path: Path) -> None:
    """既公開receiptを読み、元bytesを変えずcanonical V2だけを書く。"""

    api = importlib.import_module("tools.news_grasp_audio_projection")
    legacy = tmp_path / "legacy.json"
    legacy.write_text(
        json.dumps(
            {
                "latest_audio_date": "2026-09-01",
                "latest_audio_url": DAILY_URL,
            }
        ),
        encoding="utf-8",
    )
    before = legacy.read_bytes()

    result = api.bind_existing_audio_projection(
        repo_root=tmp_path,
        input_path=legacy,
        audio_type="daily",
        issue_date="2026-09-01",
        run_id="direct-2026-09-01",
        source_artifact="digest/Summary/2026-09-01-audio-script.md",
        runtime_state="verified-existing-release",
        public_page_href=DAILY_URL,
        provider_name="github-release",
        job_identity="audio-daily/2026-09-01",
        public_probe=lambda _url: {"ok": True, "contentType": "audio/mpeg", "size": 1024},
    )

    assert result["status"] == "verified"
    assert legacy.read_bytes() == before
    written = json.loads(
        (tmp_path / "build" / "tts" / "daily" / "latest_audio.json").read_text()
    )
    assert written["schemaVersion"] == "NEWS_GRASP_AUDIO_PROJECTION_V2"
    assert written["runId"] == "direct-2026-09-01"
    assert written["sourceReceiptSha256"]


def test_audio_binding_rejects_noncanonical_or_missing_public_asset(tmp_path: Path) -> None:
    """security Red: 架空URL・href不一致・public probe Redをverifiedへ昇格しない。"""
    api = importlib.import_module("tools.news_grasp_audio_projection")
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"latest_audio_date": "2026-09-01", "latest_audio_url": DAILY_URL}), encoding="utf-8")
    with pytest.raises(ValueError, match="audio_projection_invalid"):
        api.bind_existing_audio_projection(
            repo_root=tmp_path,
            input_path=legacy,
            audio_type="daily",
            issue_date="2026-09-01",
            run_id="direct-2026-09-01",
            source_artifact="digest/Summary/2026-09-01-audio-script.md",
            runtime_state="verified-existing-release",
            public_page_href="https://example.com/fake.mp3",
            provider_name="github-release",
            job_identity="audio-daily/2026-09-01",
            public_probe=lambda _url: {"ok": False},
        )


def test_v1_cache_buster_is_read_only_normalized_to_canonical_release_url(tmp_path: Path) -> None:
    """legacy V1の既知v queryはV2へ再生成せずmemory上でcanonical URLへ正規化する。"""
    api = importlib.import_module("tools.news_grasp_audio_projection")
    legacy = tmp_path / "legacy.json"
    legacy.write_text(
        json.dumps({"latest_audio_date": "2026-09-01", "latest_audio_url": DAILY_URL + "?v=abcdef012345"}),
        encoding="utf-8",
    )
    result = api.bind_existing_audio_projection(
        repo_root=tmp_path,
        input_path=legacy,
        audio_type="daily",
        issue_date="2026-09-01",
        run_id="expected-run",
        source_artifact="digest/Summary/2026-09-01-audio-script.md",
        runtime_state="verified-existing-release",
        public_page_href=DAILY_URL,
        provider_name="github-release",
        job_identity="audio-daily/2026-09-01",
        public_probe=lambda _url: {"ok": True},
    )
    assert result["projection"]["publicUrl"] == DAILY_URL
    assert result["projection"]["publicPageHref"] == DAILY_URL
    assert result["projection"]["ok"] is True


def test_audio_href_port_and_legacy_run_id_cannot_bypass_binding(tmp_path: Path) -> None:
    api = importlib.import_module("tools.news_grasp_audio_projection")
    projection = api.normalize_audio_projection({"latest_audio_date": "2026-09-01", "latest_audio_url": DAILY_URL}, audio_type="daily", run_id="expected")
    projection["publicPageHref"] = DAILY_URL.replace("github.com/", "github.com:444/")
    assert "audio_public_href_unbound" in api.validate_audio_projection(projection, expected_run_id="expected")["reasonCodes"]

    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"latest_audio_date": "2026-09-01", "latest_audio_url": DAILY_URL, "runId": "wrong-run"}), encoding="utf-8")
    with pytest.raises(ValueError, match="audio_run_id_mismatch"):
        api.bind_existing_audio_projection(repo_root=tmp_path, input_path=legacy, audio_type="daily", issue_date="2026-09-01", run_id="expected-run", source_artifact="digest/Summary/2026-09-01-audio-script.md", runtime_state="verified-existing-release", public_page_href=DAILY_URL, provider_name="github-release", job_identity="audio-daily/2026-09-01", public_probe=lambda _url: {"ok": True})
