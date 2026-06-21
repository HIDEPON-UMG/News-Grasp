#!/usr/bin/env python3
"""News-Grasp product constitution contract tests."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "docs" / "spec.md"
README = ROOT / "README.md"
AGENTS = ROOT / "AGENTS.md"
CLAUDE = ROOT / "CLAUDE.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _headings(markdown: str) -> set[str]:
    return set(re.findall(r"(?m)^##+\s+(.+)$", markdown))


def test_product_constitution_defines_mission_reader_and_outcome() -> None:
    text = _read(SPEC)
    headings = _headings(text)

    assert "Product Constitution" in headings
    assert "ITコンサルタント" in text
    assert "膨大なニュースを一つ一つ確認" in text
    assert "最適な粒度" in text
    assert "完全自立型ニュースサイト" in text


def test_product_constitution_locks_done_and_autonomous_repair_principles() -> None:
    text = _read(SPEC)
    headings = _headings(text)

    required_headings = {
        "Principle 1: 直せるものは直して完走",
        "Definition of Done",
        "System Integrity",
        "Fatal Boundaries",
        "Change Governance",
    }
    assert required_headings <= headings

    for phrase in [
        "直せるものは直して完走",
        "repair",
        "quarantine+refill",
        "reporter retry",
        "re-verify",
        "verified",
        "typed fatal",
        "Web / Audio / YouTube Podcast / playlist / notification",
    ]:
        assert phrase in text


def test_product_constitution_maps_feature_changes_to_quality_gates() -> None:
    text = _read(SPEC)
    headings = _headings(text)

    assert "Feature Change Quality Gate Matrix" in headings
    for phrase in [
        "機能を追加、削除、修正する場合",
        "同じ変更単位で品質 gate",
        "Source collection / URL freshness / dedup",
        "Article data / schema / tags",
        "Web publish surface",
        "Public UI / OGP / PWA / thumbnails",
        "Audio / TTS",
        "YouTube Podcast / playlist",
        "Notification",
        "Runner / state / recovery",
        "Incident / reporting",
        "Affected matrix rows",
        "Gate update decision",
        "Verification command",
        "docs/spec.md",
        "tests/test_product_spec_contract.py",
    ]:
        assert phrase in text


def test_product_constitution_keeps_markdown_structure_and_links_minimal() -> None:
    text = _read(SPEC)

    assert text.startswith("# Product Spec: News-Grasp")
    assert "## Open Questions" not in text
    assert "Describe the stable user or operator outcome" not in text
    assert "| Area | Requirement |" in text
    assert text.count("docs/spec.md") >= 1


def test_product_constitution_is_referenced_from_repo_entrypoints() -> None:
    readme = _read(README)
    agents = _read(AGENTS)
    claude = _read(CLAUDE)

    assert "[docs/spec.md](docs/spec.md)" in readme
    for text in [agents, claude]:
        assert "docs/spec.md" in text
        assert "上位プロダクト真実" in text
        assert "非自明な News-Grasp 改修" in text
        assert "Feature Change Quality Gate Matrix" in text
        assert "affected" in text
        assert "tests/test_product_spec_contract.py" in text
