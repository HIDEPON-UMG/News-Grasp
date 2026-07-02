from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.publish_inventory import CATEGORY_PATHS, scheduled_category_ids
from tools.validate_generation_quality import main, validate_generation_quality


ISSUE = "2026-06-16"


def _frontmatter(title: str, category: str, category_id: str) -> str:
    return (
        "---\n"
        f"title: {title}\n"
        f"date: {ISSUE}\n"
        f"category: {category}\n"
        f"categoryId: {category_id}\n"
        "---\n\n"
    )


def _complete_deepdive_body(*, chart_count: int = 2) -> str:
    charts = ""
    for idx in range(chart_count):
        chart_payload = {
            "type": "bar",
            "title": f"chart {idx + 1}",
            "series": [{"name": "s", "data": [1, 2, 3]}],
            "categories": ["a", "b", "c"],
            "source": "fixture",
        }
        charts += (
            "```chart\n"
            + json.dumps(chart_payload, ensure_ascii=False)
            + "\n"
            "```\n\n"
        )
    return (
        "## 背景\n\n"
        "```timeline\n[]\n```\n\n"
        "```players\n[]\n```\n\n"
        "```relations\n"
        '{"nodes":[{"id":"a","label":"A"},{"id":"b","label":"B"}],'
        '"edges":[{"from":"a","to":"b","label":"x","kind":"競合"}],"source":"fixture"}\n'
        "```\n\n"
        "本文です。\n\n"
        "## 深掘り\n\n"
        f"{charts}"
        "```table\n"
        '{"columns":["a","b"],"rows":[["1","2"]],"source":"fixture"}\n'
        "```\n\n"
        "本文です。\n\n"
        "## 注目点\n\n"
        "```decision\n"
        '{"issue":"x","options":["a"],"deadline":"d","decider":"w"}\n'
        "```\n"
    )


def _complete_audio_script(extra: str = "") -> str:
    return (
        "<!-- tts-outline\n"
        "中心論点: 需要の強さではなく、供給責任と運用条件を誰が引き受けるかを見る日。\n"
        "背景: 為替、AI、IT、モビリティ、製造、経済、ゲームの材料が同じ日に並んだ。\n"
        "なぜ今: 投資、制度、価格、供給網の変更が同じ日に出て、実装順序の判断が必要になった。\n"
        "因果関係: 発表内容から、現場の制約、利用者への影響、明日以降の観測点へつなぐ。\n"
        "カテゴリ論点:\n"
        "- fx: 当日の事実から、制約、影響、次の観測点まで踏み込む。\n"
        "- ai: 当日の事実から、制約、影響、次の観測点まで踏み込む。\n"
        "- it: 当日の事実から、制約、影響、次の観測点まで踏み込む。\n"
        "- mobility: 当日の事実から、制約、影響、次の観測点まで踏み込む。\n"
        "- manufacturing: 当日の事実から、制約、影響、次の観測点まで踏み込む。\n"
        "- economy: 当日の事実から、制約、影響、次の観測点まで踏み込む。\n"
        "- game: 当日の事実から、制約、影響、次の観測点まで踏み込む。\n"
        "リスク・未確定: 発表額や性能だけでは、責任分界、費用負担、継続運用の重さはまだ見えない。\n"
        "次の観測点: 受注、量産、価格、制度適用、利用場面への落ち方を追う。\n"
        "-->\n\n"
        "今日は6月16日です。朝のニュースをお伝えします。"
        "為替 AI IT-Consulting モビリティ 製造 経済 ゲーム。"
        + ("背景には投資と制度と供給網の制約があり、影響とリスクを分けて次の観測点を確認する日でした。" * 60)
        + extra
        + "今日の観点・考察です。責任分界と供給制約を誰が引き受けるかが焦点です。"
    )


