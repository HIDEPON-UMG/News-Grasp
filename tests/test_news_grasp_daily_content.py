from __future__ import annotations

import json
from pathlib import Path

import pytest


ISSUE_DATE = "2026-09-04"
RUN_ID = "direct-2026-09-04-1-test"


def _record(category: str) -> dict[str, object]:
    genre = {"fx": "FX", "ai": "AI"}[category]
    return {
        "date": ISSUE_DATE,
        "genre": genre,
        "title": f"{category} primary title",
        "title_ja": f"{category}の重要ニュース",
        "url": f"https://example.com/{category}/article",
        "source": "Example News",
        "thumb": f"https://example.com/{category}/image.jpg",
        "summary": "事実、背景、実務への影響を一次情報に沿って整理した。",
        "bullets": ["確認済みの事実", "背景と制約", "次に観測する点"],
        "published_date": ISSUE_DATE,
        "date_evidence_source": "canonical-page-date",
        "date_evidence_observed": ISSUE_DATE,
        "seen_at": f"{ISSUE_DATE}T06:00:00+09:00",
        "quality_shortfall_reason": "一次情報を確認できた記事を一件に限定した。",
    }


def _digest(category: str) -> str:
    genre = {"fx": "FX", "ai": "AI"}[category]
    return (
        "---\n"
        f"title: 'News Grasp #{ISSUE_DATE.replace('-', '')} — {genre}'\n"
        f"date: {ISSUE_DATE}\n"
        f"issue: {ISSUE_DATE.replace('-', '')}\n"
        f"category: {genre}\n"
        f"categoryId: {category}\n"
        "---\n\n"
        f"### [90] {category}の重要ニュース\n\n"
        f"![thumb](https://example.com/{category}/image.jpg)\n\n"
        "- 【事実・概要】：確認済みの事実を整理する。\n"
        "- 【背景・要点】：背景と制約を整理する。\n"
        "- 【影響・展望】：次の観測点を示す。\n"
    )


def _summary() -> str:
    lead = "複数カテゴリの一次情報を横断し、意思決定に必要な事実、背景、影響を分けて読む。" * 7
    return (
        "---\n"
        f"title: 'News Grasp #{ISSUE_DATE.replace('-', '')} — 日米が円買い協調介入、ドル円は一時155円台前半へ'\n"
        f"date: {ISSUE_DATE}\n"
        f"issue: {ISSUE_DATE.replace('-', '')}\n"
        "category: Summary\n"
        "categoryId: summary\n"
        "hero_headline: '日米が円買い協調介入、ドル円は一時155円台前半へ'\n"
        "theme: '一次情報から実務への影響を読む。'\n"
        "categories: [fx, ai]\n"
        "sections: [fx, ai]\n"
        "tags: [daily, summary]\n"
        "---\n\n"
        f"## § 本日のテーマ考察\n\n> {lead}\n\n"
        "- 【事実・概要】：当日の一次情報を確認した。\n"
        "- 【背景・要点】：複数カテゴリの制約を横断した。\n"
        "- 【影響・展望】：実務上の観測点を整理した。\n\n"
        "## FX\n\n"
        "- 【事実・概要】：為替の事実を確認した。\n"
        "- 【背景・要点】：政策背景を整理した。\n"
        "- 【影響・展望】：企業影響を確認する。\n\n"
        "## AI\n\n"
        "- 【事実・概要】：AIの事実を確認した。\n"
        "- 【背景・要点】：実装背景を整理した。\n"
        "- 【影響・展望】：導入条件を確認する。\n"
    )


def _deepdive() -> dict[str, str]:
    return {
        "article_markdown": (
            "---\n"
            "title: '制度と実装の境界を読む'\n"
            f"date: '{ISSUE_DATE}'\n"
            f"issue: '{ISSUE_DATE.replace('-', '')}'\n"
            "kind: deepdive\n"
            "lens: ai\n"
            "theme: '実装責任と制度制約をどう両立するか'\n"
            "og_image: 'https://example.com/ai/image.jpg'\n"
            "tags: [deepdive, news-grasp]\n"
            "---\n\n"
            "## 背景\n\n[[制度]]と**実装**の間には__責任分界__がある。\n\n"
            "## 深掘り\n\n[[企業]]は**運用条件**を定め、__検証可能性__を残す必要がある。\n\n"
            "## 注目点\n\n[[利用者]]への**説明**と__継続観測__が重要になる。\n\n"
            "## 参考リンク\n\n- [一次情報](https://example.com/ai/article)\n"
        ),
        "dialogue_markdown": (
            "---\n"
            "title: 'DeepDive解説対談'\n"
            f"date: '{ISSUE_DATE}'\n"
            "type: deepdive-dialogue\n"
            "---\n\n## 台本\n\n若手: 何が論点ですか。\n\n先輩: 制度と実装の責任分界だ。\n"
        ),
    }


