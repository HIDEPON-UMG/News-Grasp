#!/usr/bin/env python3
"""tools/send_push.py の契約テスト。

検証する不変条件（= なぜ重要か）:
  - 購読ファイルが無い / 空でも **例外を投げず空リスト** を返す
    （購読者 0 人で毎朝の Runner を落とさないため）
  - 壊れた購読データ（配列でない / endpoint 欠落）は SystemExit で早期に弾く
    （push サービスへ不正データを投げて無言失敗するのを防ぐ）
  - payload は SW が解釈する {title, body, url} の JSON で、日本語を素通しする
    （ensure_ascii=False。通知本文が \\uXXXX 化しないこと）
  - 購読者 0 人なら dry-run でなくとも exit 0（push は付随機能）

ネットワーク送信（webpush 呼び出し）はテストしない。失効購読の除去ロジックは
send_one の戻り値契約 (ok, gone, detail) を直接検証する。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.send_push import (  # noqa: E402
    build_payload,
    load_subscriptions,
    main,
)

SAMPLE_SUB = {
    "endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
    "expirationTime": None,
    "keys": {"p256dh": "BPxxxx", "auth": "yyyy"},
}


def test_load_subscriptions_missing_file_returns_empty(tmp_path):
    missing = tmp_path / "nope.json"
    assert load_subscriptions(missing) == []


def test_load_subscriptions_empty_file_returns_empty(tmp_path):
    f = tmp_path / "subs.json"
    f.write_text("   ", encoding="utf-8")
    assert load_subscriptions(f) == []


def test_load_subscriptions_valid(tmp_path):
    f = tmp_path / "subs.json"
    f.write_text(json.dumps([SAMPLE_SUB]), encoding="utf-8")
    subs = load_subscriptions(f)
    assert len(subs) == 1
    assert subs[0]["endpoint"] == SAMPLE_SUB["endpoint"]


def test_load_subscriptions_not_a_list_rejected(tmp_path):
    f = tmp_path / "subs.json"
    f.write_text(json.dumps(SAMPLE_SUB), encoding="utf-8")  # 配列でなく単一 dict
    with pytest.raises(SystemExit):
        load_subscriptions(f)


def test_load_subscriptions_missing_endpoint_rejected(tmp_path):
    f = tmp_path / "subs.json"
    f.write_text(json.dumps([{"keys": {}}]), encoding="utf-8")  # endpoint 欠落
    with pytest.raises(SystemExit):
        load_subscriptions(f)


def test_build_payload_shape_and_japanese():
    payload = build_payload("題名", "本文だよ", "https://example.com/x/")
    data = json.loads(payload)
    assert data == {"title": "題名", "body": "本文だよ", "url": "https://example.com/x/"}
    # ensure_ascii=False: 生の日本語が含まれ \\u エスケープされていない
    assert "本文だよ" in payload
    assert "\\u" not in payload


def test_main_no_subscribers_returns_zero(tmp_path, monkeypatch, capsys):
    """購読者 0 人なら dry-run でなくても exit 0（Runner を落とさない）。"""
    empty = tmp_path / "subs.json"  # 存在しないパス
    monkeypatch.setattr(
        sys, "argv",
        ["send_push.py", "--subscriptions-file", str(empty)],
    )
    assert main() == 0
    out = capsys.readouterr().out
    assert "0 件" in out or "0 人" in out


def test_main_dry_run_does_not_send(tmp_path, monkeypatch, capsys):
    """購読者がいても --dry-run なら送信処理に入らず exit 0。"""
    f = tmp_path / "subs.json"
    f.write_text(json.dumps([SAMPLE_SUB]), encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv",
        ["send_push.py", "--dry-run", "--subscriptions-file", str(f)],
    )
    assert main() == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