def _write_complete_fixture(root: Path) -> None:
    article_lines: list[str] = []
    for idx, cat_id in enumerate(scheduled_category_ids(ISSUE), start=1):
        folder = CATEGORY_PATHS[cat_id]["digest_folder"]
        url = f"https://example.com/{cat_id}/{idx}"
        digest = root / "digest" / folder / f"{ISSUE}-{folder}.md"
        digest.parent.mkdir(parents=True, exist_ok=True)
        digest.write_text(
            _frontmatter(f"{folder} digest", folder, cat_id)
            + f"# {folder}\n\n"
            + "> [!summary]\n> summary body\n\n"
            + f"### [{idx:02d}] {folder} article\n\n"
            + f"📅 {ISSUE} · 📰 Example · 🔗 [元記事]({url})\n\n"
            + "- body line\n",
            encoding="utf-8",
        )
        article_lines.append(
            json.dumps(
                {
                    "date": ISSUE,
                    "title": f"{folder} article",
                    "url": url,
                    "published_date": ISSUE,
                    "date_evidence_source": "fixture",
                },
                ensure_ascii=False,
            )
        )

    summary = root / "digest" / "Summary" / f"{ISSUE}.md"
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(
        "---\n"
        "title: Summary\n"
        f"date: {ISSUE}\n"
        "category: Summary\n"
        "categoryId: summary\n"
        "hero_left: Left\n"
        "hero_right: Right\n"
        "---\n\n"
        "# Summary\n\n"
        "## § 本日のテーマ考察\n\n"
        "本文です。\n",
        encoding="utf-8",
    )
    (summary.parent / f"{ISSUE}-audio-script.md").write_text(
        "---\n"
        "title: Audio Script\n"
        f"date: {ISSUE}\n"
        "type: audio-script\n"
        "---\n\n"
        + _complete_audio_script(),
        encoding="utf-8",
    )

    deepdive = root / "digest" / "DeepDive" / f"{ISSUE}-DeepDive.md"
    deepdive.parent.mkdir(parents=True, exist_ok=True)
    deepdive.write_text(
        "---\n"
        "title: DeepDive\n"
        f"date: {ISSUE}\n"
        "kind: deepdive\n"
        "---\n\n" + _complete_deepdive_body(),
        encoding="utf-8",
    )

    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "articles.jsonl").write_text("\n".join(article_lines) + "\n", encoding="utf-8")
    (data / "_status.md").write_text("ok\n", encoding="utf-8")
    (data / "search_audit" / ISSUE).mkdir(parents=True, exist_ok=True)


def test_generation_quality_passes_complete_fixture(tmp_path: Path) -> None:
    _write_complete_fixture(tmp_path)

    result = validate_generation_quality(tmp_path, ISSUE)

    assert result.exit_code == 0
    assert result.errors == []


def test_generation_quality_audio_script_uses_scheduled_categories_only_on_wednesday(tmp_path: Path) -> None:
    """水曜の音声原稿 gate は非対象 Game を要求しない。"""
    issue = "2026-06-24"
    article_lines: list[str] = []
    for idx, cat_id in enumerate(scheduled_category_ids(issue), start=1):
        folder = CATEGORY_PATHS[cat_id]["digest_folder"]
        url = f"https://example.com/{issue}/{cat_id}/{idx}"
        digest = tmp_path / "digest" / folder / f"{issue}-{folder}.md"
        digest.parent.mkdir(parents=True, exist_ok=True)
        digest.write_text(
            "---\n"
            f"title: {folder} digest\n"
            f"date: {issue}\n"
            f"category: {folder}\n"
            f"categoryId: {cat_id}\n"
            "---\n\n"
            f"# {folder}\n\n"
            f"### [{idx:02d}] {folder} article\n\n"
            f"📅 {issue} · 📰 Example · 🔗 [元記事]({url})\n\n"
            "- body line\n",
            encoding="utf-8",
        )
        article_lines.append(
            json.dumps(
                {
                    "date": issue,
                    "title": f"{folder} article",
                    "url": url,
                    "published_date": issue,
                    "date_evidence_source": "fixture",
                },
                ensure_ascii=False,
            )
        )

    summary_dir = tmp_path / "digest" / "Summary"
    summary_dir.mkdir(parents=True, exist_ok=True)
    (summary_dir / f"{issue}.md").write_text(
        "---\n"
        "title: Summary\n"
        f"date: {issue}\n"
        "category: Summary\n"
        "categoryId: summary\n"
        "hero_left: Left\n"
        "hero_right: Right\n"
        "---\n\n"
        "# Summary\n\n"
        "## § 本日のテーマ考察\n\n"
        "本文です。\n",
        encoding="utf-8",
    )
    scheduled_terms = "為替 AI IT-Consulting モビリティ 製造 経済"
    (summary_dir / f"{issue}-audio-script.md").write_text(
        "---\n"
        "title: Audio Script\n"
        f"date: {issue}\n"
        "type: audio-script\n"
        "---\n\n"
        "<!-- tts-outline\n"
        "中心論点: 需要の強さではなく、実装責任と運用条件を誰が引き受けるかを見る日。\n"
        "背景: 為替、AI、IT、モビリティ、製造、経済が同じ日に動いた。\n"
        "なぜ今: 投資、制度、価格、供給網の変更が重なった。\n"
        "因果関係: 発表内容から制約、影響、次の観測点へつなぐ。\n"
        "カテゴリ論点:\n"
        "- fx: 事実、制約、影響、次の観測点を踏み込む。\n"
        "- ai: 事実、制約、影響、次の観測点を踏み込む。\n"
        "- it: 事実、制約、影響、次の観測点を踏み込む。\n"
        "- mobility: 事実、制約、影響、次の観測点を踏み込む。\n"
        "- manufacturing: 事実、制約、影響、次の観測点を踏み込む。\n"
        "- economy: 事実、制約、影響、次の観測点を踏み込む。\n"
        "リスク・未確定: 責任分界と継続運用の重さはまだ見えない。\n"
        "次の観測点: 受注、量産、価格、制度適用を追う。\n"
        "-->\n\n"
        f"今日は6月24日です。朝のニュースをお伝えします。{scheduled_terms}。"
        + ("背景には投資と認証と防御の置き方があり、影響とリスクを分けて次の観測点を確認する日でした。" * 61)
        + "今日の観点・考察です。責任分界と供給制約を誰が引き受けるかが焦点です。",
        encoding="utf-8",
    )

    deepdive = tmp_path / "digest" / "DeepDive" / f"{issue}-DeepDive.md"
    deepdive.parent.mkdir(parents=True, exist_ok=True)
    deepdive.write_text(
        "---\n"
        "title: DeepDive\n"
        f"date: {issue}\n"
        "kind: deepdive\n"
        "---\n\n"
        + _complete_deepdive_body(),
        encoding="utf-8",
    )
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "articles.jsonl").write_text("\n".join(article_lines) + "\n", encoding="utf-8")
    (data / "_status.md").write_text("ok\n", encoding="utf-8")
    (data / "search_audit" / issue).mkdir(parents=True, exist_ok=True)

    result = validate_generation_quality(tmp_path, issue)

    assert result.exit_code == 0
    assert not any("カテゴリ不足: game" in err.reason for err in result.errors)