def _candidate_provider(category: str, _issue_date: str) -> tuple[list[dict], dict]:
    return ([{"title": f"{category} candidate", "url": f"https://example.com/{category}/article"}], {
        "date": ISSUE_DATE,
        "category_id": category,
        "queries": [category],
        "raw_results_total": 1,
        "candidates_total": 1,
        "selected_total": 1,
    })


def _model_runner(*, role: str, category: str | None = None, **context):
    if role == "reporter":
        assert category in {"fx", "ai"}
        return {
            "category": category,
            "issue_date": ISSUE_DATE,
            "records": [_record(category)],
            "digest_markdown": _digest(category),
            "search_audit": context["search_audit"],
        }
    if role == "editor":
        records = [_record("fx"), _record("ai")]
        return {
            "issue_date": ISSUE_DATE,
            "inputs": {
                "reporter_artifacts": [
                    f"tmp/newsroom/{ISSUE_DATE}/fx.records.jsonl",
                    f"tmp/newsroom/{ISSUE_DATE}/ai.records.jsonl",
                ],
                "dedup_file": f"build/daily-content/{RUN_ID}/dedup.json",
                "source_policy": "no_recollection",
            },
            "append_records": records,
            "summary_markdown": _summary(),
        }
    if role == "deepdive":
        return _deepdive()
    raise AssertionError(role)


def test_zero_artifact_generation_materializes_one_canonical_bundle(tmp_path: Path) -> None:
    from tools.news_grasp_daily_content import produce_current_issue

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "articles.jsonl").write_text("", encoding="utf-8")
    result = produce_current_issue(
        repo_root=tmp_path,
        issue_date=ISSUE_DATE,
        run_id=RUN_ID,
        scheduled_categories=("fx", "ai"),
        candidate_provider=_candidate_provider,
        model_runner=_model_runner,
        derived_builder=lambda **_: {"ok": True, "status": "built", "artifacts": []},
    )

    assert result["ok"] is True
    assert result["model_call_count"] == 4
    assert result["reporter_call_count"] == 2
    assert (tmp_path / "digest" / "FX" / f"{ISSUE_DATE}-FX.md").is_file()
    assert (tmp_path / "digest" / "AI" / f"{ISSUE_DATE}-AI.md").is_file()
    assert (tmp_path / "digest" / "Summary" / f"{ISSUE_DATE}.md").read_text(encoding="utf-8").startswith("---\n")
    assert (tmp_path / "digest" / "DeepDive" / f"{ISSUE_DATE}-DeepDive.md").is_file()
    rows = [json.loads(line) for line in (tmp_path / "data" / "articles.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {row["url"] for row in rows} == {
        "https://example.com/fx/article",
        "https://example.com/ai/article",
    }


def test_model_failure_before_materialize_does_not_change_canonical_artifacts(tmp_path: Path) -> None:
    from tools.news_grasp_daily_content import DailyContentError, produce_current_issue

    (tmp_path / "data").mkdir()
    articles = tmp_path / "data" / "articles.jsonl"
    articles.write_text('{"date":"2026-09-03"}\n', encoding="utf-8")
    before = articles.read_bytes()

    def broken_runner(**kwargs):
        if kwargs["role"] == "reporter" and kwargs.get("category") == "ai":
            return {"ok": True}
        return _model_runner(**kwargs)

    with pytest.raises(DailyContentError, match="REPORTER_OUTPUT_INVALID"):
        produce_current_issue(
            repo_root=tmp_path,
            issue_date=ISSUE_DATE,
            run_id=RUN_ID,
            scheduled_categories=("fx", "ai"),
            candidate_provider=_candidate_provider,
            model_runner=broken_runner,
            derived_builder=lambda **_: {"ok": True},
        )

    assert articles.read_bytes() == before
    assert not (tmp_path / "digest").exists()


def test_protected_release_is_rejected_before_candidate_or_model_call(tmp_path: Path) -> None:
    from tools.news_grasp_daily_content import DailyContentError, produce_current_issue

    calls: list[str] = []
    with pytest.raises(DailyContentError, match="PROTECTED_RELEASE_REEXECUTION_FORBIDDEN"):
        produce_current_issue(
            repo_root=tmp_path,
            issue_date="2026-09-02",
            run_id="direct-2026-09-02-real",
            scheduled_categories=("fx",),
            candidate_provider=lambda *_: calls.append("candidate"),
            model_runner=lambda **_: calls.append("model"),
            derived_builder=lambda **_: calls.append("derived"),
        )
    assert calls == []


def test_same_issue_and_run_bundle_is_reused_without_duplicate_model_call(tmp_path: Path) -> None:
    from tools.news_grasp_daily_content import produce_current_issue

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "articles.jsonl").write_text("", encoding="utf-8")
    first = produce_current_issue(
        repo_root=tmp_path,
        issue_date=ISSUE_DATE,
        run_id=RUN_ID,
        scheduled_categories=("fx", "ai"),
        candidate_provider=_candidate_provider,
        model_runner=_model_runner,
        derived_builder=lambda **_: {"ok": True, "status": "built", "artifacts": []},
    )
    second = produce_current_issue(
        repo_root=tmp_path,
        issue_date=ISSUE_DATE,
        run_id=RUN_ID,
        scheduled_categories=("fx", "ai"),
        candidate_provider=lambda *_: pytest.fail("candidate provider was called twice"),
        model_runner=lambda **_: pytest.fail("model was called twice"),
        derived_builder=lambda **_: pytest.fail("derived builder was called twice"),
    )
    assert first["bundle_id"] == second["bundle_id"]
    assert second["status"] == "reused"
    assert second["model_call_count"] == 0


