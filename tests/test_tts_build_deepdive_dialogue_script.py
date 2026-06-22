from __future__ import annotations

from pathlib import Path

from tools.tts import deepdive_dialogue
from tools.tts.build_deepdive_dialogue_script import build_dialogue_markdown, build_dialogue_script


def test_build_dialogue_markdown_satisfies_deepdive_tts_contract() -> None:
    source = """---
title: "円安161円台、介入協議の再点火"
date: "2026-06-23"
---
## 背景

ドル円が161円90銭台へ進み、財務省と米財務省の協議が市場の中心論点に戻った。
財務省は1兆1734.9億円の介入実績を示したが、Fed高金利と日米金利差は残っている。

## 深掘り

介入は短期の速度調整に効く一方、追加利上げは副作用管理が必要になる。
企業には輸入コスト、クラウド費用、海外SaaS契約、価格改定の前提として波及する。
"""
    markdown = build_dialogue_markdown(source, source_name="digest/DeepDive/2026-06-23-DeepDive.md")
    turns = deepdive_dialogue.parse_dialogue(markdown)

    assert not deepdive_dialogue.validate_dialogue(turns)
    assert "type: \"deepdive-dialogue\"" in markdown
    assert "若手:" in markdown
    assert "先輩:" in markdown
    assert "円安161円台、介入協議の再点火" in markdown


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

    out = build_dialogue_script(source)

    assert out == source.with_name("2026-06-23-DeepDive-dialogue.md")
    assert out.exists()
    assert not deepdive_dialogue.validate_dialogue(deepdive_dialogue.parse_dialogue(out.read_text(encoding="utf-8")))
