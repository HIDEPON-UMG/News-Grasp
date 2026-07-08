from __future__ import annotations

import json
import sys
from pathlib import Path

from tools.repair_runtime_e2e import (
    CompoundRepairStep,
    python_gate_command,
    run_compound_repair_plan,
    run_registry_repair_cycle,
)


def _write_daily_quality_repair_fixture(root: Path, issue: str) -> None:
    summary = root / "digest" / "Summary" / f"{issue}.md"
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(
        "---\n"
        "title: Summary\n"
        f"date: {issue}\n"
        "category: Daily Summary\n"
        "hero_left: プラットフォーム再編\n"
        "hero_right: 市場へ波及\n"
        "---\n\n"
        "# Summary\n\n"
        "## § 本日のテーマ考察\n\n"
        "> [[政策イベント]] と __企業実装__ が同じ日に並んだ。\n\n"
        "### §01 総論 — 実装力を見る日\n\n"
        "[[AI導入]] は __継続運用できる体制__ で評価される。\n\n"
        "### §02 為替 — 政策発言が値動きを縛る\n\n"
        "[[為替]] は **政策発言** と __金利差__ の条件で評価される。\n"
        "- 【事実・概要】：為替の事実を1文で示す。\n"
        "- 【背景・要点】：政策発言の背景を1文で示す。\n"
        "- 【影響・展望】：次の観測条件を1文で示す。\n\n"
        "### §03 AI — 資本と配布面が常用の条件になる\n\n"
        "[[AI]] は **配布面** と __導入条件__ の変化で評価される。\n"
        "- 【事実・概要】：AIの事実を1文で示す。\n"
        "- 【背景・要点】：配布面の背景を1文で示す。\n"
        "- 【影響・展望】：次の観測条件を1文で示す。\n\n"
        "### §04 IT — 導入前審査が入口になる\n\n"
        "[[IT投資]] は **導入前審査** と __運用責任__ で評価される。\n"
        "- 【事実・概要】：ITの事実を1文で示す。\n"
        "- 【背景・要点】：審査条件の背景を1文で示す。\n"
        "- 【影響・展望】：次の観測条件を1文で示す。\n\n"
        "### §05 モビリティ — 安全標準が市場を選別する\n\n"
        "[[安全標準]] は **価格改定** と __規制対応__ で評価される。\n"
        "- 【事実・概要】：モビリティの事実を1文で示す。\n"
        "- 【背景・要点】：安全標準の背景を1文で示す。\n"
        "- 【影響・展望】：次の観測条件を1文で示す。\n\n"
        "### §06 製造 — 量産配置が供給網を決める\n\n"
        "[[量産配置]] は **供給網** と __品質投資__ の変化で評価される。\n"
        "- 【事実・概要】：製造の事実を1文で示す。\n"
        "- 【背景・要点】：供給網の背景を1文で示す。\n"
        "- 【影響・展望】：次の観測条件を1文で示す。\n\n"
        "### §07 経済 — 金利負担の行き先が焦点になる\n\n"
        "[[金利負担]] は **物価** と __雇用条件__ の変化で評価される。\n"
        "- 【事実・概要】：経済の事実を1文で示す。\n"
        "- 【背景・要点】：金利負担の背景を1文で示す。\n"
        "- 【影響・展望】：次の観測条件を1文で示す。\n\n"
        "### §08 ゲーム — 販路と安全対応が収益の前提になる\n\n"
        "[[ゲーム流通]] は **販路設計** と __安全対応__ の変化で評価される。\n"
        "- 【事実・概要】：ゲームの事実を1文で示す。\n"
        "- 【背景・要点】：販路設計の背景を1文で示す。\n"
        "- 【影響・展望】：次の観測条件を1文で示す。\n",
        encoding="utf-8",
    )
    categories = [
        ("fx", "FX"),
        ("ai", "AI"),
        ("it", "IT-Consulting"),
        ("mobility", "Mobility"),
        ("manufacturing", "Manufacturing"),
        ("economy", "Economy"),
        ("game", "Game"),
    ]
    article_rows = []
    for cat_id, folder in categories:
        digest = root / "digest" / folder / f"{issue}-{folder}.md"
        digest.parent.mkdir(parents=True, exist_ok=True)
        cards = []
        for idx in range(5):
            url = f"https://example.com/2026/06/25/{cat_id}-{idx}"
            cards.append(
                f"### [{90 - idx}] {folder} article {idx + 1}\n\n"
                f"📅 {issue} 06:0{idx} · 📰 Example · 🔗 [元記事]({url})\n\n"
                f"![thumb](https://example.com/thumb-{cat_id}-{idx}.jpg)\n\n"
                "- [[論点]] **実装** __運用__\n\n"
                "---\n"
            )
            article_rows.append(
                {
                    "date": issue,
                    "seen_at": f"{issue}T06:0{idx}:00+09:00",
                    "genre": folder,
                    "title": f"{folder} article {idx + 1}",
                    "title_ja": f"{folder} 記事 {idx + 1}",
                    "url": url,
                    "url_norm": f"example.com/2026/06/25/{cat_id}-{idx}",
                    "source": "Example",
                    "date_evidence_source": "published_date",
                    "summary": "Quality gate repair fixture.",
                    "thumb": f"https://example.com/thumb-{cat_id}-{idx}.jpg",
                    "entities": {"companies": [], "countries": [], "services": [], "people": [], "tickers": []},
                    "topics": [cat_id],
                    "industries": [folder],
                    "events": ["repair"],
                    "tags": [f"topic/{cat_id}"],
                }
            )
        digest.write_text(
            "---\n"
            f"title: {folder}\n"
            f"date: {issue}\n"
            f"categoryId: {cat_id}\n"
            "---\n\n"
            + "\n".join(cards),
            encoding="utf-8",
        )
    articles = root / "data" / "articles.jsonl"
    articles.parent.mkdir(parents=True, exist_ok=True)
    articles.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in article_rows),
        encoding="utf-8",
    )


