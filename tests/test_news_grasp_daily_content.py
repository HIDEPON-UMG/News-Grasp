from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest


ISSUE_DATE = "2026-09-04"
RUN_ID = "direct-2026-09-04-1-test"


def _record(category: str) -> dict[str, object]:
    genre = {
        "fx": "FX",
        "ai": "AI",
        "it": "IT-Consulting",
        "mobility": "Mobility",
        "game": "Game",
    }[category]
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
    genre = {
        "fx": "FX",
        "ai": "AI",
        "it": "IT-Consulting",
        "mobility": "Mobility",
        "game": "Game",
    }[category]
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


def test_five_categories_use_three_reporter_shards_and_five_total_model_calls(
    tmp_path: Path,
) -> None:
    from tools.news_grasp_daily_content import produce_current_issue

    categories = ("fx", "ai", "it", "mobility", "game")
    reporter_calls: list[tuple[str, ...]] = []

    def shard_runner(*, role: str, category: str | None = None, **context):
        if role == "reporter_shard":
            shard = tuple(context["categories"])
            reporter_calls.append(shard)
            return {
                "issue_date": ISSUE_DATE,
                "reporters": [
                    {
                        "category": item["category"],
                        "issue_date": ISSUE_DATE,
                        "records": [_record(item["category"])],
                        "digest_markdown": _digest(item["category"]),
                        "search_audit": item["search_audit"],
                    }
                    for item in context["items"]
                ],
            }
        if role == "reporter":
            assert category is not None
            reporter_calls.append((category,))
            return {
                "category": category,
                "issue_date": ISSUE_DATE,
                "records": [_record(category)],
                "digest_markdown": _digest(category),
                "search_audit": context["search_audit"],
            }
        if role == "editor":
            return {
                "issue_date": ISSUE_DATE,
                "inputs": {},
                "append_records": [_record(item) for item in categories],
                "summary_markdown": _summary(),
            }
        if role == "deepdive":
            return _deepdive()
        raise AssertionError(role)

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "articles.jsonl").write_text("", encoding="utf-8")
    result = produce_current_issue(
        repo_root=tmp_path,
        issue_date=ISSUE_DATE,
        run_id=RUN_ID,
        scheduled_categories=categories,
        candidate_provider=_candidate_provider,
        model_runner=shard_runner,
        derived_builder=lambda **_: {"ok": True, "status": "built", "artifacts": []},
    )

    assert sorted(reporter_calls) == sorted([("fx", "mobility"), ("ai", "game"), ("it",)])
    assert result["reporter_call_count"] == 3
    assert result["model_call_count"] == 5
    assert result["model_call_count_total"] == 5