def test_generation_quality_rejects_missing_category_digest(tmp_path: Path) -> None:
    _write_complete_fixture(tmp_path)
    missing = tmp_path / "digest" / "Game" / f"{ISSUE}-Game.md"
    missing.unlink()

    result = validate_generation_quality(tmp_path, ISSUE)

    assert result.exit_code == 1
    assert any(err.code == "missing_artifact" and err.artifact.endswith("Game.md") for err in result.errors)


def test_generation_quality_rejects_placeholder_digest(tmp_path: Path) -> None:
    _write_complete_fixture(tmp_path)
    target = tmp_path / "digest" / "AI" / f"{ISSUE}-AI.md"
    target.write_text(_frontmatter("AI", "AI", "ai") + "# AI\n\n準備中\n", encoding="utf-8")

    result = validate_generation_quality(tmp_path, ISSUE)

    assert result.exit_code == 1
    assert any(err.code == "placeholder_digest" and err.artifact.endswith("AI.md") for err in result.errors)


def test_generation_quality_rejects_summary_without_hero(tmp_path: Path) -> None:
    _write_complete_fixture(tmp_path)
    summary = tmp_path / "digest" / "Summary" / f"{ISSUE}.md"
    summary.write_text(
        "---\n"
        "title: Summary\n"
        f"date: {ISSUE}\n"
        "category: Summary\n"
        "---\n\n"
        "# Summary\n\n"
        "## § 本日のテーマ考察\n\n本文\n",
        encoding="utf-8",
    )

    result = validate_generation_quality(tmp_path, ISSUE)

    assert result.exit_code == 1
    assert any(err.code == "summary_hero_missing" for err in result.errors)


def test_generation_quality_does_not_treat_audio_script_as_summary(tmp_path: Path) -> None:
    _write_complete_fixture(tmp_path)
    audio_script = tmp_path / "digest" / "Summary" / f"{ISSUE}-audio-script.md"
    audio_script.write_text(
        "---\n"
        "title: Audio Script\n"
        f"date: {ISSUE}\n"
        "type: audio-script\n"
        "---\n\n"
        + _complete_audio_script(),
        encoding="utf-8",
    )

    result = validate_generation_quality(tmp_path, ISSUE)

    assert result.exit_code == 0
    assert not any(err.artifact.endswith("-audio-script.md") for err in result.errors)


def test_generation_quality_rejects_invalid_audio_script(tmp_path: Path) -> None:
    _write_complete_fixture(tmp_path)
    audio_script = tmp_path / "digest" / "Summary" / f"{ISSUE}-audio-script.md"
    audio_script.write_text(
        "---\n"
        "title: Audio Script\n"
        f"date: {ISSUE}\n"
        "type: audio-script\n"
        "---\n\n"
        + _complete_audio_script("ここは少し意外でした。"),
        encoding="utf-8",
    )

    result = validate_generation_quality(tmp_path, ISSUE)

    assert result.exit_code == 1
    assert any(
        err.code == "audio_script_quality_invalid"
        and err.artifact.endswith(f"{ISSUE}-audio-script.md")
        and err.retryable is True
        for err in result.errors
    )


