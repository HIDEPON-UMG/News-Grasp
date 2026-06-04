#!/usr/bin/env python3
"""`tools/dedup.py --followup-gate` 契約テスト (続報ゲートの機械化版)。

# 検証する「なぜ重要か」

2026-06-05 朝バッチで AI カテゴリのトップ記事「Anthropic confidentially files IPO
prospectus with SEC」(英語) が、2026-06-03 で扱った「Anthropic、SECに機密S-1を提出し
IPO申請」(日本語) と**同一イベント**だったのに再採用された。

真因: routine-system.md 3-A.5 E (続報ゲート・「前回掲載時から新材料があるか」確認)
が **LLM の意味判断任せ**で、機械的に強制されていなかった。LLM が「Mythos model
coming in weeks」を新材料と判断したが実は 06-03 で既出。

本テストは ``feedback_check_design_principles`` の Lv2 境界 1 箇所集約として、
``dedup_candidates(followup_gate=True)`` が以下を保証することを locked-in する:

  1. 続報候補 (24h 超のタイトル類似マッチ) で**新規 token 0 個** → 落とす (fatal)
  2. 続報候補で**新規 token 1 個以上** → 通過 + ``followup_new_words`` に記録
  3. ``followup_gate=False`` (既定) のときは従来挙動 (= 続報は全て通過) を維持
  4. URL 完全一致 (= 同一記事) は経過時間に関係なく常に除外 (既存仕様維持)
  5. 24h 以内のタイトル類似マッチは ``followup_gate`` の有無に関係なく除外 (既存仕様維持)

実行:
  pytest tests/test_dedup_followup_gate.py -v
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from dedup import dedup_candidates  # noqa: E402

JST = timezone(timedelta(hours=9))


def _existing(date: str, title: str, url: str, summary: str = "") -> dict:
    """既存 articles.jsonl エントリ模擬。"""
    return {
        "date": date,
        "seen_at": f"{date}T06:30:00+09:00",
        "title": title,
        "url": url,
        "summary": summary,
        "genre": "AI",
    }


def _candidate(title: str, url: str, summary: str = "") -> dict:
    return {"title": title, "url": url, "summary": summary, "score": 90}


# ── 続報ゲートの核心契約 (機械化された E ゲート) ─────────────────────────────


def test_followup_gate_drops_when_no_new_material():
    """続報候補で新規 token 0 個 → 落とす契約 (06-05 Anthropic 再採用事故の再発防止)。

    cross-language B2 マッチには英字固有名詞 3 個以上の共通が必要なので、両 entry に
    Anthropic / SEC / IPO / S-1 由来の英字語を充足させた上で「新材料が出てこない」
    パラフレーズを用意する。
    """
    today = datetime.now(JST)
    two_days_ago = (today - timedelta(days=2)).strftime("%Y-%m-%d")
    existing = [_existing(
        date=two_days_ago,
        title="Anthropic SEC confidentially files IPO prospectus valuation",
        url="https://example.com/anthropic-ipo-jp",
        summary="Anthropic SEC IPO confidential filing prospectus valuation 965 billion",
    )]
    candidate = _candidate(
        # 動詞・名詞は existing と完全一致 (= 新材料無し)、語順だけ変えた純パラフレーズ
        title="Anthropic confidentially files SEC IPO prospectus valuation",
        url="https://example.com/anthropic-ipo-en",  # 別 URL (= 別ソース)
        summary="Anthropic SEC IPO confidential files prospectus valuation 965 billion",
    )

    passed, dropped = dedup_candidates(
        [candidate], existing, followup_gate=True,
    )

    assert len(passed) == 0, (
        f"新材料無しの続報は落ちる契約。passed={passed} dropped={dropped}"
    )
    assert len(dropped) == 1
    assert "新材料 0" in dropped[0].get("dedup_reason", ""), (
        f"dedup_reason に新材料 0 の旨が含まれるはず: {dropped[0].get('dedup_reason')}"
    )


def test_followup_gate_passes_when_new_material_present():
    """続報候補で新規 token 1 個以上 → 通過する契約。"""
    today = datetime.now(JST)
    two_days_ago = (today - timedelta(days=2)).strftime("%Y-%m-%d")
    existing = [_existing(
        date=two_days_ago,
        title="Waymo Tesla NHTSA Phoenix robotaxi safety expansion report",
        url="https://example.com/waymo-phoenix",
        summary="Waymo Tesla NHTSA Phoenix robotaxi safety expansion",
    )]
    # 新規 (= candidate のみが持つ token): Tokyo, London, launch
    candidate = _candidate(
        title="Waymo Tesla NHTSA Tokyo London robotaxi launch announcement",
        url="https://example.com/waymo-tokyo-london",
        summary="Waymo Tesla NHTSA Tokyo London robotaxi launch announcement",
    )

    passed, dropped = dedup_candidates(
        [candidate], existing, followup_gate=True,
    )

    assert len(passed) == 1, (
        f"新材料ありの続報は通過する契約。dropped={dropped} passed={passed}"
    )
    assert len(dropped) == 0
    new_words = passed[0].get("followup_new_words", [])
    assert "tokyo" in new_words or "london" in new_words, (
        f"新規 token に tokyo/london が記録されるはず: {new_words}"
    )


# ── 既存仕様の維持 (followup_gate=False で挙動を変えない) ────────────────────


def test_default_behavior_keeps_followup_through():
    """followup_gate=False (既定) のとき従来挙動 (24h 超続報は全て通過) を維持。

    B2 cross-language マッチには英字固有名詞 3 個以上の共通が必要なため、両方に
    Anthropic / SEC / IPO / S-1 / confidential などを充足させる。
    """
    today = datetime.now(JST)
    two_days_ago = (today - timedelta(days=2)).strftime("%Y-%m-%d")
    existing = [_existing(
        date=two_days_ago,
        title="Anthropic SEC confidentially files IPO prospectus S-1 valuation",
        url="https://example.com/a",
        summary="Anthropic SEC IPO confidential filing prospectus",
    )]
    candidate = _candidate(
        title="Anthropic confidentially submits IPO SEC prospectus filing",
        url="https://example.com/b",
        summary="Anthropic SEC IPO confidential prospectus filing",
    )

    passed, dropped = dedup_candidates(
        [candidate], existing,  # followup_gate 未指定 = False
    )
    # 既定では続報は通過する (= 既存挙動を変えない)
    assert len(passed) == 1, (
        f"followup_gate=False では従来通り続報は通過する契約。dropped={dropped}"
    )
    assert passed[0].get("is_followup") is True


def test_url_match_still_dropped_regardless_of_gate():
    """URL 完全一致 (= 同一記事) は followup_gate に関係なく常に除外する既存契約。"""
    today = datetime.now(JST)
    two_days_ago = (today - timedelta(days=2)).strftime("%Y-%m-%d")
    existing = [_existing(
        date=two_days_ago,
        title="Old story",
        url="https://example.com/same",
    )]
    candidate = _candidate(
        title="Same story (with new framing)",
        url="https://example.com/same",  # 同じ URL
    )

    for gate in (True, False):
        passed, dropped = dedup_candidates(
            [candidate], existing, followup_gate=gate,
        )
        assert len(passed) == 0, f"URL 一致は常に除外 (gate={gate})"
        assert "url match" in dropped[0].get("dedup_reason", "")


def test_within_24h_match_still_dropped():
    """24h 以内のタイトル類似は followup_gate に関係なく常に除外する既存契約。"""
    today = datetime.now(JST)
    existing = [{
        "date": today.strftime("%Y-%m-%d"),
        "seen_at": (today - timedelta(hours=3)).isoformat(),
        "title": "Anthropic files S-1 confidentially",
        "url": "https://example.com/a",
        "summary": "Anthropic SEC IPO",
    }]
    candidate = _candidate(
        title="Anthropic confidentially submits IPO papers to SEC",
        url="https://example.com/b",
        summary="Anthropic SEC IPO",
    )

    for gate in (True, False):
        passed, dropped = dedup_candidates(
            [candidate], existing, followup_gate=gate,
        )
        assert len(passed) == 0, f"24h 以内は常に除外 (gate={gate})"