def test_shard_failure_preserves_green_sibling_and_repairs_only_bad_category(
    tmp_path: Path,
) -> None:
    from tools import news_grasp_direct_runtime as runtime
    from tools.news_grasp_daily_content import DailyContentError, produce_current_issue

    categories = ("fx", "ai", "it", "mobility", "game")
    store = runtime.DirectRunStore(
        tmp_path / "state",
        test_only_allow_semantic_verifier=True,
    )
    run = runtime.start_run(
        store,
        cwd=tmp_path,
        issue_date=ISSUE_DATE,
        run_intent=runtime.RUN_INTENT,
        manifest_id="f" * 64,
    )
    binding = {
        "runtime_store": store,
        "writer_lease": run["writer_lease"],
        "fencing_token": run["fencing_token"],
    }

    def derived(**context):
        paths = {
            "daily_audio_script": f"digest/Summary/{ISSUE_DATE}-audio-script.md",
            "daily_audio": f"build/tts/{ISSUE_DATE}.mp3",
            "daily_audio_projection": "build/tts/daily/latest_audio.json",
            "daily_video": f"build/youtube-podcast/{ISSUE_DATE}.mp4",
            "deepdive_html": f"docs/deepdive/{ISSUE_DATE}/index.html",
            "deepdive_audio": f"build/tts/deepdive/{ISSUE_DATE}.mp3",
            "deepdive_audio_projection": "build/tts/deepdive/latest_audio.json",
            "deepdive_video": f"build/youtube-podcast-deepdive/{ISSUE_DATE}.mp4",
            "site_html": "docs/index.html",
        }
        built: list[str] = []
        for artifact_id, relative in paths.items():
            if context["repair_actions"][artifact_id] == "reuse":
                continue
            target = tmp_path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(f"{artifact_id}:{ISSUE_DATE}".encode("utf-8"))
            built.append(str(target))
        return {"ok": True, "status": "built", "artifacts": built}

    def first_runner(*, role: str, category: str | None = None, **context):
        if role == "reporter_shard":
            return {
                "issue_date": ISSUE_DATE,
                "reporters": [
                    (
                        {"category": "game", "issue_date": ISSUE_DATE}
                        if item["category"] == "game"
                        else {
                            "category": item["category"],
                            "issue_date": ISSUE_DATE,
                            "records": [_record(item["category"])],
                            "digest_markdown": _digest(item["category"]),
                            "search_audit": item["search_audit"],
                        }
                    )
                    for item in context["items"]
                ],
            }
        if role == "reporter":
            assert category is not None
            return {
                "category": category,
                "issue_date": ISSUE_DATE,
                "records": [_record(category)],
                "digest_markdown": _digest(category),
                "search_audit": context["search_audit"],
            }
        raise AssertionError("editor must not run after reporter Red")

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "articles.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(DailyContentError, match="REPORTER_OUTPUT_INVALID:game"):
        produce_current_issue(
            repo_root=tmp_path,
            issue_date=ISSUE_DATE,
            run_id=run["run_id"],
            scheduled_categories=categories,
            candidate_provider=_candidate_provider,
            model_runner=first_runner,
            derived_builder=derived,
            **binding,
        )

    repair_calls: list[tuple[str, str | None]] = []

    def repair_runner(*, role: str, category: str | None = None, **context):
        repair_calls.append((role, category))
        if role == "reporter":
            assert category == "game"
            return {
                "category": category,
                "issue_date": ISSUE_DATE,
                "records": [_record(category)],
                "digest_markdown": _digest(category),
                "search_audit": context["search_audit"],
            }
        if role == "editor":
            return {
                "issue_date": ISSUE_DATE,
                "inputs": {},
                "append_records": [_record(item) for item in categories],
                "summary_markdown": _summary(),
            }
        if role == "deepdive":
            return _deepdive()
        raise AssertionError(role)

    result = produce_current_issue(
        repo_root=tmp_path,
        issue_date=ISSUE_DATE,
        run_id=run["run_id"],
        scheduled_categories=categories,
        candidate_provider=lambda *_: pytest.fail("candidate collection repeated"),
        model_runner=repair_runner,
        derived_builder=derived,
        **binding,
    )

    assert repair_calls == [("reporter", "game"), ("editor", None), ("deepdive", None)]
    assert result["reused_model_artifacts"] == [
        "reporter:fx",
        "reporter:ai",
        "reporter:it",
        "reporter:mobility",
    ]
    assert result["repaired_model_artifacts"] == ["reporter:game"]


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


