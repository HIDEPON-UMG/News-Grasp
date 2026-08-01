from __future__ import annotations

from pathlib import Path

import pytest

from tools.tts import deepdive_dialogue
from tools.tts import build_deepdive_dialogue_script


def _sentence(index: int) -> str:
    return f"固有主体{index}が固有時点{index}に固有変化{index}を確認した根拠文です。"


def _source() -> str:
    return "---\ntitle: test\n---\n\n" + "\n\n".join(
        _sentence(index) for index in range(20)
    )


def _document(
    *,
    omit: str | None = None,
    duplicate: str | None = None,
    include_support: bool = True,
    include_source_text: bool = True,
) -> str:
    blocks: list[str] = []
    for index, value_id in enumerate(deepdive_dialogue.REQUIRED_VALUE_IDS):
        if value_id == omit:
            continue
        evidence = _sentence(index) if include_source_text else f"主根拠{index}"
        support = _sentence(index + 7) if include_source_text else f"補助根拠{index + 7}"
        marker = f"<!-- value:{value_id} evidence:source:{index}"
        if include_support:
            marker += f" support:source:{index + 7}"
        marker += " -->"
        blocks.append(
            f"{marker}\n"
            f"若手: {evidence} この観点をどう読みますか。\n\n"
            f"先輩: {evidence} {support} {value_id}では二つを分けて確認します。"
        )
        if value_id == duplicate:
            blocks.append(blocks[-1])
    return "\n\n".join(blocks)


def test_valid_reader_value_document_passes_contract() -> None:
    issues = deepdive_dialogue.validate_dialogue_document(
        _document(),
        source_markdown=_source(),
    )
    assert issues == []


def test_generator_builds_a_valid_grounded_document_from_sufficient_source() -> None:
    source = _source()
    text = build_deepdive_dialogue_script.build_dialogue_markdown(
        source,
        source_name="digest/DeepDive/2026-08-01-DeepDive.md",
    )
    assert deepdive_dialogue.validate_dialogue_document(
        text,
        source_markdown=source,
    ) == []


def test_value_contract_requires_exact_ordered_value_ids() -> None:
    turns = deepdive_dialogue.parse_dialogue(_document(omit="causal_chain"))
    issues = deepdive_dialogue.validate_value_contract(turns)
    assert any("価値ID" in issue and "causal_chain" in issue for issue in issues)


def test_value_contract_rejects_duplicate_value_segment() -> None:
    turns = deepdive_dialogue.parse_dialogue(_document(duplicate="evidence"))
    issues = deepdive_dialogue.validate_value_contract(turns)
    assert any("順序" in issue or "重複" in issue for issue in issues)


def test_value_contract_rejects_semantic_paraphrase_loop() -> None:
    blocks: list[str] = []
    repeated = "導入前提と責任分界と検収手順を確認し、運用で止める地点を決めます。"
    for index, value_id in enumerate(deepdive_dialogue.REQUIRED_VALUE_IDS):
        blocks.append(
            f"<!-- value:{value_id} evidence:source:{index} support:source:{index + 7} -->\n"
            f"若手: 観点{index}では何を確認しますか。\n\n"
            f"先輩: 観点{index}でも、{repeated}"
        )
    issues = deepdive_dialogue.validate_value_contract(
        deepdive_dialogue.parse_dialogue("\n\n".join(blocks))
    )
    assert any("意味反復" in issue for issue in issues)


def test_legacy_fixed_filler_is_fatal_even_when_length_is_sufficient() -> None:
    text = _document() + (
        "\n\n若手: ここまでをつなぐと、若手側からも一つ指摘できそうです。"
        "\n\n先輩: 言ってみよう。数字の大小ではなく、業務OSとして定着する条件を見抜けるかが今回の肝だ。"
    )
    issues = deepdive_dialogue.validate_dialogue_document(text)
    assert any("旧定型句" in issue for issue in issues)


def test_current_fixed_value_scaffold_is_fatal() -> None:
    text = _document() + "\n\n".join(
        (
            "若手: 追加確認です。",
            "先輩: 起点はここです。この二点を分けると、今日固有の変化が見えます。",
            "先輩: 確認できる事実は次の通りです。数字、主体、時点を混ぜずに読む必要があります。",
            "先輩: 記事が示す接続は次の通りです。原因と結果を同じ事実として扱わないことが重要です。",
            "先輩: 限界線はここです。ここから先は仮説として分離します。",
            "先輩: 変化の線はこうです。同じ説明の再掲ではありません。",
            "先輩: 判断材料を整理し、顧客ごとに前提条件を比較します。",
            "先輩: 最初に確認し、次に比較し、最後に判断前提を更新してください。",
        )
    )
    issues = deepdive_dialogue.validate_dialogue_document(text)
    assert any("固定価値テンプレート" in issue for issue in issues)


def test_value_segments_require_two_unique_source_evidence_bindings() -> None:
    issues = deepdive_dialogue.validate_value_contract(
        deepdive_dialogue.parse_dialogue(_document(include_support=False))
    )
    assert any("補助根拠" in issue for issue in issues)


def test_grounding_rejects_out_of_range_source_index() -> None:
    text = _document().replace("evidence:source:6", "evidence:source:99")
    issues = deepdive_dialogue.validate_dialogue_document(text, source_markdown=_source())
    assert any("根拠番号範囲外" in issue for issue in issues)


def test_grounding_rejects_marker_only_without_source_text() -> None:
    issues = deepdive_dialogue.validate_dialogue_document(
        _document(include_source_text=False),
        source_markdown=_source(),
    )
    assert any("根拠本文不一致" in issue for issue in issues)


def test_value_contract_rejects_reused_source_bindings() -> None:
    issues = deepdive_dialogue.validate_value_contract(
        deepdive_dialogue.parse_dialogue(
            _document().replace("support:source:8", "support:source:7")
        )
    )
    assert any("根拠再利用" in issue for issue in issues)


def test_generator_rejects_source_with_fewer_than_fourteen_facts() -> None:
    source = (
        "---\ntitle: short\ndate: 2026-08-01\n---\n\n"
        + "\n\n".join(_sentence(index) for index in range(13))
    )
    with pytest.raises(ValueError, match="必要14件以上"):
        build_deepdive_dialogue_script.build_dialogue_markdown(
            source,
            source_name="digest/DeepDive/2026-08-01-DeepDive.md",
        )


def test_past_month_dialogue_corpus_has_no_cross_day_template_loop() -> None:
    root = Path(__file__).resolve().parents[1]
    paths = [
        path
        for path in sorted((root / "digest" / "DeepDive").glob("*-DeepDive-dialogue.md"))
        if "2026-07-02" <= path.name[:10] <= "2026-08-01"
    ]
    result = deepdive_dialogue.audit_dialogue_corpus(paths)
    assert result["script_count"] == 31
    assert result["issues"] == []
    assert result["repeated_turn_rate"] <= 0.10
    assert result["maximum_cross_script_similarity"] <= 0.45
