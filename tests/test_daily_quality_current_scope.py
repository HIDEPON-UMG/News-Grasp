from __future__ import annotations

from pathlib import Path

from tools import validate_daily_quality


def test_daily_quality_deepdive_gate_excludes_historical_corpus(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[dict[str, object]] = []

    for name in (
        "validate_summary_hero",
        "validate_summary_emphasis",
        "validate_summary_category_focus",
        "validate_card_emphasis_coverage",
        "validate_digest_style_quality",
        "validate_issue_schedule",
        "validate_digest_article_counts",
        "validate_search_audit_for_shortfall",
        "validate_issue_thumbnail_coverage",
        "validate_digest_article_thumbnail_coverage",
        "validate_digest_source_freshness",
        "validate_jsonl_source_freshness",
        "validate_published_docs_presence",
        "validate_deepdive_presence",
        "validate_deepdive_relations_layout",
        "validate_tts_audio_presence",
    ):
        monkeypatch.setattr(validate_daily_quality, name, lambda *args, **kwargs: [])

    def fake_audit_issue(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {"issueCodes": [], "issues": []}

    monkeypatch.setattr(
        validate_daily_quality.deepdive_quality, "audit_issue", fake_audit_issue
    )

    assert validate_daily_quality.validate_daily_quality(
        issue_date="2026-09-02",
        digest_root=tmp_path / "digest",
        jsonl_path=tmp_path / "data" / "articles.jsonl",
        require_deepdive=True,
    ) == []
    assert calls == [
        {
            "repo_root": (tmp_path / "digest").resolve().parent,
            "issue_date": "2026-09-02",
            "include_corpus": False,
            "require_rendered_public": True,
            "route": "daily_quality",
        }
    ]
