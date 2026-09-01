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
import hashlib
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


def test_sender_source_binding_uses_bounded_origin_main_history_and_blob_size(monkeypatch) -> None:
    calls: list[list[str]] = []
    commit = "a" * 40
    blob_id = "b" * 40

    def fake_run(argv, **kwargs):
        del kwargs
        calls.append(list(argv))
        args = argv[1:]
        if args == ["remote", "get-url", "origin"]:
            return sp.subprocess.CompletedProcess(argv, 0, stdout="https://github.com/HIDEPON-UMG/News-Grasp.git\n", stderr="")
        if args[:2] == ["rev-list", "--max-count=256"]:
            return sp.subprocess.CompletedProcess(argv, 0, stdout=commit + "\n", stderr="")
        if args[:2] == ["rev-parse", "--verify"]:
            return sp.subprocess.CompletedProcess(argv, 0, stdout=blob_id + "\n", stderr="")
        if args == ["cat-file", "-t", blob_id]:
            return sp.subprocess.CompletedProcess(argv, 0, stdout="blob\n", stderr="")
        if args == ["cat-file", "-s", blob_id]:
            return sp.subprocess.CompletedProcess(argv, 0, stdout=str(2 * 1024 * 1024 + 1) + "\n", stderr="")
        raise AssertionError(f"unbounded blob read attempted: {args}")

    monkeypatch.setattr(sp.subprocess, "run", fake_run)
    assert sp._trusted_sender_source_binding("c" * 64) == {}
    flattened = [item for call in calls for item in call]
    assert "--all" not in flattened
    assert "origin/main" in flattened
    assert not any(call[1:3] == ["cat-file", "blob"] for call in calls)


def test_sender_source_binding_has_one_whole_operation_deadline(monkeypatch) -> None:
    calls: list[list[str]] = []
    ticks = iter([0.0, 0.0, 16.0])

    def fake_run(argv, **kwargs):
        del kwargs
        calls.append(list(argv))
        return sp.subprocess.CompletedProcess(argv, 0, stdout="https://github.com/HIDEPON-UMG/News-Grasp.git\n", stderr="")

    monkeypatch.setattr(sp.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(sp.subprocess, "run", fake_run)
    assert sp._trusted_sender_source_binding("d" * 64) == {}
    assert len(calls) == 1


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
    assert payload["status"] == "no_subscribers"
    assert payload["ok"] is True
    assert payload["subscription_count"] == 0
    assert payload["sent_count"] == 0


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
    assert payload["ok"] is True
    assert payload["subscription_count"] == 1


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


def test_delivery_ledger_prevents_duplicate_send_and_seals_prior_chain(
    tmp_path, monkeypatch, capsys
):
    from tools import daily_self_heal

    subscriptions = tmp_path / "subs.json"
    subscriptions.write_text(json.dumps([SAMPLE_SUB]), encoding="utf-8")
    token = tmp_path / "missing-token.txt"
    key = tmp_path / "vapid.pem"
    key.write_text("fixture", encoding="utf-8")
    state_path = tmp_path / "notification.json"
    calls = {"count": 0}

    def send_success(*_args, **_kwargs):
        calls["count"] += 1
        return True, False, "ok"

    monkeypatch.setattr(sp, "send_one", send_success)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "send_push.py",
            "--subscriptions-file",
            str(subscriptions),
            "--token-file",
            str(token),
            "--vapid-key-file",
            str(key),
            "--record-state",
            str(state_path),
        ],
    )

    assert main() == 0
    assert main() == 0
    capsys.readouterr()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    verified = daily_self_heal._load_notification_state(
        state_path, sp._today_jst_str()
    )

    assert calls["count"] == 1
    assert state["status"] == "already_sent"
    assert not Path(state["deliveryReceipt"]["priorDeliveryReceiptPath"]).is_absolute()
    assert not Path(state["evidenceLedgerPath"]).is_absolute()
    assert not Path(state["deliveryReceiptV2Path"]).is_absolute()
    assert state["deliveryReceiptV2Path"] == f"{sp._today_jst_str()}.already-sent-verifications.jsonl"
    verification_path = state_path.with_name(state["deliveryReceiptV2Path"])
    assert verification_path.is_file()
    assert state["deliveryReceiptV2"] in [json.loads(line) for line in verification_path.read_text(encoding="utf-8").splitlines()]
    assert str(tmp_path) not in json.dumps(state, ensure_ascii=False)
    assert verified["reason"] == ""