def _write_article_without_title_ja(path: Path, issue: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "date": issue,
        "seen_at": f"{issue}T06:00:00+09:00",
        "genre": "AI",
        "title": "Anthropic launches Claude repair benchmark",
        "title_ja": "",
        "url": "https://example.com/claude-repair-benchmark",
        "url_norm": "example.com/claude-repair-benchmark",
        "source": "Example",
        "summary": "Repair benchmark article.",
        "thumb": "https://example.com/og.png",
        "entities": {"companies": ["Anthropic"], "countries": [], "services": [], "people": [], "tickers": []},
        "topics": ["AI"],
        "industries": ["AI"],
        "events": ["benchmark"],
        "tags": ["topic/AI"],
    }
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")


def test_record_schema_runtime_repair_cycle_reruns_same_gate(tmp_path: Path) -> None:
    issue = "2026-06-25"
    articles = tmp_path / "data" / "articles.jsonl"
    _write_article_without_title_ja(articles, issue)

    result = run_registry_repair_cycle(
        repo_root=tmp_path,
        issue=issue,
        gate_id="record-schema",
        command=[
            sys.executable,
            "-m",
            "tools.validate_record",
            "--articles",
            str(articles),
            "--issue-date",
            issue,
            "--all",
        ],
        artifacts=["data/articles.jsonl"],
    )

    repaired = articles.read_text(encoding="utf-8")
    assert result.initial_exit_code == 1
    assert result.handler_id == "record-title-ja-patch"
    assert result.repair_status == "repaired"
    assert result.repair_changed is True
    assert result.post_repair_exit_code == 0
    assert result.final_status == "green_after_repair"
    assert '"title_ja": "Anthropic launches Claude repair benchmark"' in repaired


def test_python_gate_command_uses_current_interpreter() -> None:
    assert python_gate_command("tools.validate_record", "--all")[0] == sys.executable


def test_daily_quality_runtime_repair_cycle_reruns_same_gate(tmp_path: Path) -> None:
    issue = "2026-06-25"
    _write_daily_quality_repair_fixture(tmp_path, issue)

    result = run_registry_repair_cycle(
        repo_root=tmp_path,
        issue=issue,
        gate_id="daily-quality",
        command=python_gate_command(
            "tools.validate_daily_quality",
            "--date",
            issue,
            "--json",
        ),
        artifacts=[f"digest/Summary/{issue}.md"],
    )

    repaired = (tmp_path / "digest" / "Summary" / f"{issue}.md").read_text(encoding="utf-8")
    assert result.initial_exit_code == 1
    assert result.handler_id == "summary-emphasis-patch"
    assert result.repair_status == "repaired"
    assert result.post_repair_exit_code == 0
    assert result.final_status == "green_after_repair"
    assert "**title: Summary**" not in repaired
    assert "**[[政策イベント]] と __企業実装__ が同じ日に並んだ**。" in repaired


