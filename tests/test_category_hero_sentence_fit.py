#!/usr/bin/env python3
"""カテゴリートップヒーローの文単位要約 contract tests。"""
from __future__ import annotations

from tools.generate_pages import fit_to_sentences


def test_fit_to_sentences_keeps_single_sentence_complete_without_more() -> None:
    result = fit_to_sentences("ドル円は高止まりした。", max_chars=104)

    assert result["body"] == "ドル円は高止まりした。"
    assert result["bullets"] == ["ドル円は高止まりした。"]
    assert result["has_more"] is False
    assert all(b.endswith("。") for b in result["bullets"])
    assert "…" not in result["body"]


def test_fit_to_sentences_keeps_two_sentences_complete_without_more() -> None:
    text = "ドル円は高止まりした。米金利の見通しが焦点になった。"
    result = fit_to_sentences(text, max_chars=104)

    assert result["body"] == text
    assert result["bullets"] == ["ドル円は高止まりした。", "米金利の見通しが焦点になった。"]
    assert result["has_more"] is False
    assert result["body"].endswith("。")
    assert "…" not in result["body"]


def test_fit_to_sentences_overflow_shows_more_without_mid_sentence_cut() -> None:
    text = (
        "ドル円は高止まりした。"
        "米金利の見通しが焦点になった。"
        "日銀の利上げ観測と米利下げ時期が交差し、来週の指標で方向感が変わる。"
    )
    result = fit_to_sentences(text, max_chars=31)

    assert result["body"] == "ドル円は高止まりした。米金利の見通しが焦点になった。"
    assert result["bullets"] == ["ドル円は高止まりした。", "米金利の見通しが焦点になった。"]
    assert result["has_more"] is True
    assert result["body"].endswith("。")
    assert "…" not in result["body"]


def test_fit_to_sentences_keeps_first_sentence_even_when_over_budget() -> None:
    text = "日米金利差と介入警戒が同時に意識される長い一文でも、文中では切らず句点まで表示する。次の文は続きを読むへ回す。"
    result = fit_to_sentences(text, max_chars=12)

    assert result["bullets"] == ["日米金利差と介入警戒が同時に意識される長い一文でも、文中では切らず句点まで表示する。"]
    assert result["has_more"] is True
    assert result["body"].endswith("。")
    assert "…" not in result["body"]
