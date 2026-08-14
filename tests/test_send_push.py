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
    # 製造: 平日(0-4)のみ
    for wd in (0, 1, 2, 3, 4):
        assert "製造" in categories_for_weekday(wd)
    for wd in (5, 6):
        assert "製造" not in categories_for_weekday(wd)


def test_default_body_value_phrasing_and_order():
    """本文は配信順で並び『…の最新情報をまとめています。』で締める（価値訴求型）。"""
    body_tue = default_body_for_today(1)  # 火 = 全 7 カテゴリ
    assert body_tue == "為替・AI・IT・モビリティ・製造・経済・ゲームの最新情報をまとめています。"
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


def test_main_no_subscribers_records_machine_readable_state(tmp_path, monkeypatch, capsys):
    """購読者 0 人も completion proof に載せられる notification state を残す。"""
    empty = tmp_path / "subs.json"
    state = tmp_path / "notification.json"
    no_token = tmp_path / "no_token.txt"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "send_push.py",
            "--subscriptions-file",
            str(empty),
            "--token-file",
            str(no_token),
            "--record-state",
            str(state),
        ],
    )

    assert main() == 0
    capsys.readouterr()
    payload = json.loads(state.read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == "NEWS_GRASP_NOTIFICATION_OUTCOME_V2"
    assert payload["status"] == "no_subscribers"
    assert payload["ok"] is True
    assert payload["subscription_count"] == 0
    assert payload["sent_count"] == 0
    assert payload["audienceResolutionReceipt"]["resolvedAudienceCount"] == 0
    assert payload["audienceResolutionReceipt"]["receiptSha256"]


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


def test_main_dry_run_records_machine_readable_state(tmp_path, monkeypatch, capsys):
    """dry-run も publish complete へ誤昇格しない状態として記録する。"""
    f = tmp_path / "subs.json"
    f.write_text(json.dumps([SAMPLE_SUB]), encoding="utf-8")
    state = tmp_path / "notification.json"
    no_token = tmp_path / "no_token.txt"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "send_push.py",
            "--dry-run",
            "--subscriptions-file",
            str(f),
            "--token-file",
            str(no_token),
            "--record-state",
            str(state),
        ],
    )

    assert main() == 0
    capsys.readouterr()
    payload = json.loads(state.read_text(encoding="utf-8"))
    assert payload["status"] == "dry_run"
    assert payload["ok"] is False
    assert payload["subscription_count"] == 1


def test_main_records_sealed_delivery_receipt_only_when_every_target_accepts(
    tmp_path, monkeypatch, capsys
):
    subscriptions = tmp_path / "subs.json"
    subscriptions.write_text(json.dumps([SAMPLE_SUB]), encoding="utf-8")
    key = tmp_path / "vapid.pem"
    key.write_text("test-key", encoding="utf-8")
    state = tmp_path / "notification.json"
    monkeypatch.setattr(sp, "send_one", lambda *_args: (True, False, "ok"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "send_push.py",
            "--subscriptions-file",
            str(subscriptions),
            "--token-file",
            str(tmp_path / "no-token"),
            "--vapid-key-file",
            str(key),
            "--record-state",
            str(state),
        ],
    )

    assert main() == 0
    capsys.readouterr()
    payload = json.loads(state.read_text(encoding="utf-8"))
    assert payload["status"] == "sent"
    assert payload["ok"] is True
    assert payload["deliveryReceipt"]["targetCount"] == 1
    assert payload["deliveryReceipt"]["acceptedCount"] == 1
    assert payload["deliveryReceipt"]["receiptSha256"]


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


# --- fallback 公開中の push 抑止 (2026-06-12 疑義 C) --------------------------

def test_publish_status_is_fallback_pure(tmp_path):
    """当日 fallback 公開中のみ True を返す純関数契約。

    なぜ重要か: fallback publish は旧号を表示しているため、通常文面の push で誘導すると
    誤誘導になる (2026-06-12 実測で fallback 中も通常 push が飛んだ)。当日 fallback のみ
    抑止し、published_ok / ファイル無し / 壊れた JSON / 別日付の stale fallback では送信を
    止めない (前日の残骸で当日の手動送信を誤抑止しないため)。
    """
    f = tmp_path / "publish-status.json"
    # 当日 fallback → True
    f.write_text(json.dumps({"result": "published_fallback_with_notice", "date": "2026-06-12"}),
                 encoding="utf-8")
    assert sp.publish_status_is_fallback(f, "2026-06-12") is True
    # 別日付の fallback (stale) → False
    assert sp.publish_status_is_fallback(f, "2026-06-13") is False
    # published_ok → False
    f.write_text(json.dumps({"result": "published_ok", "date": "2026-06-12"}), encoding="utf-8")
    assert sp.publish_status_is_fallback(f, "2026-06-12") is False
    # ファイル無し → False
    assert sp.publish_status_is_fallback(tmp_path / "nope.json", "2026-06-12") is False
    # 壊れた JSON → False
    f.write_text("{ broken", encoding="utf-8")
    assert sp.publish_status_is_fallback(f, "2026-06-12") is False


def test_main_suppresses_push_during_fallback(tmp_path, monkeypatch, capsys):
    """当日 fallback 公開中は購読者がいても send_one を呼ばず exit 0 (疑義 C)。

    なぜ重要か: 2026-06-12 実測で fallback 中も通常文面の push が飛び、品質確認中の旧号へ
    誤誘導した。fallback 状態を読んで送信を抑止し、成功公開 (mark-ok で published_ok) 後に
    だけ通常 push が飛ぶことを locked-in する。
    """
    f = tmp_path / "subs.json"
    f.write_text(json.dumps([SAMPLE_SUB]), encoding="utf-8")
    no_token = tmp_path / "no_token.txt"  # token 無し → file 経路を強制
    status = tmp_path / "publish-status.json"
    today = sp._today_jst_str()
    status.write_text(
        json.dumps({"result": "published_fallback_with_notice", "date": today}),
        encoding="utf-8",
    )
    monkeypatch.setattr(sp, "PUBLISH_STATUS_FILE", status)
    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        return True, False, "ok"

    monkeypatch.setattr(sp, "send_one", _boom)
    monkeypatch.setattr(
        sys, "argv",
        ["send_push.py",
         "--subscriptions-file", str(f),
         "--token-file", str(no_token),
         "--vapid-key-file", str(tmp_path / "key.pem")],
    )
    assert main() == 0
    out = capsys.readouterr().out
    assert "抑止" in out
    assert called["n"] == 0, "fallback 中に send_one が呼ばれた (抑止が効いていない)"


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