def test_production_current_issue_gate_invokes_canonical_content_producer(monkeypatch, tmp_path: Path) -> None:
    from tools import news_grasp_daily_content as content
    from tools import news_grasp_daily_gate as gate
    from tools import news_grasp_direct_runtime as runtime

    calls: list[dict] = []

    def producer(**kwargs):
        calls.append(kwargs)
        raise content.DailyContentError("injected_stop_after_producer_entry")

    monkeypatch.setattr(content, "produce_current_issue", producer)
    store = runtime.DirectRunStore(tmp_path / "state", test_only_allow_semantic_verifier=False)
    result = gate._default_current_issue_integration(
        store=store,
        run_id=RUN_ID,
        run={"generation": 1},
        issue_date=ISSUE_DATE,
        run_intent=runtime.RUN_INTENT,
        cwd=tmp_path,
        route_capability={"capability": "scheduled_production_daily"},
    )

    assert len(calls) == 1
    assert calls[0]["run_id"] == RUN_ID
    assert result["ok"] is False
    assert "content_generation_red:DailyContentError" in result["failures"][0]


def test_derived_failure_retry_reuses_validated_model_bundle(tmp_path: Path) -> None:
    from tools.news_grasp_daily_content import DailyContentError, produce_current_issue

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "articles.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(DailyContentError, match="DERIVED_BUILD_FAILED"):
        produce_current_issue(
            repo_root=tmp_path,
            issue_date=ISSUE_DATE,
            run_id=RUN_ID,
            scheduled_categories=("fx", "ai"),
            candidate_provider=_candidate_provider,
            model_runner=_model_runner,
            derived_builder=lambda **_: {"ok": False},
        )

    result = produce_current_issue(
        repo_root=tmp_path,
        issue_date=ISSUE_DATE,
        run_id=RUN_ID,
        scheduled_categories=("fx", "ai"),
        candidate_provider=lambda *_: pytest.fail("candidate provider repeated after derived failure"),
        model_runner=lambda **_: pytest.fail("model repeated after derived failure"),
        derived_builder=lambda **_: {"ok": True, "status": "built", "artifacts": []},
    )
    assert result["ok"] is True
    assert result["model_call_count"] == 0
    assert result["model_call_count_total"] == 4
