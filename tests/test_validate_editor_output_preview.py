from __future__ import annotations

import json
from pathlib import Path

from tools.validate_editor_output_preview import validate_editor_output_preview


def _write_preview(path: Path, *, summary: str, url: str = "https://example.com/news") -> None:
    path.write_text(
        json.dumps(
            {
                "issue_date": "2026-07-11",
                "inputs": {
                    "reporter_artifacts": ["build/reporter-artifacts/2026-07-11/editor-input-manifest.json"],
                    "dedup_file": "build/deduped-candidates",
                    "source_policy": "no_recollection",
                },
                "append_records": [{
                    "date": "2026-07-11", "genre": "AI", "title": "Example", "title_ja": "例示記事",
                    "url": url, "source": "Example", "summary": "要約", "bullets": ["事実", "背景", "展望"],
                }],
                "summary_markdown": summary,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_rejects_aborted_editor_payload_before_materialization(tmp_path: Path) -> None:
    preview = tmp_path / "editor-output.preview.json"
    _write_preview(
        preview,
        summary="### ⛔ ブロック — 編集規約違反を検知したため生成前に中断",
        url="https://example.invalid/editorial-run-aborted",
    )

    errors = validate_editor_output_preview(preview, issue_date="2026-07-11")

    assert any("reflection" in error for error in errors)
    assert any(".invalid" in error for error in errors)


def test_accepts_semantically_valid_editor_payload(tmp_path: Path) -> None:
    preview = tmp_path / "editor-output.preview.json"
    lead = "本日は主要カテゴリを横断し、企業戦略と技術投資の接点を整理する。" * 8
    _write_preview(preview, summary=f"## § 本日のテーマ考察\n\n> {lead}\n")

    assert validate_editor_output_preview(preview, issue_date="2026-07-11") == []


def test_rejects_preview_that_drops_nonempty_reporter_category(tmp_path: Path) -> None:
    repo = tmp_path
    preview = repo / "build" / "reporter-artifacts" / "2026-07-11" / "editor-output.preview.json"
    manifest = preview.parent / "editor-input-manifest.json"
    records = repo / "tmp" / "newsroom" / "2026-07-11" / "manufacturing.records.jsonl"
    records.parent.mkdir(parents=True)
    records.write_text(
        json.dumps(
            {
                "date": "2026-07-11",
                "genre": "Manufacturing",
                "title": "TSMC plant resumes",
                "url": "https://example.com/manufacturing",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "date": "2026-07-11",
                "scheduled_categories": ["ai", "manufacturing"],
                "reporter_artifacts": [
                    "tmp/newsroom/2026-07-11/manufacturing.records.jsonl"
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    lead = "本日は主要カテゴリを横断し、企業戦略と技術投資の接点を整理する。" * 8
    _write_preview(preview, summary=f"## § 本日のテーマ考察\n\n> {lead}\n")

    errors = validate_editor_output_preview(preview, issue_date="2026-07-11")

    assert any("dropped nonempty reporter category: manufacturing" in error for error in errors)
