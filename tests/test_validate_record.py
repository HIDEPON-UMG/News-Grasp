#!/usr/bin/env python3
"""tools/validate_record.py の契約テスト (Plan v3 P0-B Lv4)。

`validate_record()` が次の 4 つの境界違反を raise することを locked-in:

  1. `thumb` 値が int 等の非 str/None → RecordSchemaError
  2. `date` が `YYYY-MM-DD` でない (例 `2026-13-99`) → RecordSchemaError
  3. `url` キー欠落 → RecordSchemaError
  4. 既存 23 件補修済み canonical record → 通過

これらは「2026-06-06 23 件 thumb 欠落」と同 class of bugs を pin する。
本テストが赤になったら append 経路 (Python script でも claude 直 append でも) を
再点検する。
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.validate_record import (  # noqa: E402
    RecordSchemaError,
    validate_jsonl,
    validate_record,
)


def _canonical() -> dict:
    """既存 23 件補修済みフォーマットの正規 record (= 通過することを期待)。"""
    return {
        "date": "2026-06-05",
        "seen_at": "2026-06-05T06:00:00+09:00",
        "genre": "AI",
        "title": "Anthropic Claude 4.8 公開",
        "url": "https://www.anthropic.com/news/claude-4-8",
        "url_norm": "anthropic.com/news/claude-4-8",
        "source": "Anthropic",
        "summary": "Opus 4.8 / Sonnet 4.6 を公開。",
        "thumb": "https://www.anthropic.com/og.png",
        "entities": {
            "companies": ["Anthropic"], "countries": ["米国"],
            "services": ["Claude"], "people": [], "tickers": [],
        },
        "topics": ["AI"],
        "industries": ["AI"],
        "events": ["製品発表"],
        "tags": ["co/Anthropic", "topic/AI", "score/高"],
    }


def test_validate_record_rejects_int_thumb():
    """thumb=123 (int) は RecordSchemaError で弾く (class of bugs: 型ドリフト)。"""
    rec = _canonical()
    rec["thumb"] = 123
    with pytest.raises(RecordSchemaError) as ei:
        validate_record(rec)
    assert "thumb" in str(ei.value)


def test_validate_record_rejects_invalid_date():
    """date='2026-13-99' (形式不正) は RecordSchemaError で弾く。"""
    rec = _canonical()
    rec["date"] = "2026-13-99"
    with pytest.raises(RecordSchemaError) as ei:
        validate_record(rec)
    assert "date" in str(ei.value)


def test_validate_record_rejects_missing_url():
    """url キー欠落は RecordSchemaError で弾く (必須キー)。"""
    rec = _canonical()
    del rec["url"]
    with pytest.raises(RecordSchemaError) as ei:
        validate_record(rec)
    assert "url" in str(ei.value)


def test_validate_record_accepts_canonical_record():
    """既存 23 件補修済み canonical record は通過する (= 後方互換)。"""
    validate_record(_canonical())  # raise しないことが契約


def test_validate_record_rejects_missing_thumb_key():
    """thumb キー欠落 (値 None 以外) は RecordSchemaError (2026-06-06 事故の真因)。"""
    rec = _canonical()
    del rec["thumb"]
    with pytest.raises(RecordSchemaError) as ei:
        validate_record(rec)
    assert "thumb" in str(ei.value)


def test_validate_record_accepts_thumb_none():
    """thumb=None (キー存在・値 None) は通過する (URL 取得失敗時の正規表現)。"""
    rec = _canonical()
    rec["thumb"] = None
    validate_record(rec)  # raise しないことが契約


def test_validate_record_rejects_non_https_url():
    """url が 'http(s)://' 以外で始まる場合は RecordSchemaError (ftp:// 等)。"""
    rec = _canonical()
    rec["url"] = "ftp://example.com/x"
    with pytest.raises(RecordSchemaError) as ei:
        validate_record(rec)
    assert "url" in str(ei.value)


def test_validate_jsonl_recent_skips_legacy(tmp_path: Path):
    """validate_jsonl(recent_days=7) は cutoff 前の legacy record を skip する。"""
    import json
    legacy = {"date": "2025-01-01", "title": "legacy", "url": "x"}  # broken legacy
    canonical = _canonical()
    canonical["date"] = "2026-06-05"
    p = tmp_path / "articles.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for r in (legacy, canonical):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 2026-06-06 視点で recent_days=7 なら legacy (2025-01-01) は対象外で PASS
    errs = validate_jsonl(p, recent_days=7, today=date(2026, 6, 6))
    assert errs == [], f"recent 範囲外の legacy で fail してはいけない: {errs}"


def test_validate_jsonl_recent_catches_recent_break(tmp_path: Path):
    """validate_jsonl(recent_days=7) は直近の broken record を必ず検出する。"""
    import json
    broken = _canonical()
    broken["thumb"] = 123  # 直近の record で thumb 型違反
    p = tmp_path / "articles.jsonl"
    p.write_text(json.dumps(broken, ensure_ascii=False) + "\n", encoding="utf-8")

    errs = validate_jsonl(p, recent_days=7, today=date(2026, 6, 6))
    assert len(errs) == 1, f"直近の thumb 型違反 1 件を検出するはず: {errs}"
    assert "thumb" in errs[0]


# ── --issue-date: 号日整合チェック (2026-06-11 21 件誤記事故の機械検査) ────────
#
# articles.jsonl の date は号日 (= digest ファイル名と一致) であって記事公開日ではない。
# 当日生成 (seen_at の日付部分 == 号日) なのに date が号日とズレた record を fatal 化する。


def _issue_record(*, date_v: str, seen_at: str) -> dict:
    """号日整合チェック用に date / seen_at を差し替えた canonical record を返す。"""
    rec = _canonical()
    rec["date"] = date_v
    rec["seen_at"] = seen_at
    return rec


def _write_jsonl(tmp_path: Path, rec: dict) -> Path:
    import json
    p = tmp_path / "articles.jsonl"
    p.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


def test_issue_date_fails_when_seen_today_but_date_mismatch(tmp_path: Path):
    """seen_at が号日 (当日生成) なのに date が号日と違えば fatal。"""
    issue = "2026-06-11"
    # 当日生成 (seen_at=2026-06-11) なのに date=2026-06-10 にズレている = 誤記
    rec = _issue_record(date_v="2026-06-10", seen_at="2026-06-11T06:00:00+09:00")
    p = _write_jsonl(tmp_path, rec)
    errs = validate_jsonl(p, recent_days=None, issue_date=issue)
    assert len(errs) == 1, f"号日不整合 1 件を検出するはず: {errs}"
    assert "号日不整合" in errs[0]


def test_issue_date_passes_when_seen_today_and_date_matches(tmp_path: Path):
    """seen_at が号日かつ date も号日なら PASS。"""
    issue = "2026-06-11"
    rec = _issue_record(date_v="2026-06-11", seen_at="2026-06-11T06:00:00+09:00")
    p = _write_jsonl(tmp_path, rec)
    errs = validate_jsonl(p, recent_days=None, issue_date=issue)
    assert errs == [], f"号日一致なら PASS のはず: {errs}"


def test_issue_date_ignores_past_records(tmp_path: Path):
    """seen_at の日付部分が号日と異なる過去 record は対象外 (date がズレても許容)。"""
    issue = "2026-06-11"
    # seen_at=2026-06-09 (過去生成)。date=2026-06-09 で issue とズレるが当日生成でないので対象外
    rec = _issue_record(date_v="2026-06-09", seen_at="2026-06-09T06:00:00+09:00")
    p = _write_jsonl(tmp_path, rec)
    errs = validate_jsonl(p, recent_days=None, issue_date=issue)
    assert errs == [], f"過去 record は号日チェック対象外のはず: {errs}"


def test_issue_date_unspecified_is_backward_compatible(tmp_path: Path):
    """--issue-date 未指定なら現行挙動と完全に同一 (号日チェックを一切しない)。"""
    # 当日生成 date ズレの record でも issue_date=None なら検出しない
    rec = _issue_record(date_v="2026-06-10", seen_at="2026-06-11T06:00:00+09:00")
    p = _write_jsonl(tmp_path, rec)
    errs = validate_jsonl(p, recent_days=None, issue_date=None)
    assert errs == [], f"issue_date 未指定は号日チェックなし (後方互換): {errs}"


def test_issue_date_works_with_recent_window(tmp_path: Path):
    """--recent との併用が壊れないこと (recent 窓内なら号日チェックも効く)。"""
    import json
    issue = "2026-06-11"
    # recent 窓内 (2026-06-11) の当日生成 date ズレ record
    bad = _issue_record(date_v="2026-06-10", seen_at="2026-06-11T06:00:00+09:00")
    # recent 窓外 (古い) の record は date がズレていても対象外
    old = _issue_record(date_v="2025-01-01", seen_at="2025-01-01T06:00:00+09:00")
    p = tmp_path / "articles.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for r in (old, bad):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    errs = validate_jsonl(p, recent_days=7, today=date(2026, 6, 11), issue_date=issue)
    assert len(errs) == 1, f"recent 窓内の号日不整合 1 件のみ検出するはず: {errs}"
    assert "号日不整合" in errs[0]