def test_generation_quality_rejects_audio_script_without_topic_outline(tmp_path: Path) -> None:
    _write_complete_fixture(tmp_path)
    audio_script = tmp_path / "digest" / "Summary" / f"{ISSUE}-audio-script.md"
    audio_script.write_text(
        "---\n"
        "title: Audio Script\n"
        f"date: {ISSUE}\n"
        "type: audio-script\n"
        "---\n\n"
        "今日は6月16日です。朝のニュースをお伝えします。"
        "為替 AI IT-Consulting モビリティ 製造 経済 ゲーム。"
        + ("背景と影響とリスクと次の観測点を含めて条件設計を確認する日でした。" * 95)
        + "今日の観点・考察です。責任分界と供給制約を誰が引き受けるかが焦点です。",
        encoding="utf-8",
    )

    result = validate_generation_quality(tmp_path, ISSUE)

    assert result.exit_code == 1
    assert any(
        err.code == "audio_script_quality_invalid"
        and "論点設計メモ不足" in err.reason
        for err in result.errors
    )


def test_generation_quality_rejects_missing_deepdive(tmp_path: Path) -> None:
    _write_complete_fixture(tmp_path)
    (tmp_path / "digest" / "DeepDive" / f"{ISSUE}-DeepDive.md").unlink()

    result = validate_generation_quality(tmp_path, ISSUE)

    assert result.exit_code == 1
    assert any(err.code == "missing_artifact" and "DeepDive" in err.artifact for err in result.errors)


def test_generation_quality_rejects_deepdive_with_single_chart(tmp_path: Path) -> None:
    _write_complete_fixture(tmp_path)
    deepdive = tmp_path / "digest" / "DeepDive" / f"{ISSUE}-DeepDive.md"
    deepdive.write_text(
        "---\n"
        "title: DeepDive\n"
        f"date: {ISSUE}\n"
        "kind: deepdive\n"
        "---\n\n" + _complete_deepdive_body(chart_count=1),
        encoding="utf-8",
    )

    result = validate_generation_quality(tmp_path, ISSUE)

    assert result.exit_code == 1
    assert any(
        err.code == "deepdive_structure_invalid"
        and err.artifact.endswith(f"{ISSUE}-DeepDive.md")
        and err.retryable is True
        for err in result.errors
    )


def test_generation_quality_rejects_zero_issue_articles(tmp_path: Path) -> None:
    _write_complete_fixture(tmp_path)
    (tmp_path / "data" / "articles.jsonl").write_text(
        json.dumps({"date": "2026-06-15", "title": "old", "url": "https://example.com/old"}) + "\n",
        encoding="utf-8",
    )

    result = validate_generation_quality(tmp_path, ISSUE)

    assert result.exit_code == 1
    assert any(err.code == "articles_issue_empty" for err in result.errors)


def test_generation_quality_rejects_missing_date_evidence_source(tmp_path: Path) -> None:
    _write_complete_fixture(tmp_path)
    records = []
    for line in (tmp_path / "data" / "articles.jsonl").read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        rec.pop("date_evidence_source", None)
        records.append(json.dumps(rec, ensure_ascii=False))
    (tmp_path / "data" / "articles.jsonl").write_text("\n".join(records) + "\n", encoding="utf-8")

    result = validate_generation_quality(tmp_path, ISSUE)

    assert result.exit_code == 1
    assert any(
        err.code == "date_evidence_source_missing"
        and err.artifact == "data/articles.jsonl"
        and err.retryable is True
        for err in result.errors
    )


def test_generation_quality_rejects_category_article_without_body(tmp_path: Path) -> None:
    _write_complete_fixture(tmp_path)
    target = tmp_path / "digest" / "AI" / f"{ISSUE}-AI.md"
    target.write_text(
        _frontmatter("AI", "AI", "ai")
        + "# AI\n\n"
        + "### [01] body missing\n\n"
        + "📅 2026-06-16 · 📰 Example · 🔗 [元記事](https://example.com/ai/1)\n\n"
        + "#tag/only\n",
        encoding="utf-8",
    )

    result = validate_generation_quality(tmp_path, ISSUE)

    assert result.exit_code == 1
    assert any(err.code == "category_article_body_missing" and err.category == "ai" for err in result.errors)


def test_generation_quality_json_errors_are_machine_readable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_complete_fixture(tmp_path)
    (tmp_path / "digest" / "AI" / f"{ISSUE}-AI.md").unlink()

    rc = main(["--date", ISSUE, "--repo-root", str(tmp_path), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert {"code", "artifact", "category", "retryable"} <= set(payload["errors"][0])


def test_generation_quality_config_error_returns_exit_2(tmp_path: Path) -> None:
    result = validate_generation_quality(tmp_path, "not-a-date")

    assert result.exit_code == 2
    assert any(err.retryable is False for err in result.errors)