def test_reporter_failure_reuses_candidates_and_green_reporter_on_repair(
    tmp_path: Path,
) -> None:
    from tools.news_grasp_daily_content import DailyContentError, produce_current_issue

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "articles.jsonl").write_text("", encoding="utf-8")
    first_calls: list[tuple[str, str | None]] = []

    def first_runner(**kwargs):
        first_calls.append((kwargs["role"], kwargs.get("category")))
        if kwargs["role"] == "reporter" and kwargs.get("category") == "ai":
            return {"ok": True}
        return _model_runner(**kwargs)

    with pytest.raises(DailyContentError, match="REPORTER_OUTPUT_INVALID:ai"):
        produce_current_issue(
            repo_root=tmp_path,
            issue_date=ISSUE_DATE,
            run_id=RUN_ID,
            scheduled_categories=("fx", "ai"),
            candidate_provider=_candidate_provider,
            model_runner=first_runner,
            derived_builder=lambda **_: {"ok": True, "artifacts": []},
        )

    repair_calls: list[tuple[str, str | None]] = []

    def repair_runner(**kwargs):
        repair_calls.append((kwargs["role"], kwargs.get("category")))
        return _model_runner(**kwargs)

    result = produce_current_issue(
        repo_root=tmp_path,
        issue_date=ISSUE_DATE,
        run_id=RUN_ID,
        scheduled_categories=("fx", "ai"),
        candidate_provider=lambda *_: pytest.fail("candidate collection repeated"),
        model_runner=repair_runner,
        derived_builder=lambda **_: {"ok": True, "status": "built", "artifacts": []},
    )

    assert sorted(first_calls) == [("reporter", "ai"), ("reporter", "fx")]
    assert repair_calls == [
        ("reporter", "ai"),
        ("editor", None),
        ("deepdive", None),
    ]
    assert result["model_call_count"] == 3
    assert result["reused_model_artifacts"] == ["reporter:fx"]
    assert result["repaired_model_artifacts"] == ["reporter:ai"]


def test_editor_failure_reuses_all_reporters_on_repair(tmp_path: Path) -> None:
    from tools.news_grasp_daily_content import DailyContentError, produce_current_issue

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "articles.jsonl").write_text("", encoding="utf-8")

    def first_runner(**kwargs):
        if kwargs["role"] == "editor":
            return {"ok": True}
        return _model_runner(**kwargs)

    with pytest.raises(DailyContentError, match="EDITOR_OUTPUT_INVALID"):
        produce_current_issue(
            repo_root=tmp_path,
            issue_date=ISSUE_DATE,
            run_id=RUN_ID,
            scheduled_categories=("fx", "ai"),
            candidate_provider=_candidate_provider,
            model_runner=first_runner,
            derived_builder=lambda **_: {"ok": True, "artifacts": []},
        )

    repair_calls: list[tuple[str, str | None]] = []

    def repair_runner(**kwargs):
        repair_calls.append((kwargs["role"], kwargs.get("category")))
        assert kwargs["role"] != "reporter"
        return _model_runner(**kwargs)

    result = produce_current_issue(
        repo_root=tmp_path,
        issue_date=ISSUE_DATE,
        run_id=RUN_ID,
        scheduled_categories=("fx", "ai"),
        candidate_provider=lambda *_: pytest.fail("candidate collection repeated"),
        model_runner=repair_runner,
        derived_builder=lambda **_: {"ok": True, "status": "built", "artifacts": []},
    )

    assert repair_calls == [("editor", None), ("deepdive", None)]
    assert result["model_call_count"] == 2
    assert result["reused_model_artifacts"] == ["reporter:fx", "reporter:ai"]
    assert result["repaired_model_artifacts"] == ["editor"]


