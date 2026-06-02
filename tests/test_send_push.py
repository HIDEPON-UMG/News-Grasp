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

import tools.send_push as sp  # noqa: E402
from tools.send_push import (  # noqa: E402
    build_payload,
    categories_for_weekday,
    default_body_for_today,
    load_subscriptions,
    load_subscriptions_from_worker,
    main,
    resolve_token,
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


def test_schedule_matches_routine_matrix():
    """配信曜日マトリクスが routine-system.md と一致（経済=平日のみ / ゲーム=火木土日）。

    ここがズレると通知が「配信していないカテゴリ」を約束してしまう。
    weekday(): 月=0 ... 日=6。
    """
    everyday = {"為替", "AI", "IT", "モビリティ"}
    for wd in range(7):
        cats = set(categories_for_weekday(wd))
        assert everyday <= cats, f"曜日{wd}: 毎日カテゴリが欠けている"
    # 経済: 平日(0-4)のみ
    for wd in (0, 1, 2, 3, 4):
        assert "経済" in categories_for_weekday(wd)
    for wd in (5, 6):
        assert "経済" not in categories_for_weekday(wd)
    # ゲーム: 火(1)木(3)土(5)日(6)のみ
    for wd in (1, 3, 5, 6):
        assert "ゲーム" in categories_for_weekday(wd)
    for wd in (0, 2, 4):
        assert "ゲーム" not in categories_for_weekday(wd)


def test_default_body_value_phrasing_and_order():
    """本文は配信順で並び『…の最新情報をまとめています。』で締める（価値訴求型）。"""
    body_tue = default_body_for_today(1)  # 火 = 全 6 カテゴリ
    assert body_tue == "為替・AI・IT・モビリティ・経済・ゲームの最新情報をまとめています。"
    body_sat = default_body_for_today(5)  # 土 = 経済なし・ゲームあり
    assert body_sat == "為替・AI・IT・モビリティ・ゲームの最新情報をまとめています。"
    assert body_sat.endswith("の最新情報をまとめています。")


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
    no_token = tmp_path / "no_token.txt"  # token 無し → Worker でなく file 経路を強制
    monkeypatch.setattr(
        sys, "argv",
        ["send_push.py",
         "--subscriptions-file", str(empty),
         "--token-file", str(no_token)],
    )
    assert main() == 0
    out = capsys.readouterr().out
    assert "0 件" in out or "0 人" in out


def test_main_dry_run_does_not_send(tmp_path, monkeypatch, capsys):
    """購読者がいても --dry-run なら送信処理に入らず exit 0。"""
    f = tmp_path / "subs.json"
    f.write_text(json.dumps([SAMPLE_SUB]), encoding="utf-8")
    no_token = tmp_path / "no_token.txt"  # token 無し → file 経路を強制
    monkeypatch.setattr(
        sys, "argv",
        ["send_push.py", "--dry-run",
         "--subscriptions-file", str(f),
         "--token-file", str(no_token)],
    )
    assert main() == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out


# --- 送信契約: TTL ---------------------------------------------------------

def test_send_one_passes_positive_ttl(monkeypatch):
    """webpush へ必ず正の TTL を渡す（TTL=0 を禁ずる）不変条件。

    なぜ重要か: push サービス（FCM/APNs）は TTL=0 を「送信時に端末がオンラインで
    なければ破棄」と解釈し、しかも送信側には 201 を返す。朝のスリープ中端末では
    毎朝 silently 破棄され「送信成功なのに通知が来ない」が再発する（2026-06-01 実測）。
    pywebpush の既定 ttl=0 に戻ると本テストが落ちる。
    """
    import pywebpush

    captured = {}

    def fake_webpush(**kwargs):
        captured.update(kwargs)
        return object()

    # send_one は関数内で `from pywebpush import webpush` するため、モジュール属性を差し替える
    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)
    ok, gone, detail = sp.send_one(SAMPLE_SUB, '{"title":"t"}', "key.pem", "mailto:a@b.c")
    assert ok is True and gone is False
    assert captured.get("ttl", 0) > 0, "webpush に正の TTL を渡していない（TTL=0 はオフライン端末で破棄される）"


def test_send_one_passes_high_urgency(monkeypatch):
    """webpush へ必ず Urgency: high を渡す不変条件。

    なぜ重要か: Urgency 未指定（pywebpush 既定）は "normal" 扱いとなり、端末 OS が
    省電力状態のとき配信が先送りされる（Android Doze はメンテナンス窓までバッチ、
    iOS は apns-priority 5）。朝 06:38 は端末が一晩アイドルで Doze/低電力に入っており、
    FCM/APNs が 201 受理しても **端末側で通知が出ない**「送信成功なのに来ない」が
    起きる（日中の手動送信は端末がアクティブなので即届く＝非対称性の正体）。
    "high" は FCM 優先度 high / apns-priority 10 にマップされ Doze を貫通する。
    normal に戻る退行をここで封じる。
    """
    import pywebpush

    captured = {}

    def fake_webpush(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)
    ok, gone, detail = sp.send_one(SAMPLE_SUB, '{"title":"t"}', "key.pem", "mailto:a@b.c")
    assert ok is True and gone is False
    headers = captured.get("headers") or {}
    assert headers.get("Urgency") == "high", (
        "webpush に Urgency: high を渡していない（normal は省電力端末で配信が先送りされる）"
    )


# --- Worker 連携 ---------------------------------------------------------

def test_resolve_token_missing_and_empty_and_value(tmp_path):
    assert resolve_token(str(tmp_path / "nope.txt")) is None
    empty = tmp_path / "t.txt"
    empty.write_text("   ", encoding="utf-8")
    assert resolve_token(str(empty)) is None
    real = tmp_path / "t2.txt"
    real.write_text("  secret-token\n", encoding="utf-8")
    assert resolve_token(str(real)) == "secret-token"


def test_load_subscriptions_from_worker(monkeypatch):
    """Worker /list の戻りを購読配列として読み、token を URL に載せる。"""
    captured = {}

    def fake_get(url, timeout=10):
        captured["url"] = url
        return [SAMPLE_SUB]

    monkeypatch.setattr(sp, "_http_get_json", fake_get)
    subs = load_subscriptions_from_worker("https://w.example.dev", "tok en/+")
    assert len(subs) == 1 and subs[0]["endpoint"] == SAMPLE_SUB["endpoint"]
    # token は URL エンコードされて /list に載る
    assert captured["url"].startswith("https://w.example.dev/list?token=")
    assert "tok%20en" in captured["url"], "token が URL エンコードされていない"


def test_main_prefers_worker_when_configured(tmp_path, monkeypatch, capsys):
    """worker-url と token が揃えば Worker を取得元に選ぶ（dry-run で送信せず確認）。"""
    tok = tmp_path / "tok.txt"
    tok.write_text("abc123", encoding="utf-8")
    monkeypatch.setattr(sp, "_http_get_json", lambda url, timeout=10: [SAMPLE_SUB])
    monkeypatch.setattr(
        sys, "argv",
        ["send_push.py", "--dry-run",
         "--worker-url", "https://w.example.dev",
         "--token-file", str(tok)],
    )
    assert main() == 0
    out = capsys.readouterr().out
    assert "取得元:   worker" in out
    assert "購読者:   1 件" in out