def test_notification_v2_binds_run_and_anonymized_recipient_without_raw_endpoint() -> None:
    """V2 receiptはrun-intentと匿名recipient結果を保持し、生endpointを保存しない。"""
    audience = sp._audience_set_sha256([SAMPLE_SUB])
    state = sp._notification_state(
        status="sent",
        ok=True,
        source="file",
        subscription_count=1,
        sent_count=1,
        payload_sha256="a" * 64,
        audience_set_sha256=audience,
        run_id="direct-2026-09-01-test",
        run_intent="scheduled_production_direct",
        recipient_results=[{"recipientKey": sp._recipient_key(SAMPLE_SUB, audience), "status": "sent"}],
    )
    receipt = state["deliveryReceiptV2"]
    assert receipt["schemaVersion"] == "NEWS_GRASP_NOTIFICATION_DELIVERY_RECEIPT_V2"
    assert receipt["runId"] == "direct-2026-09-01-test"
    assert SAMPLE_SUB["endpoint"] not in json.dumps(receipt, ensure_ascii=False)


def test_public_completion_rejects_self_hashed_notification_without_sender_ledger(tmp_path) -> None:
    """security Red: 派生V2 JSONの自己SHAだけでは送達authorityにしない。"""
    from tools.news_grasp_direct_completion import _notification

    issue_date = sp._today_jst_str()
    state = sp._notification_state(
        status="sent",
        ok=True,
        source="file",
        subscription_count=1,
        sent_count=1,
        payload_sha256="a" * 64,
        audience_set_sha256="b" * 64,
        run_id="direct-test",
        recipient_results=[{"recipientKey": "c" * 64, "status": "sent"}],
    )
    path = tmp_path / "build" / "notification" / f"{issue_date}.json"
    path.parent.mkdir(parents=True)
    sp._write_atomic_json(path, state)
    result = _notification(tmp_path, issue_date, run_id="direct-test")
    assert result["ok"] is False
    assert "notification_sender_ledger_missing" in result["failures"]


def test_public_completion_rejects_sender_ledger_without_immutable_git_blob_binding(tmp_path) -> None:
    """local self-hashだけでなくpre-send Git blob bindingを要求する。"""
    from tools.news_grasp_direct_completion import _notification

    issue_date = sp._today_jst_str()
    state_path = tmp_path / "build" / "notification" / f"{issue_date}.json"
    state = sp._notification_state(
        status="sent",
        ok=True,
        source="file",
        subscription_count=1,
        sent_count=1,
        payload_sha256="a" * 64,
        audience_set_sha256="b" * 64,
        run_id="direct-test",
        recipient_results=[{"recipientKey": "c" * 64, "status": "sent"}],
    )
    sp._write_notification_state(str(state_path), state)
    result = _notification(tmp_path, issue_date, run_id="direct-test")
    assert result["ok"] is False
    assert "notification_sender_git_blob_binding_invalid" in result["failures"] or "notification_trusted_sender_source_missing" in result["failures"]


def test_public_completion_rejects_notification_receipt_path_alias(tmp_path) -> None:
    """state sibling exact ID以外のrepo内ledger/V2/prior aliasを拒否する。"""
    from tools.news_grasp_direct_completion import _notification

    issue_date = sp._today_jst_str()
    state_path = tmp_path / "build" / "notification" / f"{issue_date}.json"
    state_path.parent.mkdir(parents=True)
    state = sp._notification_state(
        status="sent",
        ok=True,
        source="file",
        subscription_count=1,
        sent_count=1,
        payload_sha256="a" * 64,
        audience_set_sha256="b" * 64,
        run_id="direct-test",
        recipient_results=[{"recipientKey": "c" * 64, "status": "sent"}],
    )
    sp._write_notification_state(str(state_path), state)
    mutated = json.loads(state_path.read_text(encoding="utf-8"))
    mutated["evidenceLedgerPath"] = "other.json"
    mutated["deliveryReceiptV2Path"] = "other-v2.json"
    sp._write_atomic_json(state_path, mutated)
    result = _notification(tmp_path, issue_date, run_id="direct-test")
    assert "notification_sender_ledger_path_id_invalid" in result["failures"]
    assert "notification_v2_path_id_invalid" in result["failures"]