def test_daily_quality_runtime_repairs_search_audit_count_mismatch(tmp_path: Path) -> None:
    issue = "2026-06-25"
    _write_daily_quality_repair_fixture(tmp_path, issue)
    summary = tmp_path / "digest" / "Summary" / f"{issue}.md"
    summary_text = summary.read_text(encoding="utf-8")
    summary.write_text(
        summary_text.replace(
            "> [[政策イベント]] と __企業実装__ が同じ日に並んだ。",
            "> **[[政策イベント]] と __企業実装__ が同じ日に並んだ**。",
        ).replace(
            "[[AI導入]] は __継続運用できる体制__ で評価される。",
            "[[AI導入]] は **導入条件** と __継続運用できる体制__ で評価される。",
        ),
        encoding="utf-8",
    )
    ai_digest = tmp_path / "digest" / "AI" / f"{issue}-AI.md"
    ai_cards = []
    for idx in range(3):
        ai_cards.append(
            f"### [{90 - idx}] AI article {idx + 1}\n\n"
            f"📅 {issue} 06:0{idx} · 📰 Example · 🔗 [元記事](https://example.com/2026/06/25/ai-{idx})\n\n"
            f"![thumb](https://example.com/thumb-ai-{idx}.jpg)\n\n"
            "- [[論点]] **実装** __運用__\n\n"
            "---\n"
        )
    ai_digest.write_text(
        "---\n"
        "title: AI\n"
        f"date: {issue}\n"
        "categoryId: ai\n"
        "quality_shortfall_reason: selected quality shortfall\n"
        "---\n\n"
        + "\n".join(ai_cards),
        encoding="utf-8",
    )
    audit = tmp_path / "data" / "search_audit" / issue / "ai.json"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(
        json.dumps(
            {
                "date": issue,
                "category_id": "ai",
                "queries": ["q1", "q2", "q3"],
                "raw_results_total": 12,
                "candidates_total": 6,
                "selected_total": 8,
                "coverage_terms_checked": [
                    "OpenAI",
                    "Anthropic",
                    "Google",
                    "Apple",
                    "Microsoft",
                    "Meta",
                    "NVIDIA",
                ],
                "dropped": [{"title": "low", "reason": "quality shortfall"}],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    result = run_registry_repair_cycle(
        repo_root=tmp_path,
        issue=issue,
        gate_id="daily-quality",
        command=python_gate_command(
            "tools.validate_daily_quality",
            "--date",
            issue,
            "--json",
        ),
        artifacts=[f"data/search_audit/{issue}/ai.json"],
    )

    repaired = json.loads(audit.read_text(encoding="utf-8"))
    assert result.initial_exit_code == 1
    assert result.handler_id == "search-audit-metadata-patch"
    assert result.repair_status == "repaired"
    assert result.post_repair_exit_code == 0
    assert result.final_status == "green_after_repair"
    assert repaired["selected_total"] == 3


def test_daily_quality_runtime_repairs_sequential_known_failures(tmp_path: Path) -> None:
    issue = "2026-06-25"
    summary = tmp_path / "digest" / "Summary" / f"{issue}.md"
    audit = tmp_path / "data" / "search_audit" / issue / "mobility.json"
    summary.parent.mkdir(parents=True, exist_ok=True)
    audit.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(
        "# Summary\n\n"
        "## § 本日のテーマ考察\n\n"
        "> [[政策イベント]] が並んだ。\n\n"
        "### §01 総論 — 実装力を見る日\n\n"
        "[[AI導入]] は継続運用できる体制で評価される。\n",
        encoding="utf-8",
    )
    audit.write_text(
        json.dumps(
            {
                "date": issue,
                "category_id": "mobility",
                "candidates_total": 25,
                "selected_total": 4,
                "dropped_examples": [{"title": "old", "reason": "actual_source_age_gt_1d"}],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    gate_code = (
        "from pathlib import Path\n"
        "import json, sys\n"
        f"root=Path({str(tmp_path)!r}); issue={issue!r}\n"
        "summary=(root/'digest'/'Summary'/f'{issue}.md').read_text(encoding='utf-8')\n"
        "audit=json.loads((root/'data'/'search_audit'/issue/'mobility.json').read_text(encoding='utf-8'))\n"
        "if '**' not in summary or '__' not in summary:\n"
        "    print(f'ERROR: digest/Summary/{issue}.md: reflection section §01 lacks required emphasis: ** ** bold, __ __ underline', file=sys.stderr)\n"
        "    raise SystemExit(1)\n"
        "if 'dropped' not in audit or 'coverage_terms_checked' not in audit:\n"
        "    print(f'ERROR: data/search_audit/{issue}/mobility.json: dropped reasons are required when candidates were excluded.', file=sys.stderr)\n"
        "    print(f'ERROR: data/search_audit/{issue}/mobility.json: coverage_terms_checked missing required terms: BYD, Tesla, Toyota, Uber, Waymo', file=sys.stderr)\n"
        "    raise SystemExit(1)\n"
    )

    result = run_registry_repair_cycle(
        repo_root=tmp_path,
        issue=issue,
        gate_id="daily-quality",
        command=[sys.executable, "-c", gate_code],
        artifacts=[f"digest/Summary/{issue}.md", f"data/search_audit/{issue}"],
    )

    assert result.initial_exit_code == 1
    assert result.post_repair_exit_code == 0
    assert result.final_status == "green_after_repair"
    assert result.handler_id == "summary-emphasis-patch+search-audit-metadata-patch"


def _write_valid_article(path: Path, issue: str, *, title_ja: str = "日本語タイトル", date: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "date": date or issue,
        "seen_at": f"{issue}T06:00:00+09:00",
        "genre": "AI",
        "title": "Compound repair validates abnormal paths",
        "title_ja": title_ja,
        "url": "https://example.com/compound-repair",
        "thumb": "https://example.com/compound.png",
    }
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")


def test_compound_repair_plan_requires_every_step_green(tmp_path: Path) -> None:
    issue = "2026-06-25"
    articles = tmp_path / "data" / "articles.jsonl"
    _write_valid_article(articles, issue, title_ja="")
    gate = python_gate_command(
        "tools.validate_record",
        "--articles",
        str(articles),
        "--issue-date",
        issue,
        "--all",
    )

    result = run_compound_repair_plan(
        repo_root=tmp_path,
        issue=issue,
        no_publish=True,
        steps=[
            CompoundRepairStep(
                name="first-record-schema",
                gate_id="record-schema",
                command=gate,
                artifacts=["data/articles.jsonl"],
            ),
            CompoundRepairStep(
                name="second-record-schema",
                gate_id="record-schema",
                command=gate,
                artifacts=["data/articles.jsonl"],
            ),
        ],
    )

    assert result.final_status == "green_after_compound_repair"
    assert [step.final_status for step in result.steps] == ["green_after_repair", "already_green"]
    assert result.public_actions_attempted == ()
    assert '"title_ja": "Compound repair validates abnormal paths"' in articles.read_text(encoding="utf-8")


def test_compound_repair_plan_repairs_residual_known_failure_before_terminal(tmp_path: Path) -> None:
    issue = "2026-06-25"
    articles = tmp_path / "data" / "articles.jsonl"
    _write_valid_article(articles, issue, title_ja="", date="2026-06-24")

    result = run_compound_repair_plan(
        repo_root=tmp_path,
        issue=issue,
        no_publish=True,
        steps=[
            CompoundRepairStep(
                name="repairable-record-schema",
                gate_id="record-schema",
                command=python_gate_command(
                    "tools.validate_record",
                    "--articles",
                    str(articles),
                    "--issue-date",
                    issue,
                    "--all",
                ),
                artifacts=["data/articles.jsonl"],
            ),
        ],
    )

    assert result.final_status == "green_after_compound_repair"
    assert result.steps[0].final_status == "green_after_repair"
    assert result.steps[0].handler_id == "record-issue-date-patch+record-title-ja-patch"
    assert result.steps[0].repair_status == "repaired"
    assert "autonomous_recovery" in result.steps[0].post_repair_output
    assert '"date": "2026-06-25"' in articles.read_text(encoding="utf-8")
    assert result.public_actions_attempted == ()
