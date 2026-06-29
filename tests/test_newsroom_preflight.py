#!/usr/bin/env python3
"""E2E 前 no-Codex preflight の契約テスト。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.newsroom_preflight as newsroom_preflight


ROOT = Path(__file__).resolve().parent.parent
ISSUE_DATE = "2026-06-23"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _minimal_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    reporter_schema = {
        "type": "object",
        "required": [
            "category",
            "issue_date",
            "records_file",
            "digest_file",
            "search_audit",
            "selected_count",
            "titles",
        ],
        "properties": {
            "category": {"type": "string"},
            "issue_date": {"type": "string"},
            "records_file": {"type": "string"},
            "digest_file": {"type": "string"},
            "search_audit": {"type": "string"},
            "selected_count": {"type": "integer"},
            "titles": {"type": "array"},
        },
    }
    editor_schema = {
        "type": "object",
        "required": ["inputs"],
        "properties": {
            "inputs": {
                "type": "object",
                "properties": {
                    "source_policy": {"type": "string", "enum": ["no_recollection"]},
                },
            },
        },
    }
    _write_json(repo / "schemas/reporter_fanout_return.schema.json", reporter_schema)
    _write_json(repo / "schemas/reporter_records.schema.json", {"type": "object"})
    _write_json(repo / "schemas/editor_summary.schema.json", editor_schema)
    (repo / "prompts").mkdir(parents=True, exist_ok=True)
    (repo / "prompts/newsroom-reporter-system.md").write_text(
        "schemas/reporter_fanout_return.schema.json\nコンパクト JSON\nフル record・記事本文・digest md 本文",
        encoding="utf-8",
    )
    (repo / "prompts/newsroom-editor-system.md").write_text(
        "editor-input-manifest\nsource_policy\nno_recollection\nschemas/editor_summary.schema.json",
        encoding="utf-8",
    )
    (repo / "tools").mkdir(parents=True, exist_ok=True)
    (repo / "tools/verify_reporter_output.py").write_text("# fixture\n", encoding="utf-8")
    (repo / "tools/fetch_article_body.py").write_text("# fixture\n", encoding="utf-8")
    (repo / "docs").mkdir(parents=True, exist_ok=True)
    (repo / "docs/sw.js").write_text("const SW_VERSION = 'ng-2026-06-23-001';\n", encoding="utf-8")
    _write_json(
        repo / "docs/publish-status.json",
        {"date": ISSUE_DATE, "result": "published_ok"},
    )
    return repo


def _run_preflight(repo_root: Path) -> list[str]:
    try:
        return newsroom_preflight.run(repo_root, issue_date=ISSUE_DATE)
    except TypeError as exc:  # pragma: no cover - Red test diagnostic before implementation.
        raise AssertionError("newsroom_preflight.run must accept issue_date") from exc


def test_newsroom_preflight_passes_current_contracts() -> None:
    assert _run_preflight(ROOT) == []


def test_newsroom_preflight_rejects_category_source_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _minimal_repo(tmp_path)
    monkeypatch.setattr(
        newsroom_preflight,
        "CATEGORIES",
        {"fx": {}, "ai": {}, "summary": {}},
        raising=False,
    )
    monkeypatch.setattr(newsroom_preflight, "CATEGORY_ORDER", ("fx",), raising=False)
    monkeypatch.setattr(
        newsroom_preflight,
        "CATEGORY_PATHS",
        {"fx": {"digest_folder": "FX", "docs_segment": "fx"}},
        raising=False,
    )
    monkeypatch.setattr(newsroom_preflight, "refill_category_ids", lambda: ["fx"], raising=False)

    errors = _run_preflight(repo)

    assert any("category source drift" in error for error in errors)


def test_newsroom_preflight_rejects_missing_model_policy_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _minimal_repo(tmp_path)
    monkeypatch.setattr(
        newsroom_preflight,
        "DEFAULT_MODEL_POLICY",
        {
            "reporter": {},
            "editor": {"default": "gpt-5.4-mini"},
            "newsroom_editor": {"default": "gpt-5.4-mini"},
            "deepdive": {"default": "gpt-5.5"},
        },
        raising=False,
    )

    errors = _run_preflight(repo)

    assert any("model policy missing required key: reporter.default" in error for error in errors)


def test_newsroom_preflight_rejects_missing_sw_version(tmp_path: Path) -> None:
    repo = _minimal_repo(tmp_path)
    (repo / "docs/sw.js").write_text("// missing version\n", encoding="utf-8")

    errors = _run_preflight(repo)

    assert any("docs/sw.js missing SW_VERSION" in error for error in errors)


def test_newsroom_preflight_rejects_invalid_publish_status(tmp_path: Path) -> None:
    repo = _minimal_repo(tmp_path)
    _write_json(repo / "docs/publish-status.json", {"date": ISSUE_DATE, "result": "unknown"})

    errors = _run_preflight(repo)

    assert any("publish-status invalid result" in error for error in errors)


def test_newsroom_preflight_rejects_fallback_publish_status(tmp_path: Path) -> None:
    repo = _minimal_repo(tmp_path)
    _write_json(
        repo / "docs" / "publish-status.json",
        {"date": ISSUE_DATE, "result": "published_fallback_with_notice"},
    )

    errors = _run_preflight(repo)

    assert any("publish-status invalid result" in error for error in errors)


def test_newsroom_preflight_rejects_distribution_inventory_missing_sentinel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _minimal_repo(tmp_path)
    monkeypatch.setattr(
        newsroom_preflight,
        "required_distribution_artifacts",
        lambda issue: ["build/tts/latest_audio.json"],
        raising=False,
    )

    errors = _run_preflight(repo)

    assert any("distribution inventory missing required sentinel" in error for error in errors)