def test_runtime_ledger_repairs_only_drifted_artifact_without_model_recall(
    tmp_path: Path,
) -> None:
    from tools import news_grasp_direct_runtime as runtime
    from tools import news_grasp_daily_content as content

    produce_current_issue = content.produce_current_issue

    history_row = {
        **_record("fx"),
        "date": "2026-09-03",
        "published_date": "2026-09-03",
        "url": "https://example.com/fx/history",
    }
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "articles.jsonl").write_text(
        json.dumps(history_row, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    assert content._run_local_git(tmp_path, "init", "-q").returncode == 0
    assert content._run_local_git(tmp_path, "add", "--", "data/articles.jsonl").returncode == 0
    assert content._run_local_git(
        tmp_path,
        "-c",
        "user.name=News-Grasp fixture",
        "-c",
        "user.email=fixture@example.test",
        "commit",
        "-q",
        "--no-gpg-sign",
        "-m",
        "baseline",
    ).returncode == 0
    baseline_sha = str(
        content._run_local_git(tmp_path, "rev-parse", "HEAD", text=True).stdout
    ).strip()
    store = runtime.DirectRunStore(
        tmp_path / "state",
        test_only_allow_semantic_verifier=True,
    )
    run = runtime.start_run(
        store,
        cwd=tmp_path,
        issue_date=ISSUE_DATE,
        run_intent=runtime.RUN_INTENT,
        manifest_id="f" * 64,
        source_baseline=baseline_sha,
        remote_base_sha=baseline_sha,
    )
    binding = {
        "runtime_store": store,
        "writer_lease": run["writer_lease"],
        "fencing_token": run["fencing_token"],
    }

    def derived(**context):
        paths = {
            "daily_audio_script": f"digest/Summary/{ISSUE_DATE}-audio-script.md",
            "daily_audio": f"build/tts/{ISSUE_DATE}.mp3",
            "daily_audio_projection": "build/tts/daily/latest_audio.json",
            "daily_video": f"build/youtube-podcast/{ISSUE_DATE}.mp4",
            "deepdive_html": f"docs/deepdive/{ISSUE_DATE}/index.html",
            "deepdive_audio": f"build/tts/deepdive/{ISSUE_DATE}.mp3",
            "deepdive_audio_projection": "build/tts/deepdive/latest_audio.json",
            "deepdive_video": f"build/youtube-podcast-deepdive/{ISSUE_DATE}.mp4",
            "site_html": "docs/index.html",
        }
        built: list[str] = []
        for artifact_id, relative in paths.items():
            if context["repair_actions"][artifact_id] == "reuse":
                continue
            target = tmp_path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(f"{artifact_id}:{ISSUE_DATE}".encode("utf-8"))
            built.append(str(target))
        return {"ok": True, "status": "built", "artifacts": built}

    first = produce_current_issue(
        repo_root=tmp_path,
        issue_date=ISSUE_DATE,
        run_id=run["run_id"],
        scheduled_categories=("fx", "ai"),
        candidate_provider=_candidate_provider,
        model_runner=_model_runner,
        derived_builder=derived,
        **binding,
    )
    assert first["model_call_count"] == 4
    articles = tmp_path / "data" / "articles.jsonl"
    first_rows = [json.loads(line) for line in articles.read_text(encoding="utf-8").splitlines()]
    assert first_rows[0] == history_row
    assert {row["url"] for row in first_rows if row["date"] == ISSUE_DATE} == {
        _record("fx")["url"],
        _record("ai")["url"],
    }
    summary = tmp_path / "digest" / "Summary" / f"{ISSUE_DATE}.md"
    summary.write_text("drifted", encoding="utf-8")

    second = produce_current_issue(
        repo_root=tmp_path,
        issue_date=ISSUE_DATE,
        run_id=run["run_id"],
        scheduled_categories=("fx", "ai"),
        candidate_provider=lambda *_: pytest.fail("candidate provider repeated"),
        model_runner=lambda **_: pytest.fail("model repeated"),
        derived_builder=derived,
        **binding,
    )

    assert second["ok"] is True
    assert second["model_call_count"] == 0
    assert summary.read_text(encoding="utf-8") == _summary()

    articles.write_bytes(b'{"invalid":\n')
    invalid_repair = produce_current_issue(
        repo_root=tmp_path,
        issue_date=ISSUE_DATE,
        run_id=run["run_id"],
        scheduled_categories=("fx", "ai"),
        candidate_provider=lambda *_: pytest.fail("candidate provider repeated"),
        model_runner=lambda **_: pytest.fail("model repeated"),
        derived_builder=derived,
        **binding,
    )
    invalid_rows = [json.loads(line) for line in articles.read_text(encoding="utf-8").splitlines()]
    assert invalid_repair["model_call_count"] == 0
    assert invalid_rows[0] == history_row
    assert len([row for row in invalid_rows if row["date"] == ISSUE_DATE]) == 2

    stale_row = {**_record("fx"), "url": "https://example.com/stale-current-row"}
    articles.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in (history_row, stale_row)) + "\n",
        encoding="utf-8",
    )
    stale_repair = produce_current_issue(
        repo_root=tmp_path,
        issue_date=ISSUE_DATE,
        run_id=run["run_id"],
        scheduled_categories=("fx", "ai"),
        candidate_provider=lambda *_: pytest.fail("candidate provider repeated"),
        model_runner=lambda **_: pytest.fail("model repeated"),
        derived_builder=derived,
        **binding,
    )
    repaired_rows = [json.loads(line) for line in articles.read_text(encoding="utf-8").splitlines()]
    assert stale_repair["model_call_count"] == 0
    assert repaired_rows[0] == history_row
    assert {row["url"] for row in repaired_rows if row["date"] == ISSUE_DATE} == {
        _record("fx")["url"],
        _record("ai")["url"],
    }