def test_sent_state_without_canonical_delivery_ledger_is_rejected(tmp_path):
    from tools import daily_self_heal

    issue_date = sp._today_jst_str()
    state = sp._notification_state(
        status="sent",
        ok=True,
        source="file",
        subscription_count=1,
        sent_count=1,
        payload_sha256="a" * 64,
        audience_set_sha256="b" * 64,
    )
    state_path = tmp_path / f"{issue_date}.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    verified = daily_self_heal._load_notification_state(state_path, issue_date)

    assert verified["reason"] == "notification_evidence_ledger_invalid"
    assert not (tmp_path / f"{issue_date}.delivery.json").exists()


@pytest.mark.parametrize(
    ("status", "ledger_suffix"),
    [("sent", "delivery"), ("already_sent", "delivery"), ("no_subscribers", "audience")],
)
def test_notification_evidence_ledger_rejects_leaf_symlink(
    tmp_path, status, ledger_suffix
):
    from tools import daily_self_heal

    issue_date = sp._today_jst_str()
    state_path = tmp_path / f"{issue_date}.json"
    common = {
        "source": "file",
        "payload_sha256": "a" * 64,
        "audience_set_sha256": "b" * 64,
    }
    if status == "no_subscribers":
        state = sp._notification_state(
            status=status,
            ok=True,
            subscription_count=0,
            sent_count=0,
            **common,
        )
    else:
        sent = sp._notification_state(
            status="sent",
            ok=True,
            subscription_count=1,
            sent_count=1,
            **common,
        )
        sp._write_notification_state(str(state_path), sent)
        if status == "sent":
            state = sent
        else:
            ledger_path = tmp_path / f"{issue_date}.delivery.json"
            ledger_raw = ledger_path.read_bytes()
            prior = json.loads(ledger_raw.decode("utf-8"))
            state = sp._notification_state(
                status="already_sent",
                ok=True,
                subscription_count=1,
                sent_count=1,
                prior_delivery_receipt_sha256=prior["receiptSha256"],
                prior_delivery_receipt_file_sha256=hashlib.sha256(
                    ledger_raw
                ).hexdigest(),
                prior_delivery_receipt_path=str(ledger_path.absolute()),
                **common,
            )
    if status != "sent":
        sp._write_notification_state(str(state_path), state)
    ledger_path = tmp_path / f"{issue_date}.{ledger_suffix}.json"
    external = tmp_path / f"external-{status}.json"
    external.write_bytes(ledger_path.read_bytes())
    ledger_path.unlink()
    try:
        ledger_path.symlink_to(external)
    except OSError:
        pytest.skip("file symlink creation is unavailable")

    verified = daily_self_heal._load_notification_state(state_path, issue_date)

    assert verified["reason"] == "notification_evidence_ledger_invalid"


@pytest.mark.parametrize("invalid_kind", ["directory", "dangling_symlink"])
def test_invalid_prior_delivery_ledger_blocks_duplicate_send(
    tmp_path, monkeypatch, invalid_kind
):
    issue_date = sp._today_jst_str()
    subscriptions = tmp_path / "subs.json"
    subscriptions.write_text(json.dumps([SAMPLE_SUB]), encoding="utf-8")
    key = tmp_path / "vapid.pem"
    key.write_text("fixture", encoding="utf-8")
    state_path = tmp_path / f"{issue_date}.json"
    ledger_path = tmp_path / f"{issue_date}.delivery.json"
    if invalid_kind == "directory":
        ledger_path.mkdir()
    else:
        try:
            ledger_path.symlink_to(tmp_path / "missing-ledger.json")
        except OSError:
            pytest.skip("file symlink creation is unavailable")
    send_count = {"value": 0}

    def unexpected_send(*_args, **_kwargs):
        send_count["value"] += 1
        return True, False, "ok"

    monkeypatch.setattr(sp, "send_one", unexpected_send)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "send_push.py",
            "--subscriptions-file",
            str(subscriptions),
            "--token-file",
            str(tmp_path / "missing-token.txt"),
            "--vapid-key-file",
            str(key),
            "--record-state",
            str(state_path),
        ],
    )

    assert main() == 1
    assert send_count["value"] == 0
    assert json.loads(state_path.read_text(encoding="utf-8"))["status"] == "delivery_ledger_invalid"
