from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.validate_editor_output_preview import (
    editor_preview_producer_contract,
    validate_editor_output_preview,
)


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


def test_producer_contract_pins_exact_markdown_lane_prefixes() -> None:
    contract = editor_preview_producer_contract()

    assert "EDITOR_PREVIEW_PRODUCER_CONTRACT_V1" in contract
    assert "- 【事実・概要】：" in contract
    assert "- 【背景・要点】：" in contract
    assert "- 【影響・展望】：" in contract
    assert "`+- 【事実・概要】：` は禁止" in contract


def test_rejects_diff_prefixed_lane_lines_with_actionable_error(tmp_path: Path) -> None:
    preview = tmp_path / "editor-output.preview.json"
    lead = "本日は主要カテゴリを横断し、企業戦略と技術投資の接点を整理する。" * 8
    summary = (
        f"## § 本日のテーマ考察\n\n> {lead}\n\n"
        "### §01 AI — 自律実行の境界を検証する\n\n"
        "[[AI運用]]の条件を**実証**し、__次の観測点__を整理する。\n"
        "+- 【事実・概要】：自律実行の事実を整理する。\n"
        "+- 【背景・要点】：権限境界の背景を整理する。\n"
        "+- 【影響・展望】：次の検証条件を整理する。\n"
    )
    _write_preview(preview, summary=summary)

    errors = validate_editor_output_preview(preview, issue_date="2026-07-11")

    assert any("invalid diff prefix '+-'" in error for error in errors)
    assert any("行頭を正確に `- 【事実・概要】：`" in error for error in errors)


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


def test_explicit_repo_root_validates_manifest_from_external_preview(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    artifact_dir = repo / "build/reporter-artifacts/2026-07-11"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "manufacturing.records.jsonl").write_text('{}\n', encoding="utf-8")
    (artifact_dir / "editor-input-manifest.json").write_text(json.dumps({
        "scheduled_categories": ["manufacturing"],
        "reporter_artifacts": ["build/reporter-artifacts/2026-07-11/manufacturing.records.jsonl"],
    }), encoding="utf-8")
    preview = tmp_path / "editor-preview.json"
    _write_preview(preview, summary="## § 本日のテーマ考察\n\n> " + "主要な企業の動向を整理する。" * 20)
    monkeypatch.chdir(tmp_path)
    errors = validate_editor_output_preview(preview, issue_date="2026-07-11", repo_root=repo)
    assert any("dropped nonempty reporter category: manufacturing" in error for error in errors)
    payload = json.loads(preview.read_text(encoding="utf-8"))
    payload["append_records"][0]["genre"] = "Manufacturing"
    preview.write_text(json.dumps(payload), encoding="utf-8")
    assert validate_editor_output_preview(preview, issue_date="2026-07-11", repo_root=repo) == []


def test_explicit_repo_root_does_not_hide_missing_manifest(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    preview = tmp_path / "editor-preview.json"
    _write_preview(preview, summary="## § 本日のテーマ考察\n\n> " + "主要な企業の動向を整理する。" * 20)
    (tmp_path / "editor-input-manifest.json").write_text('{}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    errors = validate_editor_output_preview(preview, issue_date="2026-07-11", repo_root=repo)
    assert any("reporter manifest missing" in error for error in errors)


@pytest.mark.parametrize("reference", ["../other/editor-input-manifest.json", "absolute", "reparse"])
def test_explicit_root_rejects_unsafe_manifest(tmp_path: Path, monkeypatch, reference: str) -> None:
    from tools import news_grasp_daily_content as content
    repo = tmp_path / "repo"
    repo.mkdir()
    other = tmp_path / "other/editor-input-manifest.json"
    other.parent.mkdir()
    other.write_text('{}', encoding="utf-8")
    preview = tmp_path / "editor-preview.json"
    _write_preview(preview, summary="## § 本日のテーマ考察\n\n> " + "主要な企業の動向を整理する。" * 20)
    if reference == "absolute":
        reference = str(other)
    elif reference == "reparse":
        reference = "editor-input-manifest.json"
        (repo / reference).write_text('{}', encoding="utf-8")
        monkeypatch.setattr(content, "_has_reparse_ancestor", lambda path: True)
    payload = json.loads(preview.read_text(encoding="utf-8"))
    payload["inputs"]["reporter_artifacts"] = [reference]
    preview.write_text(json.dumps(payload), encoding="utf-8")
    errors = validate_editor_output_preview(preview, issue_date="2026-07-11", repo_root=repo)
    assert any("reporter reference invalid" in error for error in errors)


@pytest.mark.parametrize("record_reference", ["../other/manufacturing.records.jsonl", "missing/manufacturing.records.jsonl"])
def test_explicit_root_rejects_unsafe_or_missing_records(tmp_path: Path, record_reference: str) -> None:
    repo = tmp_path / "repo"
    artifacts = repo / "build/reporter-artifacts/2026-07-11"
    artifacts.mkdir(parents=True)
    (artifacts / "editor-input-manifest.json").write_text(json.dumps({
        "scheduled_categories": ["manufacturing"], "reporter_artifacts": [record_reference],
    }), encoding="utf-8")
    preview = tmp_path / "editor-preview.json"
    _write_preview(preview, summary="## § 本日のテーマ考察\n\n> " + "主要な企業の動向を整理する。" * 20)
    errors = validate_editor_output_preview(preview, issue_date="2026-07-11", repo_root=repo)
    assert any("reporter reference invalid" in error for error in errors)
