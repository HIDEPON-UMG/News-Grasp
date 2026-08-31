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


def test_source_evidence_sentences_strip_internal_transport_metadata() -> None:
    source = (
        "---\ntitle: metadata\n---\n\n"
        '<!-- claim-source: {"claim":"内部だけの主張です。",'
        '"sourceUrl":"https://example.com/private",'
        '"evidence":"内部だけの根拠です。"} -->\n\n'
        "<!-- value:current_signal evidence:source:0 support:source:7 -->\n\n"
        "公開本文では対象企業が2026年8月に新条件を公表しました。\n"
    )

    evidence = deepdive_dialogue.source_evidence_sentences(source)
    joined = "\n".join(evidence)

    assert "公開本文では対象企業が2026年8月に新条件を公表しました。" in evidence
    assert "claim-source" not in joined
    assert "sourceUrl" not in joined
    assert "value:current_signal" not in joined


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
        senior_text = (
            f"{evidence} {support} 担当編集者が次回公開前に一次資料との差分表を更新する。"
            if value_id == "next_action"
            else f"{evidence} {support} {value_id}では二つを分けて確認する。"
        )
        blocks.append(
            f"{marker}\n"
            f"若手: {evidence} この観点をどう読みますか。\n\n"
            f"先輩: {senior_text}"
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


def test_dialogue_quality_has_no_minimum_character_padding_gate() -> None:
    turns: list[deepdive_dialogue.DialogueTurn] = []
    for index, value_id in enumerate(deepdive_dialogue.REQUIRED_VALUE_IDS):
        turns.extend(
            (
                deepdive_dialogue.DialogueTurn(
                    "junior",
                    f"論点{index}を確認します。",
                    value_id,
                    f"source:{index}",
                    f"source:{index + 7}",
                ),
                deepdive_dialogue.DialogueTurn(
                    "senior",
                    f"論点{index}の固有差は条件{index}だ。",
                    value_id,
                    f"source:{index}",
                    f"source:{index + 7}",
                ),
            )
        )

    issues = deepdive_dialogue.validate_dialogue(turns)

    assert not any("字数不足" in issue for issue in issues)


def test_value_contract_allows_variable_turn_count_with_both_roles() -> None:
    turns = deepdive_dialogue.parse_dialogue(_document())
    first = turns[0]
    turns.insert(
        1,
        deepdive_dialogue.DialogueTurn(
            "junior",
            "その変化が前提をどう動かすかも確認します。",
            first.value_id,
            first.evidence_id,
            first.support_id,
        ),
    )

    issues = deepdive_dialogue.validate_value_contract(turns)

    assert not any("セリフ数違反" in issue for issue in issues)


def test_dialogue_persona_rejects_polite_senior_and_plain_junior() -> None:
    turns = [
        deepdive_dialogue.DialogueTurn("junior", "何を確認する。"),
        deepdive_dialogue.DialogueTurn("senior", "この条件を確認します。"),
    ]

    issues = deepdive_dialogue.validate_dialogue(turns)

    assert any("若手口調違反" in issue for issue in issues)
    assert any("先輩口調違反" in issue for issue in issues)


@pytest.mark.parametrize(
    "fragment",
    [
        "https://example.com/private",
        '{"sourceUrl":"https://example.com/private"}',
        "```json",
        "[内部リンク](https://example.com/private)",
        "<!-- claim-source: secret -->",
    ],
)
def test_dialogue_utterance_rejects_internal_url_json_markdown_fragments(
    fragment: str,
) -> None:
    turns = [
        deepdive_dialogue.DialogueTurn("junior", "確認します。"),
        deepdive_dialogue.DialogueTurn("senior", f"内部断片は出さない。{fragment}"),
    ]

    issues = deepdive_dialogue.validate_dialogue(turns)

    assert any("発話内部断片" in issue for issue in issues)


def test_next_action_requires_actor_action_artifact_and_trigger() -> None:
    text = _document().replace(
        "担当編集者が次回公開前に一次資料との差分表を更新する。",
        "追加で確認する。",
    )

    issues = deepdive_dialogue.validate_dialogue_document(text)

    assert any("next_action具体性不足" in issue for issue in issues)


def test_legacy_deterministic_generator_is_not_v2_valid(tmp_path: Path) -> None:
    source = _source()
    source_path = tmp_path / "digest" / "DeepDive" / "2026-08-01-DeepDive.md"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source, encoding="utf-8")
    output = source_path.with_name("2026-08-01-DeepDive-dialogue.md")
    output.write_bytes(b"existing dialogue must remain untouched\n")
    before_source = source_path.read_bytes()
    before_output = output.read_bytes()

    with pytest.raises(
        build_deepdive_dialogue_script.DeepDiveDialogueGenerationRequired
    ) as markdown_caught:
        build_deepdive_dialogue_script.build_dialogue_markdown(
            source,
            source_name="digest/DeepDive/2026-08-01-DeepDive.md",
        )

    with pytest.raises(
        build_deepdive_dialogue_script.DeepDiveDialogueGenerationRequired
    ) as script_caught:
        build_deepdive_dialogue_script.build_dialogue_script(
            source_path,
            output=output,
        )

    assert str(markdown_caught.value) == build_deepdive_dialogue_script.DEEPDIVE_LLM_DIALOGUE_REQUIRED
    assert str(script_caught.value) == build_deepdive_dialogue_script.DEEPDIVE_LLM_DIALOGUE_REQUIRED
    assert source_path.read_bytes() == before_source
    assert output.read_bytes() == before_output