def test_articles_baseline_rejects_missing_blob_and_tampered_start_seal(
    tmp_path: Path,
) -> None:
    from tools import news_grasp_daily_content as content
    from tools import news_grasp_direct_runtime as runtime

    repo = tmp_path / "repo"
    repo.mkdir()
    assert content._run_local_git(repo, "init", "-q").returncode == 0
    assert content._run_local_git(
        repo,
        "-c",
        "user.name=News-Grasp fixture",
        "-c",
        "user.email=fixture@example.test",
        "commit",
        "-q",
        "--no-gpg-sign",
        "--allow-empty",
        "-m",
        "empty baseline",
    ).returncode == 0
    baseline_sha = str(content._run_local_git(repo, "rev-parse", "HEAD", text=True).stdout).strip()
    store = runtime.DirectRunStore(
        tmp_path / "state",
        test_only_allow_semantic_verifier=True,
    )
    run = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=runtime.RUN_INTENT,
        manifest_id="f" * 64,
        source_baseline=baseline_sha,
        remote_base_sha=baseline_sha,
    )
    ledger = runtime.DailyArtifactLedger(
        store,
        run_id=run["run_id"],
        issue_date=ISSUE_DATE,
        writer_lease=run["writer_lease"],
        fencing_token=run["fencing_token"],
    )

    with pytest.raises(content.DailyContentError, match="ARTICLES_BASELINE_BLOB_MISSING"):
        content._read_articles_jsonl_baseline(repo, ledger)

    with store.connect() as conn:
        seal = json.loads(
            conn.execute(
                "SELECT start_seal_json FROM runs WHERE run_id=?",
                (run["run_id"],),
            ).fetchone()[0]
        )
        seal["sourceBaseline"] = "b" * 40
        conn.execute(
            "UPDATE runs SET start_seal_json=? WHERE run_id=?",
            (json.dumps(seal, ensure_ascii=False, sort_keys=True), run["run_id"]),
        )
        conn.commit()

    with pytest.raises(content.DailyContentError, match="ARTICLES_BASELINE_START_SEAL_INVALID"):
        content._read_articles_jsonl_baseline(repo, ledger)


def test_runtime_ledger_refuses_new_candidate_and_model_work_after_75_minutes(
    tmp_path: Path,
) -> None:
    from tools import news_grasp_direct_runtime as runtime
    from tools.news_grasp_daily_content import DailyContentError, produce_current_issue

    class FakeClock:
        def __init__(self) -> None:
            self.value = datetime.fromisoformat("2026-09-04T06:00:00+09:00")

        def __call__(self) -> datetime:
            return self.value

    clock = FakeClock()
    store = runtime.DirectRunStore(
        tmp_path / "state",
        clock=clock,
        test_only_allow_semantic_verifier=True,
    )
    run = runtime.start_run(
        store,
        cwd=tmp_path,
        issue_date=ISSUE_DATE,
        run_intent=runtime.RUN_INTENT,
        manifest_id="f" * 64,
        scheduler_trigger_at="2026-09-04T06:00:00+09:00",
    )
    clock.value += timedelta(minutes=76)
    candidate_calls: list[str] = []

    with pytest.raises(DailyContentError, match="SLO_CANDIDATE_COLLECTION_FROZEN"):
        produce_current_issue(
            repo_root=tmp_path,
            issue_date=ISSUE_DATE,
            run_id=run["run_id"],
            scheduled_categories=("fx",),
            candidate_provider=lambda category, _issue: candidate_calls.append(category),
            model_runner=lambda **_: pytest.fail("model started after SLO freeze"),
            derived_builder=lambda **_: pytest.fail("derived build started without inputs"),
            runtime_store=store,
            writer_lease=run["writer_lease"],
            fencing_token=run["fencing_token"],
        )

    assert candidate_calls == []


