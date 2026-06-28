from __future__ import annotations

import json
from pathlib import Path

from tools.tts import build_script, deepdive_dialogue
from tools.tts.build_deepdive_dialogue_script import build_dialogue_markdown, build_dialogue_script


def _write_deepdive(path: Path, *, title: str, tags: list[str], body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f'title: "{title}"\n'
        f'date: "{path.name[:10]}"\n'
        "tags: ["
        + ", ".join(f'"{tag}"' for tag in tags)
        + "]\n"
        "---\n\n"
        + body,
        encoding="utf-8",
    )


def test_build_dialogue_markdown_satisfies_deepdive_tts_contract(tmp_path: Path) -> None:
    archive = tmp_path / "digest" / "DeepDive"
    _write_deepdive(
        archive / "2026-06-21-DeepDive.md",
        title="為替介入、政策効果と副作用",
        tags=["deepdive", "currency", "boj", "market"],
        body="## 背景\n前回は為替介入の政策効果と副作用を扱った。\n",
    )
    _write_deepdive(
        archive / "2026-06-22-DeepDive.md",
        title="円安と企業コスト、価格転嫁の焦点",
        tags=["deepdive", "currency", "cost", "market"],
        body="## 背景\n前回は円安が企業コストと価格転嫁に与える影響を扱った。\n",
    )
    source = """---
title: "円安161円台、介入協議の再点火"
date: "2026-06-23"
tags: ["deepdive", "currency", "boj", "market"]
---
## 背景

ドル円が161円90銭台へ進み、財務省と米財務省の協議が市場の中心論点に戻った。
財務省は1兆1734.9億円の介入実績を示したが、Fed高金利と日米金利差は残っている。

## 深掘り

介入は短期の速度調整に効く一方、追加利上げは副作用管理が必要になる。
企業には輸入コスト、クラウド費用、海外SaaS契約、価格改定の前提として波及する。
"""
    markdown = build_dialogue_markdown(
        source,
        source_name="digest/DeepDive/2026-06-23-DeepDive.md",
        archive_dir=archive,
    )
    turns = deepdive_dialogue.parse_dialogue(markdown)

    assert not deepdive_dialogue.validate_dialogue(turns)
    assert "type: \"deepdive-dialogue\"" in markdown
    assert "若手:" in markdown
    assert "先輩:" in markdown
    assert "円安161円台、介入協議の再点火" in markdown


def test_build_dialogue_markdown_uses_recent_deepdive_context(tmp_path: Path) -> None:
    archive = tmp_path / "digest" / "DeepDive"
    _write_deepdive(
        archive / "2026-06-20-DeepDive.md",
        title="AIエージェントは操作代行から統制設計へ",
        tags=["deepdive", "OpenAI", "AI-agent", "workflow", "Security"],
        body=(
            "## 背景\n"
            "前回は、AIエージェントに操作権限を渡すときの統制設計を扱った。\n"
            "委任、監査、成果物レビューの境界が企業導入の焦点になった。\n"
        ),
    )
    _write_deepdive(
        archive / "2026-06-21-DeepDive.md",
        title="アクセンチュア急落、AI変革の時間差",
        tags=["deepdive", "Accenture", "AI", "IT-Consulting", "workflow"],
        body=(
            "## 背景\n"
            "コンサル企業ではAI変革の成果が売上へ反映されるまで時間差がある。\n"
            "業務設計、価格モデル、検収責任が顧客説明の中心になった。\n"
        ),
    )
    source = """---
title: "Codex浸透、全社業務OSへ"
date: "2026-06-28"
tags: ["deepdive", "OpenAI", "Codex", "AI-agent", "workflow", "skills"]
---

## 背景

```related
[
  {"date": "2026-06-20", "title": "AIエージェントは操作代行から統制設計へ", "relation": "続報", "link": "AIエージェント運用", "change": "前回はAIエージェントに実行権限を渡す統制設計を扱った。今回の変化点は、Codex利用データの公開で業務設計そのものが論点になったことだ。"}
]
```

OpenAIがCodex利用データを公開し、社内利用率と外部組織利用率の差が見えた。
論点は、AIツールを導入するかではなく、どの業務を委任し、どの成果物を人が検収するかへ移った。

## 深掘り

CodexはChatGPTの代替ではなく、作業を割り振る業務OSとして使われ始めている。
権限、ファイルアクセス、レビュー設計、skills再利用が導入成否を分ける。
"""

    markdown = build_dialogue_markdown(
        source,
        source_name="digest/DeepDive/2026-06-28-DeepDive.md",
        archive_dir=archive,
    )
    turns = deepdive_dialogue.parse_dialogue(markdown)

    assert "context_sources:" in markdown
    assert "2026-06-20" in markdown
    assert "2026-06-21" in markdown
    assert "前回は" in markdown
    assert "今回" in markdown
    assert "ITコンサルタント" in markdown
    assert "背景OpenAI" not in markdown
    assert "背景前回" not in markdown
    assert "今回の変化点は、前回は" not in markdown
    assert "最後に、現場で今日から確認できることはありますか" not in markdown
    assert not deepdive_dialogue.validate_dialogue(turns)


