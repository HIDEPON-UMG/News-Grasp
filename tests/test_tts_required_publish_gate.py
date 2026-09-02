from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from tools.validate_daily_quality import validate_tts_audio_presence


ISSUE = date(2026, 6, 17)
AUDIO_URL = (
    "https://github.com/HIDEPON-UMG/News-Grasp/releases/download/"
    "audio-daily/2026-06-17.mp3?v=testhash"
)


def _write_complete_fixture(root: Path, *, audio_url: str = AUDIO_URL) -> None:
    (root / "digest" / "Summary").mkdir(parents=True)
    (root / "digest" / "Summary" / "2026-06-17-audio-script.md").write_text(
        "---\ndate: 2026-06-17\n---\n\n音声原稿です。\n",
        encoding="utf-8",
    )
    (root / "build" / "tts").mkdir(parents=True)
    (root / "build" / "tts" / "latest_audio.json").write_text(
        json.dumps({"latest_audio_date": "2026-06-17", "latest_audio_url": audio_url}),
        encoding="utf-8",
    )
    (root / "docs" / "2026-06-17" / "summary").mkdir(parents=True)
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "index.html").write_text(
        f'<section class="daily-audio"><audio preload="none" controls src="{audio_url}"></audio></section>',
        encoding="utf-8",
    )
    (root / "docs" / "2026-06-17" / "summary" / "index.html").write_text(
        f'<section class="daily-audio"><audio preload="none" controls src="{audio_url}"></audio></section>',
        encoding="utf-8",
    )


def test_tts_audio_presence_requires_latest_audio_json(tmp_path: Path) -> None:
    """音声成果物が必須化された日は latest_audio sentinel 欠落で公開前に落とす。"""
    _write_complete_fixture(tmp_path)
    (tmp_path / "build" / "tts" / "latest_audio.json").unlink()

    errs = validate_tts_audio_presence(
        repo_root=tmp_path,
        digest_root=tmp_path / "digest",
        docs_root=tmp_path / "docs",
        issue=ISSUE,
    )

    assert any("latest_audio.json" in err for err in errs)


def test_tts_audio_presence_requires_home_and_summary_embed(tmp_path: Path) -> None:
    """Release URL があっても Home/Summary HTML へ同じ URL が未反映なら落とす。"""
    _write_complete_fixture(tmp_path)
    (tmp_path / "docs" / "2026-06-17" / "summary" / "index.html").write_text(
        '<section class="daily-audio"><audio preload="none" controls src="old.mp3"></audio></section>',
        encoding="utf-8",
    )

    errs = validate_tts_audio_presence(
        repo_root=tmp_path,
        digest_root=tmp_path / "digest",
        docs_root=tmp_path / "docs",
        issue=ISSUE,
    )

    assert any("summary" in err and "audio URL" in err for err in errs)


def test_tts_audio_presence_allows_complete_fixture(tmp_path: Path) -> None:
    """原稿、latest_audio、Home/Summary 埋め込みが揃った通常公開だけ通す。"""
    _write_complete_fixture(tmp_path)

    assert validate_tts_audio_presence(
        repo_root=tmp_path,
        digest_root=tmp_path / "digest",
        docs_root=tmp_path / "docs",
        issue=ISSUE,
    ) == []


def test_tts_audio_presence_prefers_canonical_v2_without_v1_writer(
    tmp_path: Path,
) -> None:
    """V2へ一本化した後はV1 stateを再生成せず日次gateを閉じる。"""

    _write_complete_fixture(tmp_path)
    (tmp_path / "build" / "tts" / "latest_audio.json").unlink()
    v2_path = tmp_path / "build" / "tts" / "daily" / "latest_audio.json"
    v2_path.parent.mkdir(parents=True)
    v2_audio_url = AUDIO_URL.split("?", 1)[0]
    v2_path.write_text(
        json.dumps(
            {
                "schemaVersion": "NEWS_GRASP_AUDIO_PROJECTION_V2",
                "audioType": "daily",
                "sourceArtifact": "digest/Summary/2026-06-17-audio-script.md",
                "runtimeState": "release-existing",
                "provider": {"name": "github-release", "jobIdentity": "existing"},
                "publicUrl": v2_audio_url,
                "publicPageHref": v2_audio_url,
                "issueDate": "2026-06-17",
                "runId": "direct-test",
                "runIntent": "scheduled_production_direct",
                "completionState": "verified",
                "adapterSourceSchema": "NEWS_GRASP_AUDIO_PROJECTION_V2",
                "ok": True,
            }
        ),
        encoding="utf-8",
    )

    assert validate_tts_audio_presence(
        repo_root=tmp_path,
        digest_root=tmp_path / "digest",
        docs_root=tmp_path / "docs",
        issue=ISSUE,
    ) == []


def test_tts_audio_presence_uses_date_summary_for_historical_audit(tmp_path: Path) -> None:
    """過去日監査は現在日のlatest/homeを過去成果物へ誤適用しない。"""
    _write_complete_fixture(tmp_path)
    current_url = AUDIO_URL.replace("2026-06-17", "2026-06-18")
    (tmp_path / "build" / "tts" / "latest_audio.json").write_text(
        json.dumps({"latest_audio_date": "2026-06-18", "latest_audio_url": current_url}),
        encoding="utf-8",
    )
    (tmp_path / "docs" / "index.html").write_text(
        f'<audio controls src="{current_url}"></audio>',
        encoding="utf-8",
    )

    assert validate_tts_audio_presence(
        repo_root=tmp_path,
        digest_root=tmp_path / "digest",
        docs_root=tmp_path / "docs",
        issue=ISSUE,
    ) == []