def test_repair_scope_rejects_changes_outside_allowed_json_pointer() -> None:
    from tools.news_grasp_daily_content import DailyContentError, _assert_repair_scope

    previous = {
        "category": "fx",
        "issue_date": ISSUE_DATE,
        "records": [{"title": "keep", "thumb": "bad"}],
        "digest_markdown": "keep digest",
    }
    failure = {
        "invalidPayload": previous,
        "allowedMutationPaths": ["/records/0/thumb"],
    }
    scoped = json.loads(json.dumps(previous))
    scoped["records"][0]["thumb"] = "https://example.com/image.jpg"
    _assert_repair_scope(failure, scoped)

    unscoped = json.loads(json.dumps(scoped))
    unscoped["digest_markdown"] = "rewritten digest"
    with pytest.raises(DailyContentError, match="REPAIR_UNSCOPED_MUTATION"):
        _assert_repair_scope(failure, unscoped)


@pytest.mark.parametrize(
    "failure",
    [
        {"invalidPayload": {"summary_markdown": "old"}},
        {"invalidPayload": {"summary_markdown": "old"}, "allowedMutationPaths": []},
        {"invalidPayload": {"summary_markdown": "old"}, "allowedMutationPaths": ["summary_markdown"]},
        {"invalidPayload": {"summary_markdown": "old"}, "allowedMutationPaths": ["/missing/leaf"]},
    ],
)
def test_repair_scope_fails_closed_when_exact_mask_is_unresolved(failure: dict) -> None:
    from tools.news_grasp_daily_content import DailyContentError, _assert_repair_scope

    with pytest.raises(DailyContentError, match="REPAIR_MUTATION_SCOPE_UNRESOLVED"):
        _assert_repair_scope(failure, {"summary_markdown": "new"})


def test_repair_scope_allows_only_an_explicit_missing_leaf() -> None:
    from tools.news_grasp_daily_content import _assert_repair_scope

    previous = {"records": [{"title": "keep"}], "digest_markdown": "keep"}
    repaired = {"records": [{"title": "keep", "thumb": "https://example.test/a.jpg"}], "digest_markdown": "keep"}
    _assert_repair_scope(
        {
            "invalidPayload": previous,
            "allowedMutationPaths": ["/records/0/thumb"],
        },
        repaired,
    )


def test_high_cost_derived_stage_is_frozen_before_tts_call(tmp_path: Path, monkeypatch) -> None:
    from tools.news_grasp_daily_content import DailyContentError, _default_derived_builder
    from tools.tts import synthesize_daily

    monkeypatch.setattr(
        synthesize_daily,
        "synthesize",
        lambda *_args, **_kwargs: pytest.fail("TTS started after SLO freeze"),
    )
    actions = {
        artifact_id: "reuse"
        for artifact_id in (
            "daily_audio_script",
            "daily_audio_projection",
            "daily_video",
            "deepdive_html",
            "deepdive_audio",
            "deepdive_audio_projection",
            "deepdive_video",
            "site_html",
        )
    }
    actions["daily_audio"] = "rebuild_deterministic"

    with pytest.raises(DailyContentError, match="SLO_HIGH_COST_GENERATION_FROZEN:daily_audio"):
        _default_derived_builder(
            repo_root=tmp_path,
            issue_date=ISSUE_DATE,
            run_id=RUN_ID,
            repair_actions=actions,
            high_cost_admission=lambda: False,
        )
