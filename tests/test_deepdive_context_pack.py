from __future__ import annotations

import json
from pathlib import Path

from tools.deepdive_context_pack import build_context_pack, main


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


def test_context_pack_excludes_unrelated_recent_and_fulltext(tmp_path: Path) -> None:
    repo = tmp_path
    _write_article(
        repo,
        {
            "date": "2026-06-28",
            "title": "OpenAI Codexの全社展開で業務OS化が進む",
            "summary": "Codex、AIエージェント、workflow、検収責任が導入論点になった。",
            "tags": ["OpenAI", "Codex", "AI-agent", "workflow"],
            "entities": ["OpenAI", "Codex"],
            "topics": ["workflow", "governance"],
        },
    )
    _write_deepdive(
        repo / "digest" / "DeepDive" / "2026-06-20-DeepDive.md",
        title="AIエージェントは操作代行から統制設計へ",
        tags=["deepdive", "OpenAI", "AI-agent", "workflow", "governance"],
        body="## 背景\n前回はAIエージェントの委任、監査、検収責任を扱った。全文混入検査用の長大本文SENTINEL_FULL_TEXT_SHOULD_NOT_LEAKを含む。\n",
    )
    _write_deepdive(
        repo / "digest" / "DeepDive" / "2026-06-27-DeepDive.md",
        title="旅行需要とホテル価格の季節変動",
        tags=["deepdive", "travel", "hotel", "tourism"],
        body="## 背景\n旅行需要とホテル価格は季節要因で変動している。\n",
    )

    pack = build_context_pack("2026-06-28", repo_root=repo)
    blob = json.dumps(pack, ensure_ascii=False)
    dates = [item["date"] for item in pack["candidates"]]

    assert dates == ["2026-06-20"]
    assert "2026-06-27" not in dates
    assert "SENTINEL_FULL_TEXT_SHOULD_NOT_LEAK" not in blob
    assert len(pack["candidates"]) <= 8
    assert pack["candidates"][0]["evidence"]
    assert pack["candidates"][0]["signal_terms"]
    assert pack["candidates"][0]["relation"] in {"続報", "主役共有", "波及", "対比"}


def test_context_pack_ignores_ai_only_overlap(tmp_path: Path) -> None:
    repo = tmp_path
    _write_article(
        repo,
        {
            "date": "2026-06-28",
            "title": "AIという語だけが共通する別テーマ",
            "summary": "Codexやworkflowではなく、半導体需給を扱う。",
            "tags": ["AI"],
        },
    )
    _write_deepdive(
        repo / "digest" / "DeepDive" / "2026-06-26-DeepDive.md",
        title="AIを使った旅行需要予測",
        tags=["deepdive", "AI", "travel", "hotel"],
        body="## 背景\n旅行需要予測を扱った。\n",
    )

    pack = build_context_pack("2026-06-28", repo_root=repo)

    assert pack["candidates"] == []


def test_context_pack_does_not_split_generic_news_grasp_tag_into_signal(tmp_path: Path) -> None:
    repo = tmp_path
    _write_article(
        repo,
        {
            "date": "2026-08-01",
            "title": "軽EVの価格競争",
            "summary": "BYDと国内軽EVの価格、航続距離、販売網を扱う。",
            "tags": ["news-grasp", "issue-20260801", "BYD", "軽EV"],
        },
    )
    _write_deepdive(
        repo / "digest" / "DeepDive" / "2026-07-31-DeepDive.md",
        title="AIクラウドの調達戦略",
        tags=["news-grasp", "issue-20260731", "AI", "cloud"],
        body="## 背景\nAIクラウドと調達戦略を扱った。\n",
    )

    pack = build_context_pack("2026-08-01", repo_root=repo)

    assert pack["candidates"] == []


def test_context_pack_rejects_single_concept_overlap_without_explicit_related(tmp_path: Path) -> None:
    repo = tmp_path
    _write_article(
        repo,
        {
            "date": "2026-08-01",
            "title": "軽EVの価格競争",
            "summary": "BYDと国内軽EVの価格を扱う。",
            "tags": ["BYD", "軽EV", "価格競争"],
        },
    )
    _write_deepdive(
        repo / "digest" / "DeepDive" / "2026-07-31-DeepDive.md",
        title="AI価格競争と配布設計",
        tags=["OpenAI", "生成AI", "価格競争"],
        body="## 背景\nAIサービスの単価と配布設計を扱った。\n",
    )

    pack = build_context_pack("2026-08-01", repo_root=repo)

    assert pack["candidates"] == []


def test_context_pack_cli_writes_small_json(tmp_path: Path) -> None:
    repo = tmp_path
    output = repo / "build" / "deepdive-context" / "2026-06-28.json"
    _write_article(
        repo,
        {
            "date": "2026-06-28",
            "title": "OpenAI Codex workflow",
            "summary": "Codex workflow governance",
            "tags": ["OpenAI", "Codex", "workflow"],
        },
    )
    _write_deepdive(
        repo / "digest" / "DeepDive" / "2026-06-20-DeepDive.md",
        title="Codex workflow governance",
        tags=["deepdive", "OpenAI", "Codex", "workflow"],
        body="## 背景\nCodex workflow governanceを扱った。\n",
    )

    assert main(["--date", "2026-06-28", "--repo-root", str(repo), "--output", str(output)]) == 0
    data = json.loads(output.read_text(encoding="utf-8"))

    assert data["date"] == "2026-06-28"
    assert data["candidates"][0]["date"] == "2026-06-20"