def test_build_dialogue_markdown_rejects_unrelated_recent_fallback(tmp_path: Path) -> None:
    archive = tmp_path / "digest" / "DeepDive"
    _write_deepdive(
        archive / "2026-06-24-DeepDive.md",
        title="食品スーパーの棚割り最適化",
        tags=["deepdive", "retail", "grocery", "store-layout"],
        body="## 背景\n食品スーパーでは棚割りと在庫回転率の最適化が焦点になった。\n",
    )
    _write_deepdive(
        archive / "2026-06-25-DeepDive.md",
        title="旅行需要とホテル価格の季節変動",
        tags=["deepdive", "travel", "hotel", "tourism"],
        body="## 背景\n旅行需要とホテル価格は季節要因で変動している。\n",
    )
    source = """---
title: "Codex浸透、全社業務OSへ"
date: "2026-06-28"
tags: ["deepdive", "OpenAI", "Codex", "AI-agent", "workflow", "skills"]
---

OpenAIがCodex利用データを公開し、業務委任と検収責任が焦点になった。
"""

    try:
        build_dialogue_markdown(
            source,
            source_name="digest/DeepDive/2026-06-28-DeepDive.md",
            archive_dir=archive,
        )
    except ValueError as exc:
        assert "関連DeepDive文脈不足" in str(exc)
    else:
        raise AssertionError("関連性のない直近DeepDiveをfallback採用してはいけない")


