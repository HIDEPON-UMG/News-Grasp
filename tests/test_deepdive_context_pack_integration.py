from __future__ import annotations

import json
from pathlib import Path

from tools.deepdive_context_pack import build_context_pack, write_context_pack
from tools.render_deepdive import _require_blocks, extract_blocks
from tools.tts import deepdive_dialogue
from tools.tts.build_deepdive_dialogue_script import build_dialogue_markdown
from tools.validate_deepdive_urls import extract_urls


def _write_article(repo: Path, record: dict[str, object]) -> None:
    path = repo / "data" / "articles.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _write_deepdive(path: Path, *, title: str, tags: list[str], body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f'title: "{title}"\n'
        f'date: "{path.name[:10]}"\n'
        f'theme: "{title}"\n'
        'lens: "tech"\n'
        "tags: ["
        + ", ".join(f'"{tag}"' for tag in tags)
        + "]\n"
        "---\n\n"
        + body,
        encoding="utf-8",
    )


def _grounded_body() -> str:
    return "\n".join(
        [
            "最新発表は対象業務と導入時期を明示した。",
            "公開統計は部門ごとの採用速度の差を示した。",
            "制度変更は実行権限と検収責任の分離を求めた。",
            "企業事例は定型作業から委任を広げている。",
            "費用分析は継続監査の負担を重要視した。",
            "障害記録は曖昧な権限境界が復旧を遅らせた。",
            "反対事例では高い自動化率でも品質が改善しなかった。",
            "比較資料は限定導入の方が検証可能性を保った。",
            "時系列では試行から全社運用へ論点が移った。",
            "現場調査は再利用手順が定着率を左右すると示した。",
            "監査結果は成果物の根拠追跡を必須とした。",
            "経営判断は便益と停止条件の両方を求めた。",
            "次の確認点は責任者と再検証時期の固定である。",
            "実務対応は小規模な証拠収集から範囲を広げる。",
        ]
    )


def _current_deepdive() -> str:
    return """---
title: "Codex浸透、全社業務OSへ"
date: "2026-06-28"
theme: "Codex全社展開"
lens: "tech"
tags: ["deepdive", "OpenAI", "Codex", "AI-agent", "workflow", "skills"]
---

## 背景

Codexは業務を割り振る実行基盤として使われ始めた。

```timeline
[
  {"date": "2026-06-20", "event": "AIエージェント統制の論点化", "url": "https://example.com/agent"},
  {"date": "2026-06-28", "event": "Codex利用データの公開", "url": "https://example.com/codex"}
]
```

```players
[
  {"name": "OpenAI", "role": "Codex提供者"},
  {"name": "企業IT部門", "role": "導入と統制の責任主体"}
]
```

```relations
{
  "source": "https://example.com/codex",
  "nodes": [{"id": "openai", "label": "OpenAI"}, {"id": "it", "label": "企業IT部門"}],
  "edges": [{"from": "openai", "to": "it", "kind": "供給", "label": "Codex"}]
}
```

## 深掘り

業務委任、検収責任、skills再利用が実務上の焦点になっている。

```chart
{"title": "Codex導入論点", "type": "bar", "unit": "点", "source": "https://example.com/codex", "data": [{"label": "委任", "value": 3}, {"label": "検収", "value": 4}]}
```

```chart
{"title": "統制成熟度", "type": "line", "unit": "点", "source": "https://example.com/agent", "data": [{"label": "前回", "value": 2}, {"label": "今回", "value": 4}]}
```

```table
{"title": "実務確認表", "source": "https://example.com/codex", "columns": ["論点", "確認"], "rows": [["委任", "対象業務"], ["検収", "責任者"]]}
```

## 注目点

導入ツール名ではなく、どの業務をAIへ渡し、どこで止めるかが判断材料になる。

```decision
{"issue": "Codexをどの業務へ委任するか", "options": ["限定導入", "全社展開"], "deadline": "2026-Q3", "decider": "CIO"}
```

## 要約

Codexの全社展開は、AIツール導入ではなく業務設計の論点として読む必要がある。

## 参考リンク

- OpenAI Codex overview: https://example.com/codex
""" + _grounded_body()


def test_context_pack_to_downstream_gates_and_tts(tmp_path: Path) -> None:
    repo = tmp_path
    _write_article(
        repo,
        {
            "date": "2026-06-28",
            "title": "OpenAI Codexの全社展開で業務OS化が進む",
            "summary": "Codex、AIエージェント、workflow、検収責任が導入論点になった。",
            "tags": ["OpenAI", "Codex", "AI-agent", "workflow", "skills"],
        },
    )
    archive = repo / "digest" / "DeepDive"
    _write_deepdive(
        archive / "2026-06-20-DeepDive.md",
        title="AIエージェントは操作代行から統制設計へ",
        tags=["deepdive", "OpenAI", "AI-agent", "workflow"],
        body="## 背景\n前回はAIエージェントの委任、監査、検収責任を扱った。\n",
    )
    _write_deepdive(
        archive / "2026-06-21-DeepDive.md",
        title="Codex導入と検収責任",
        tags=["deepdive", "Codex", "workflow", "skills"],
        body="## 背景\n前回はCodex導入と検収責任を扱った。\n",
    )
    current = archive / "2026-06-28-DeepDive.md"
    current.write_text(_current_deepdive(), encoding="utf-8")
    pack_path = repo / "build" / "deepdive-context" / "2026-06-28.json"

    pack = write_context_pack("2026-06-28", repo_root=repo, output=pack_path)
    text = current.read_text(encoding="utf-8")
    blocks = extract_blocks(text)
    _require_blocks(current, blocks)
    urls = extract_urls(text)
    markdown = build_dialogue_markdown(
        text,
        source_name=current.as_posix(),
        archive_dir=archive,
        context_pack_path=pack_path,
    )

    assert len(pack["candidates"]) == 2
    assert all(item["evidence"] for item in pack["candidates"])
    assert {ref.location for ref in urls} >= {"refs", "timeline", "relations.source", "chart.source", "table.source"}
    assert not deepdive_dialogue.validate_dialogue(deepdive_dialogue.parse_dialogue(markdown))
    assert "2026-06-20" in markdown
    assert "2026-06-21" in markdown