def test_value_contract_requires_exact_ordered_value_ids() -> None:
    turns = deepdive_dialogue.parse_dialogue(_document(omit="causal_chain"))
    issues = deepdive_dialogue.validate_value_contract(turns)
    assert any("価値ID" in issue and "causal_chain" in issue for issue in issues)


def test_value_contract_rejects_duplicate_value_segment() -> None:
    issues = deepdive_dialogue.validate_dialogue_document(
        _document(duplicate="evidence")
    )
    assert any("重複セリフ" in issue or "反復ブロック" in issue for issue in issues)


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


def test_generator_rejects_source_with_fewer_than_fourteen_facts(tmp_path: Path) -> None:
    source = (
        "---\ntitle: short\ndate: 2026-08-01\n---\n\n"
        + "\n\n".join(_sentence(index) for index in range(13))
    )
    source_path = tmp_path / "digest" / "DeepDive" / "2026-08-01-DeepDive.md"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source, encoding="utf-8")
    output = source_path.with_name("2026-08-01-DeepDive-dialogue.md")
    output.write_bytes(b"existing short-source output\n")
    before_source = source_path.read_bytes()
    before_output = output.read_bytes()

    with pytest.raises(
        build_deepdive_dialogue_script.DeepDiveDialogueGenerationRequired
    ) as markdown_caught:
        build_deepdive_dialogue_script.build_dialogue_markdown(
            source,
            source_name="digest/DeepDive/2026-08-01-DeepDive.md",
        )

    with pytest.raises(
        build_deepdive_dialogue_script.DeepDiveDialogueGenerationRequired
    ) as script_caught:
        build_deepdive_dialogue_script.build_dialogue_script(
            source_path,
            output=output,
        )

    assert str(markdown_caught.value) == build_deepdive_dialogue_script.DEEPDIVE_LLM_DIALOGUE_REQUIRED
    assert str(script_caught.value) == build_deepdive_dialogue_script.DEEPDIVE_LLM_DIALOGUE_REQUIRED
    assert source_path.read_bytes() == before_source
    assert output.read_bytes() == before_output


def test_past_month_legacy_dialogue_corpus_is_v2_red_until_remediation() -> None:
    root = Path(__file__).resolve().parents[1]
    paths = [
        path
        for path in sorted((root / "digest" / "DeepDive").glob("*-DeepDive-dialogue.md"))
        if "2026-07-02" <= path.name[:10] <= "2026-08-01"
    ]
    result = deepdive_dialogue.audit_dialogue_corpus(paths)
    assert result["script_count"] == 31
    assert result["issues"]
    assert any("先輩口調違反" in issue for issue in result["issues"])