def test_build_dialogue_markdown_uses_context_pack_without_related_block(tmp_path: Path) -> None:
    archive = tmp_path / "digest" / "DeepDive"
    source = """---
title: "Codex浸透、全社業務OSへ"
date: "2026-06-28"
tags: ["deepdive", "OpenAI", "Codex", "AI-agent", "workflow", "skills"]
---

## 背景

Codexは業務を割り振る実行基盤として使われ始めた。
委任、検収、skills再利用が実務上の焦点になっている。
"""
    _write_deepdive(
        archive / "2026-06-20-DeepDive.md",
        title="AIエージェントは操作代行から統制設計へ",
        tags=["deepdive", "OpenAI", "AI-agent", "workflow"],
        body="## 背景\n前回はAIエージェントの統制設計を扱った。\n",
    )
    _write_deepdive(
        archive / "2026-06-21-DeepDive.md",
        title="Codex導入と検収責任",
        tags=["deepdive", "Codex", "workflow", "skills"],
        body="## 背景\n前回はCodex導入と検収責任を扱った。\n",
    )
    pack = tmp_path / "build" / "deepdive-context" / "2026-06-28.json"
    pack.parent.mkdir(parents=True)
    pack.write_text(
        json.dumps(
            {
                "date": "2026-06-28",
                "candidates": [
                    {
                        "date": "2026-06-20",
                        "title": "AIエージェントは操作代行から統制設計へ",
                        "relation": "主役共有",
                        "score": 15,
                        "signal_terms": ["openai", "ai-agent", "workflow"],
                        "evidence": [{"type": "term_overlap", "terms": ["openai", "workflow"]}],
                        "summary_excerpt": "前回はAIエージェントの統制設計を扱った。",
                        "decision_issue": "委任と検収の境界",
                    },
                    {
                        "date": "2026-06-21",
                        "title": "Codex導入と検収責任",
                        "relation": "続報",
                        "score": 14,
                        "signal_terms": ["codex", "workflow", "skills"],
                        "evidence": [{"type": "term_overlap", "terms": ["codex", "skills"]}],
                        "summary_excerpt": "前回はCodex導入と検収責任を扱った。",
                        "decision_issue": "成果物レビューの責任",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    markdown = build_dialogue_markdown(
        source,
        source_name="digest/DeepDive/2026-06-28-DeepDive.md",
        archive_dir=archive,
        context_pack_path=pack,
    )

    assert "2026-06-20" in markdown
    assert "2026-06-21" in markdown
    assert "context_sources:" in markdown
    assert not deepdive_dialogue.validate_dialogue(deepdive_dialogue.parse_dialogue(markdown))


def test_build_dialogue_markdown_expands_when_two_context_sources_exist(tmp_path: Path) -> None:
    archive = tmp_path / "digest" / "DeepDive"
    source = """---
title: "Codex浸透、全社業務OSへ"
date: "2026-06-28"
tags: ["deepdive", "OpenAI", "Codex", "AI-agent", "workflow", "skills"]
---

## 背景

Codexは業務を割り振る実行基盤として使われ始めた。
委任、検収、skills再利用が実務上の焦点になっている。

## 深掘り

導入率を見るだけでは不十分で、権限、レビュー、再利用可能な作業手順まで見ないと実装の深さは測れない。
"""
    _write_deepdive(
        archive / "2026-06-20-DeepDive.md",
        title="AIエージェントは操作代行から統制設計へ",
        tags=["deepdive", "OpenAI", "AI-agent", "workflow"],
        body="## 背景\n前回はAIエージェントの統制設計を扱った。委任、監査、成果物レビューの境界が企業導入の焦点になった。\n",
    )
    _write_deepdive(
        archive / "2026-06-21-DeepDive.md",
        title="Codex導入と検収責任",
        tags=["deepdive", "Codex", "workflow", "skills"],
        body="## 背景\n前回はCodex導入と検収責任を扱った。業務設計、価格モデル、検収責任が顧客説明の中心になった。\n",
    )

    markdown = build_dialogue_markdown(
        source,
        source_name="digest/DeepDive/2026-06-28-DeepDive.md",
        archive_dir=archive,
    )
    turns = deepdive_dialogue.parse_dialogue(markdown)
    char_count = build_script.effective_char_count("\n".join(turn.text for turn in turns))

    assert "audio_target_minutes: 6" in markdown
    assert len(turns) >= 28
    assert char_count >= 1600
    assert "各過去回を、今回の実務判断に変換すると" in markdown
    assert "最後に、現場で今日から確認できることはありますか" not in markdown
    assert not deepdive_dialogue.validate_dialogue(turns)


def test_build_dialogue_markdown_rejects_context_pack_candidate_without_evidence(tmp_path: Path) -> None:
    archive = tmp_path / "digest" / "DeepDive"
    _write_deepdive(
        archive / "2026-06-20-DeepDive.md",
        title="食品スーパーの棚割り最適化",
        tags=["deepdive", "retail", "grocery"],
        body="## 背景\n食品スーパーの棚割り最適化を扱った。\n",
    )
    pack = tmp_path / "build" / "deepdive-context" / "2026-06-28.json"
    pack.parent.mkdir(parents=True)
    pack.write_text(
        json.dumps(
            {
                "date": "2026-06-28",
                "candidates": [
                    {
                        "date": "2026-06-20",
                        "title": "食品スーパーの棚割り最適化",
                        "relation": "対比",
                        "score": 99,
                        "signal_terms": [],
                        "evidence": [],
                        "summary_excerpt": "食品スーパーの棚割り最適化を扱った。",
                        "decision_issue": "無関係な候補",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    source = """---
title: "Codex浸透、全社業務OSへ"
date: "2026-06-28"
tags: ["deepdive", "OpenAI", "Codex", "AI-agent", "workflow", "skills"]
---

OpenAIがCodex利用データを公開し、業務委任と検収責任が焦点になった。
"""

    try:
        build_dialogue_markdown(
            source,
            source_name="digest/DeepDive/2026-06-28-DeepDive.md",
            archive_dir=archive,
            context_pack_path=pack,
        )
    except ValueError as exc:
        assert "関連DeepDive文脈不足" in str(exc)
    else:
        raise AssertionError("根拠なしcontext pack候補をTTSが採用してはいけない")


def test_build_dialogue_script_regenerates_existing_script_without_context_sources(tmp_path: Path) -> None:
    source = tmp_path / "digest" / "DeepDive" / "2026-06-28-DeepDive.md"
    _write_deepdive(
        source,
        title="Codex浸透、全社業務OSへ",
        tags=["deepdive", "OpenAI", "Codex", "AI-agent", "workflow", "skills"],
        body=(
            "## 背景\n"
            "Codexは業務を割り振る実行基盤として使われ始めた。\n"
            "委任、検収、skills再利用が実務上の焦点になっている。\n"
        ),
    )
    _write_deepdive(
        source.with_name("2026-06-20-DeepDive.md"),
        title="AIエージェントは操作代行から統制設計へ",
        tags=["deepdive", "OpenAI", "AI-agent", "workflow"],
        body="## 背景\n前回はAIエージェントの統制設計を扱った。\n",
    )
    _write_deepdive(
        source.with_name("2026-06-21-DeepDive.md"),
        title="アクセンチュア急落、AI変革の時間差",
        tags=["deepdive", "Accenture", "AI", "IT-Consulting", "workflow"],
        body="## 背景\nAI変革の時間差と価格モデルを扱った。\n",
    )
    out = source.with_name("2026-06-28-DeepDive-dialogue.md")
    out.write_text(
        "若手: 最後に、現場で今日から確認できることはありますか。\n"
        "先輩: 契約通貨、更新時期、価格改定の余地、代替調達先を確認することだね。\n",
        encoding="utf-8",
    )

    build_dialogue_script(source)

    regenerated = out.read_text(encoding="utf-8")
    assert "context_sources:" in regenerated
    assert "最後に、現場で今日から確認できることはありますか" not in regenerated


def test_build_dialogue_script_writes_expected_path(tmp_path: Path) -> None:
    source = tmp_path / "digest" / "DeepDive" / "2026-06-23-DeepDive.md"
    source.parent.mkdir(parents=True)
    source.write_text(
        """---
title: "テストDeepDive"
date: "2026-06-23"
---
## 背景

これは復旧テスト用のDeepDive本文です。政策、企業影響、次の焦点を説明します。
""",
        encoding="utf-8",
    )
    _write_deepdive(
        source.with_name("2026-06-21-DeepDive.md"),
        title="前回DeepDive一",
        tags=["deepdive", "政策", "企業影響"],
        body="## 背景\n前回は政策の前提と企業影響を扱った。\n",
    )
    _write_deepdive(
        source.with_name("2026-06-22-DeepDive.md"),
        title="前回DeepDive二",
        tags=["deepdive", "AI", "次の焦点"],
        body="## 背景\n前回はAI活用と次の焦点を扱った。\n",
    )

    out = build_dialogue_script(source)

    assert out == source.with_name("2026-06-23-DeepDive-dialogue.md")
    assert out.exists()
    assert not deepdive_dialogue.validate_dialogue(deepdive_dialogue.parse_dialogue(out.read_text(encoding="utf-8")))
