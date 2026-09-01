#!/usr/bin/env python3
"""News-Grasp 本番用 Web Push 送信スクリプト。

毎朝の digest 更新後、購読済みの全端末へ「更新したよ、読んでみて！」を push する。

購読情報の取得元（優先順）:
  1. Cloudflare Worker (+ KV) — 既定の本番経路。ユーザーが「許可」を押すと
     docs/push.js が購読を Worker に自動 POST する（セルフサービス）。本スクリプトは
     Worker の GET /list?token= で全購読を取得する。
     有効化条件: 環境変数 NEWS_GRASP_PUSH_WORKER_URL（または --worker-url）と、
     ~/.secrets/news-grasp-push-token.txt（LIST_TOKEN）の両方が揃っていること。
  2. ローカル JSON ファイル — fallback（管理人の手元テスト用）。
     既定 data/push_subscriptions.secret.json（*.secret.json で gitignore 済）。

VAPID 秘密鍵は ~/.secrets/news-grasp-vapid.pem（tools/gen_vapid_keys.py で生成）。

設計上の約束:
    - 購読者が 0 人でも、鍵が無くても **毎朝の Runner を失敗させない**（exit 0）。
      push は付随的機能であり、digest 生成・公開の成否を左右してはならない。
      鍵が無いのに購読者がいる場合だけ、設定漏れとして exit 1 で表面化する。
    - 期限切れ購読（HTTP 404/410）は送信時に検出し、取得元から自動除去する
      （Worker なら /unsubscribe、ファイルなら書き戻し）。

使い方:
    python tools/send_push.py                  # 既定文面で全購読へ送信
    python tools/send_push.py --dry-run         # 送信せず取得元・対象数・payload を表示
    python tools/send_push.py --url https://... # 遷移先(タップで開く URL)を上書き
"""
import argparse
import ctypes
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.config import BASE_URL, CATEGORIES, PUSH_WORKER_URL  # noqa: E402  URL の単一ソース
from tools.publish_inventory import CATEGORY_ORDER, PUBLICATION_SCHEDULE  # noqa: E402

DEFAULT_VAPID_KEY_FILE = Path.home() / ".secrets" / "news-grasp-vapid.pem"
DEFAULT_TOKEN_FILE = Path.home() / ".secrets" / "news-grasp-push-token.txt"
DEFAULT_SUBSCRIPTIONS_FILE = ROOT / "data" / "push_subscriptions.secret.json"
# fallback 公開状態の単一ソース (publish_fallback.py が所有)。当日が fallback 公開
# (品質確認中 notice) のとき、通常文面の push で旧号へ誘導するのを抑止する。
PUBLISH_STATUS_FILE = ROOT / "docs" / "publish-status.json"
# VAPID の "sub" クレーム: push サービスが送信者に連絡するための識別子（mailto: か https:）
VAPID_CLAIMS_SUB = "mailto:hideki.kusunoki@gmail.com"

DEFAULT_TITLE = "📰 今日のNews Grasp"

# push サービス（FCM / APNs / Mozilla）に「端末がオフラインでもこの秒数だけ保持して
# 再接続時に配信せよ」と伝える TTL。**0（pywebpush の既定）は禁止**: TTL=0 だと
# push サービスは「送信した瞬間に端末がオンラインでなければ破棄」し、しかも送信側には
# 201/Created を返す。朝 06:35 の送信時にスマホが Doze/スリープだと毎朝 silently
# 破棄され、ログ上は「送信成功」なのに通知が一度も来ない（2026-06-01 実測で発覚）。
# 当日中に開けば足りるので 12 時間保持する（翌朝の重複配信は tag 固定で 1 件に畳まれる）。
DEFAULT_TTL_SECONDS = 12 * 60 * 60

# Web Push の Urgency ヘッダ（RFC 8030 §5.3）。**"high" を必須**とする。
# 未指定（pywebpush 既定）は "normal" 扱いになり、端末 OS が省電力状態のとき
# 配信を先送り（バッチ）する: Android の Doze は normal 優先度の push を
# メンテナンス窓までまとめ、iOS は apns-priority 5 として省電力配信する。
# 朝 06:38 の送信時、スマホは一晩アイドルで Doze / 低電力モードに入っているため、
# TTL を持たせて push サービス（FCM/APNs）が 201 受理しても **端末側で通知が
# surface せず「送信成功なのに来ない」** が起きる（日中の手動送信は端末が
# アクティブなので即表示される → これが「手動は届くが毎朝来ない」の非対称性）。
# "high" は FCM 優先度 high / apns-priority 10 にマップされ Doze を貫通して即時配信する。
DEFAULT_URGENCY = "high"


def _trusted_sender_source_binding(producer_sha256: str) -> dict[str, str]:
    """receipt producer bytesをimmutable Git commit blobへ束縛する。"""
    if not re.fullmatch(r"[0-9a-f]{64}", producer_sha256):
        return {}
    deadline = time.monotonic() + 15.0

    def run_git(args: list[str], *, text: bool = False) -> subprocess.CompletedProcess:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(args, 15)
        return subprocess.run(
            ["git", *args], cwd=str(ROOT), capture_output=True, text=text,
            encoding="utf-8" if text else None, errors="replace" if text else None,
            timeout=max(0.1, remaining), check=False, shell=False,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )

    try:
        remote_url = run_git(["remote", "get-url", "origin"], text=True)
    except subprocess.TimeoutExpired:
        return {}
    allowed_urls = {
        "https://github.com/HIDEPON-UMG/News-Grasp",
        "https://github.com/HIDEPON-UMG/News-Grasp.git",
        "git@github.com:HIDEPON-UMG/News-Grasp.git",
    }
    if remote_url.returncode != 0 or remote_url.stdout.strip() not in allowed_urls:
        return {}
    try:
        history = run_git(
            ["rev-list", "--max-count=256", "--first-parent", "origin/main", "--", "tools/send_push.py"],
            text=True,
        )
    except subprocess.TimeoutExpired:
        return {}
    if history.returncode != 0:
        return {}
    commits = [line for line in history.stdout.splitlines() if re.fullmatch(r"[0-9a-f]{40}", line)][:256]
    seen_blobs: set[str] = set()
    for commit in commits:
        try:
            blob_id_proc = run_git(["rev-parse", "--verify", f"{commit}:tools/send_push.py"], text=True)
        except subprocess.TimeoutExpired:
            return {}
        blob_id = blob_id_proc.stdout.strip()
        if blob_id_proc.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40,64}", blob_id) or blob_id in seen_blobs:
            continue
        seen_blobs.add(blob_id)
        try:
            kind = run_git(["cat-file", "-t", blob_id], text=True)
            size = run_git(["cat-file", "-s", blob_id], text=True)
        except subprocess.TimeoutExpired:
            return {}
        if kind.returncode != 0 or kind.stdout.strip() != "blob" or size.returncode != 0:
            continue
        try:
            byte_count = int(size.stdout.strip())
        except ValueError:
            continue
        if not 0 <= byte_count <= 2 * 1024 * 1024:
            continue
        try:
            blob = run_git(["cat-file", "blob", blob_id])
        except subprocess.TimeoutExpired:
            return {}
        if blob.returncode == 0 and len(blob.stdout) == byte_count and hashlib.sha256(blob.stdout).hexdigest() == producer_sha256:
            return {"senderSourceCommit": commit, "senderSourcePath": "tools/send_push.py", "senderSourceBlobId": blob_id, "senderSourceBlobSha256": producer_sha256}
    return {}


def _opened_path(descriptor: int, fallback: Path) -> Path:
    if os.name != "nt":
        return Path(os.path.realpath(fallback))
    import msvcrt

    handle = msvcrt.get_osfhandle(descriptor)
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetFinalPathNameByHandleW(
        ctypes.c_void_p(handle), buffer, len(buffer), 0
    )
    if length <= 0 or length >= len(buffer):
        raise OSError("opened path unavailable")
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(value)


def _safe_regular_bytes(path: Path, *, max_bytes: int) -> bytes:
    candidate = Path(os.path.abspath(path))
    before = os.lstat(candidate)
    attributes = int(getattr(before, "st_file_attributes", 0))
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        or before.st_nlink != 1
        or before.st_size <= 0
        or before.st_size > max_bytes
    ):
        raise OSError("unsafe evidence file")
    descriptor = os.open(
        candidate,
        os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened = os.fstat(descriptor)
        opened_path = _opened_path(descriptor, candidate)
        if (
            os.path.normcase(os.path.abspath(opened_path))
            != os.path.normcase(os.path.abspath(candidate))
            or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_nlink)
            != (before.st_dev, before.st_ino, before.st_size, 1)
        ):
            raise OSError("evidence identity drift")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    raw = b"".join(chunks)
    if (
        len(raw) != before.st_size
        or len(raw) > max_bytes
        or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    ):
        raise OSError("evidence changed during read")
    return raw

def categories_for_weekday(weekday: int) -> list[str]:
    """その曜日に配信されるカテゴリ表示名を、配信順で返す。"""
    scheduled = PUBLICATION_SCHEDULE.get(weekday, set())
    names: list[str] = []
    for cat_id in CATEGORY_ORDER:
        if cat_id not in scheduled:
            continue
        name = str(CATEGORIES[cat_id]["jp"])
        names.append("IT" if name == "IT-Consulting" else name)
    return names


def default_body_for_today(weekday: int | None = None) -> str:
    """その日に配信されるカテゴリだけを並べた通知本文（価値訴求型）。"""
    if weekday is None:
        try:
            from zoneinfo import ZoneInfo
            weekday = datetime.now(ZoneInfo("Asia/Tokyo")).weekday()
        except Exception:  # noqa: BLE001  tz 取得失敗時はローカル時刻にフォールバック
            weekday = datetime.now().weekday()
    return "・".join(categories_for_weekday(weekday)) + "の最新情報をまとめています。"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="News-Grasp Web Push sender")
    p.add_argument("--title", default=DEFAULT_TITLE, help="通知タイトル")
    p.add_argument("--body", default=None,
                   help="通知本文（既定: 当日配信カテゴリを自動列挙）")
    p.add_argument("--url", default=f"{BASE_URL}/",
                   help=f"タップで開く URL（既定: {BASE_URL}/）")
    p.add_argument("--worker-url", default=None,
                   help=f"購読保存先 Worker の URL（既定: config.PUSH_WORKER_URL = {PUSH_WORKER_URL}）")
    p.add_argument("--token-file", default=str(DEFAULT_TOKEN_FILE),
                   help=f"Worker /list の LIST_TOKEN ファイル（既定: {DEFAULT_TOKEN_FILE}）")
    p.add_argument("--subscriptions-file", default=str(DEFAULT_SUBSCRIPTIONS_FILE),
                   help=f"fallback の購読 JSON 配列（既定: {DEFAULT_SUBSCRIPTIONS_FILE}）")
    p.add_argument("--vapid-key-file", default=str(DEFAULT_VAPID_KEY_FILE),
                   help=f"VAPID 秘密鍵 PEM（既定: {DEFAULT_VAPID_KEY_FILE}）")
    p.add_argument("--dry-run", action="store_true",
                   help="送信せず、取得元・対象数・payload だけ表示")
    p.add_argument("--record-state", default=None,
                   help="通知結果を publish-complete 用 JSON として保存するパス")
    p.add_argument("--run-id", default="", help="direct runtime run ID")
    p.add_argument("--run-intent", default="scheduled_production_direct", help="実行意図")
    p.add_argument("--retry-count", type=int, default=0, help="cause-bound retry回数")
    return p.parse_args()


def _write_notification_state(path: str | None, payload: dict) -> None:
    if not path:
        return
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    state = dict(payload)
    status = str(state.get("status") or "")
    if status in {"sent", "already_sent"}:
        ledger_path = out.with_name(f"{state['date']}.delivery.json")
        if status == "sent" and isinstance(state.get("deliveryReceipt"), dict):
            _write_exclusive_json(ledger_path, state["deliveryReceipt"])
        ledger_raw = _safe_regular_bytes(ledger_path, max_bytes=65536)
        ledger = json.loads(ledger_raw.decode("utf-8-sig"))
        state["evidenceLedgerPath"] = ledger_path.name
        state["evidenceLedgerFileSha256"] = hashlib.sha256(ledger_raw).hexdigest()
        state["evidenceLedgerReceiptSha256"] = str(
            ledger.get("receiptSha256") or ""
        )
        v2 = state.get("deliveryReceiptV2")
        if isinstance(v2, dict):
            v2_path = out.with_name(f"{state['date']}.delivery-v2.json")
            if status == "sent":
                _write_exclusive_json(v2_path, v2)
            elif status == "already_sent":
                verification_path = out.with_name(f"{state['date']}.already-sent-verifications.jsonl")
                descriptor = os.open(verification_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                try:
                    os.write(descriptor, (json.dumps(v2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            state["deliveryReceiptV2Path"] = v2_path.name
    elif status == "no_subscribers" and isinstance(
        state.get("audienceResolutionReceipt"), dict
    ):
        ledger_path = out.with_name(f"{state['date']}.audience.json")
        _write_atomic_json(ledger_path, state["audienceResolutionReceipt"])
        ledger_raw = _safe_regular_bytes(ledger_path, max_bytes=65536)
        ledger = json.loads(ledger_raw.decode("utf-8-sig"))
        state["evidenceLedgerPath"] = ledger_path.name
        state["evidenceLedgerFileSha256"] = hashlib.sha256(ledger_raw).hexdigest()
        state["evidenceLedgerReceiptSha256"] = str(
            ledger.get("receiptSha256") or ""
        )
    _write_atomic_json(out, state)


def _write_atomic_json(path: Path, payload: dict) -> None:
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_exclusive_json(path: Path, payload: dict) -> None:
    """immutable sender receiptをsingle-writer exclusive-createで保存する。"""
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _notification_state(
    *,
    status: str,
    ok: bool,
    source: str = "",
    subscription_count: int = 0,
    sent_count: int = 0,
    detail: str = "",
    payload_sha256: str = "",
    audience_set_sha256: str = "",
    prior_delivery_receipt_sha256: str = "",
    prior_delivery_receipt_file_sha256: str = "",
    prior_delivery_receipt_path: str = "",
    run_id: str = "",
    run_intent: str = "scheduled_production_direct",
    retry_count: int = 0,
    recipient_results: list[dict] | None = None,
    original_sent_at: str = "",
    sender_event_id: str = "",
    prior_sender_producer_sha256: str = "",
) -> dict:
    producer_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if not payload_sha256:
        payload_sha256 = hashlib.sha256(b"").hexdigest()
    if not audience_set_sha256:
        audience_set_sha256 = hashlib.sha256(b"[]").hexdigest()
    producer_run_id = uuid.uuid4().hex
    state = {
        "date": _today_jst_str(),
        "status": status,
        "ok": ok,
        "source": source,
        "subscription_count": subscription_count,
        "sent_count": sent_count,
        "detail": detail,
        "recorded_at": datetime.now().astimezone().isoformat(),
        "payload_sha256": payload_sha256,
        "audience_set_sha256": audience_set_sha256,
        "producer": "tools.send_push",
        "producer_sha256": producer_sha256,
        "producer_run_id": producer_run_id,
        "run_id": run_id,
        "run_intent": run_intent,
        "retry_count": retry_count,
    }
    if status == "no_subscribers":
        receipt = {
            "schemaVersion": "NEWS_GRASP_NOTIFICATION_AUDIENCE_RESOLUTION_V1",
            "date": state["date"],
            "source": source,
            "subscriptionCount": subscription_count,
            "audienceSetSha256": audience_set_sha256,
            "producer": state["producer"],
            "producerSha256": producer_sha256,
            "producerRunId": producer_run_id,
            "resolvedAt": state["recorded_at"],
        }
        receipt["receiptSha256"] = hashlib.sha256(
            json.dumps(
                receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        state["audienceResolutionReceipt"] = receipt
        state["audienceResolutionReceiptSha256"] = receipt["receiptSha256"]
    elif status in {"sent", "already_sent"}:
        receipt = {
            "schemaVersion": "NEWS_GRASP_NOTIFICATION_DELIVERY_RECEIPT_V1",
            "date": state["date"],
            "source": source,
            "subscriptionCount": subscription_count,
            "sentCount": sent_count,
            "payloadSha256": payload_sha256,
            "audienceSetSha256": audience_set_sha256,
            "producer": state["producer"],
            "producerSha256": producer_sha256,
            "producerRunId": producer_run_id,
            "deliveredAt": state["recorded_at"],
        }
        if status == "already_sent":
            receipt["priorDeliveryReceiptSha256"] = prior_delivery_receipt_sha256
            receipt["priorDeliveryReceiptFileSha256"] = prior_delivery_receipt_file_sha256
            receipt["priorDeliveryReceiptPath"] = prior_delivery_receipt_path
        receipt["receiptSha256"] = hashlib.sha256(
            json.dumps(
                receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        state["deliveryReceipt"] = receipt
        state["deliveryReceiptSha256"] = receipt["receiptSha256"]
        receipt_v2 = {
            "schemaVersion": "NEWS_GRASP_NOTIFICATION_DELIVERY_RECEIPT_V2",
            "issueDate": state["date"],
            "runId": run_id,
            "runIntent": run_intent,
            "status": status,
            "originalSentAt": original_sent_at or receipt.get("deliveredAt") or state["recorded_at"],
            "verifiedAt": state["recorded_at"],
            "retryCount": int(retry_count),
            "payloadIdentity": payload_sha256,
            "audienceIdentity": audience_set_sha256,
            "subscriptionCount": subscription_count,
            "sentCount": sent_count,
            "recipientResults": list(recipient_results or []),
            "priorDeliveryReceiptSha256": prior_delivery_receipt_sha256 or receipt["receiptSha256"],
            "senderEventId": sender_event_id or producer_run_id,
            **_trusted_sender_source_binding(prior_sender_producer_sha256 or producer_sha256),
        }
        receipt_v2["receiptSha256"] = hashlib.sha256(
            json.dumps(receipt_v2, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        state["deliveryReceiptV2"] = receipt_v2
        state["deliveryReceiptV2Sha256"] = receipt_v2["receiptSha256"]
    return state


# ---------------------------------------------------------------------------
# HTTP ヘルパ（標準ライブラリのみ。テストでは monkeypatch で差し替える）
#   - User-Agent を明示する。既定の "Python-urllib/x" は Cloudflare のエッジで
#     bot 判定され 403 で弾かれることがあり、その場合 /list が永遠に取れず
#     毎朝の送信が黙って skip される（2026-05-29 実測で 403 を踏んで発覚）。
# ---------------------------------------------------------------------------

_USER_AGENT = "News-Grasp-Push/1.0 (+https://hidepon-umg.github.io/News-Grasp)"


def _http_get_json(url: str, timeout: int = 10):
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": _USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _http_post_json(url: str, body: dict, timeout: int = 10):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# 購読情報の取得元
# ---------------------------------------------------------------------------

def _validate_subs(data, where: str) -> list[dict]:
    if not isinstance(data, list):
        raise SystemExit(f"FAIL: {where} は購読オブジェクトの配列である必要があります")
    subs: list[dict] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict) or "endpoint" not in item:
            raise SystemExit(f"FAIL: {where} の {i} 番目に 'endpoint' がありません")
        subs.append(item)
    return subs


def load_subscriptions(path: str | Path) -> list[dict]:
    """購読 JSON 配列をローカルファイルから読む。無ければ空リスト（エラーにしない）。"""
    p = Path(path)
    if not p.exists():
        print(f"購読ファイルがまだありません: {p}（購読者 0 人として扱います）")
        return []
    raw = p.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    return _validate_subs(json.loads(raw), str(p))


def load_subscriptions_from_worker(worker_url: str, token: str) -> list[dict]:
    """Worker の GET /list?token= から全購読を取得する。"""
    url = f"{worker_url}/list?token={urllib.parse.quote(token, safe='')}"
    return _validate_subs(_http_get_json(url), "Worker /list")


def prune_from_worker(worker_url: str, endpoint: str) -> None:
    """失効購読を Worker の POST /unsubscribe で削除する（失敗は致命でない）。"""
    try:
        _http_post_json(f"{worker_url}/unsubscribe", {"endpoint": endpoint})
    except Exception as e:  # noqa: BLE001  掃除失敗で全体を止めない
        print(f"  - Worker からの失効購読削除に失敗: {endpoint[:40]}... ({e})")


def resolve_token(token_file: str) -> str | None:
    p = Path(token_file)
    if p.exists():
        t = p.read_text(encoding="utf-8").strip()
        return t or None
    return None


def build_payload(title: str, body: str, url: str) -> str:
    """SW の push ハンドラが解釈する JSON 文字列を作る。"""
    return json.dumps({"title": title, "body": body, "url": url}, ensure_ascii=False)


def _audience_set_sha256(subscriptions: list[dict]) -> str:
    canonical = json.dumps(
        sorted(subscriptions, key=lambda item: str(item.get("endpoint") or "")),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _recipient_key(subscription: dict, audience_set_sha256: str) -> str:
    """endpointを保存せず、receipt内で安定した匿名化keyへ変換する。"""
    endpoint = str(subscription.get("endpoint") or "")
    return hashlib.sha256(f"{audience_set_sha256}:{endpoint}".encode("utf-8")).hexdigest()


def _load_prior_delivery_receipt(path: Path) -> tuple[dict, str] | None:
    try:
        raw = _safe_regular_bytes(path, max_bytes=65536)
        value = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(value, dict):
            return None
        body = {key: item for key, item in value.items() if key != "receiptSha256"}
        expected = hashlib.sha256(
            json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if value.get("receiptSha256") != expected:
            return None
        return value, hashlib.sha256(raw).hexdigest()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _today_jst_str() -> str:
    """当日 (JST) を YYYY-MM-DD で返す。tz 取得失敗時はローカル時刻。"""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001  tz 取得失敗時はローカル時刻にフォールバック
        return datetime.now().strftime("%Y-%m-%d")


def publish_status_is_fallback(status_path: str | Path, today_str: str) -> bool:
    """docs/publish-status.json が「当日 fallback 公開中」を示すなら True。

    Content Gate 失敗で fallback publish (品質確認中 notice) になった日は、サイトが
    旧号を表示しているため、通常文面の push (「今日の News Grasp / …の最新情報」) で
    そこへ誘導すると誤誘導になる (2026-06-12 実測で fallback 中も通常 push が飛んだ)。
    本関数が True を返す間、send_push は送信を抑止する。

    成功公開時は runner が `publish_fallback mark-ok` で result=published_ok を書くため
    抑止は解除される。status ファイルが無い / JSON 不正 / result が fallback でない /
    date が当日でない場合は False (= 通常どおり送信)。前日以前の stale fallback を
    当日の手動送信が誤って抑止しないよう date == 当日 を必須にする。
    """
    try:
        status = json.loads(Path(status_path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return False
    if not isinstance(status, dict):
        return False
    return (
        status.get("result") == "published_fallback_with_notice"
        and status.get("date") == today_str
    )


def send_one(subscription: dict, payload: str, vapid_key_file: str, claims_sub: str):
    """1 購読へ送信。(ok: bool, gone: bool, detail: str) を返す。

    gone=True は購読が失効（404/410）= 取得元から除去すべき、の意味。
    """
    from pywebpush import WebPushException, webpush  # 遅延 import（テストを軽く保つ）

    try:
        webpush(
            subscription_info=subscription,
            data=payload,
            vapid_private_key=vapid_key_file,
            vapid_claims={"sub": claims_sub},
            ttl=DEFAULT_TTL_SECONDS,  # 0 だとオフライン端末で破棄される（上の定数コメント参照）
            headers={"Urgency": DEFAULT_URGENCY},  # Doze/低電力中の端末でも即配信（normal は先送りされる）
            timeout=10,
        )
        return True, False, "ok"
    except WebPushException as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        gone = status in (404, 410)
        return False, gone, f"status={status} {e}"


def main() -> int:
    args = parse_args()

    # 取得元の決定: Worker URL + token が揃えば Worker、無ければローカルファイル。
    # URL は --worker-url > config.PUSH_WORKER_URL（環境変数で上書き可）の順。
    worker_url = (args.worker_url or PUSH_WORKER_URL).rstrip("/") or None
    token = resolve_token(args.token_file)
    if worker_url and token:
        source = "worker"
        try:
            subs = load_subscriptions_from_worker(worker_url, token)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                # token 不一致 = 設定漏れ。表面化させる。
                print(
                    "FAIL: Worker /list が 401（LIST_TOKEN 不一致）。"
                    f"{args.token_file} の内容と Worker の secret を確認してください",
                    file=sys.stderr,
                )
                _write_notification_state(
                    args.record_state,
                    _notification_state(status="config_error", ok=False, source="worker", detail="worker_list_401"),
                )
                return 1
            # その他の HTTP エラーは付随機能として今朝はスキップ（Runner を止めない）
            print(f"警告: Worker /list が HTTP {e.code}。今朝の push をスキップします（exit 0）",
                  file=sys.stderr)
            _write_notification_state(
                args.record_state,
                _notification_state(status="external_error", ok=False, source="worker", detail=f"http_{e.code}"),
            )
            return 0
        except urllib.error.URLError as e:
            print(f"警告: Worker に接続できません（{e.reason}）。今朝の push をスキップします（exit 0）",
                  file=sys.stderr)
            _write_notification_state(
                args.record_state,
                _notification_state(status="external_error", ok=False, source="worker", detail=str(e.reason)),
            )
            return 0
    else:
        source = "file"
        subs = load_subscriptions(args.subscriptions_file)

    body = args.body if args.body is not None else default_body_for_today()
    payload = build_payload(args.title, body, args.url)
    payload_sha256 = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    audience_set_sha256 = _audience_set_sha256(subs)

    if args.record_state and subs and not args.dry_run:
        delivery_path = Path(args.record_state).with_name(
            f"{_today_jst_str()}.delivery.json"
        )
        try:
            os.lstat(delivery_path)
            delivery_ledger_present = True
        except FileNotFoundError:
            delivery_ledger_present = False
        except OSError:
            delivery_ledger_present = True
        if delivery_ledger_present:
            prior = _load_prior_delivery_receipt(delivery_path)
            if prior is None:
                _write_notification_state(
                    args.record_state,
                    _notification_state(
                        status="delivery_ledger_invalid",
                        ok=False,
                        source=source,
                        subscription_count=len(subs),
                        sent_count=0,
                        payload_sha256=payload_sha256,
                        audience_set_sha256=audience_set_sha256,
                    ),
                )
                return 1
            prior_receipt, prior_file_sha = prior
            if (
                prior_receipt.get("date") == _today_jst_str()
                and prior_receipt.get("source") == source
                and prior_receipt.get("subscriptionCount") == len(subs)
                and prior_receipt.get("sentCount") == len(subs)
                and prior_receipt.get("payloadSha256") == payload_sha256
                and prior_receipt.get("audienceSetSha256") == audience_set_sha256
            ):
                _write_notification_state(
                    args.record_state,
                    _notification_state(
                        status="already_sent",
                        ok=True,
                        source=source,
                        subscription_count=len(subs),
                        sent_count=len(subs),
                        payload_sha256=payload_sha256,
                        audience_set_sha256=audience_set_sha256,
                        prior_delivery_receipt_sha256=str(prior_receipt["receiptSha256"]),
                        prior_delivery_receipt_file_sha256=prior_file_sha,
                        prior_delivery_receipt_path=delivery_path.name,
                        run_id=args.run_id,
                        run_intent=args.run_intent,
                        retry_count=args.retry_count,
                        original_sent_at=str(prior_receipt.get("deliveredAt") or ""),
                        sender_event_id=str(prior_receipt.get("producerRunId") or ""),
                        prior_sender_producer_sha256=str(prior_receipt.get("producerSha256") or ""),
                        recipient_results=[
                            {"recipientKey": _recipient_key(item, audience_set_sha256), "status": "already_sent"}
                            for item in subs
                        ],
                    ),
                )
                return 0
            _write_notification_state(
                args.record_state,
                _notification_state(
                    status="delivery_ledger_conflict",
                    ok=False,
                    source=source,
                    subscription_count=len(subs),
                    sent_count=0,
                    payload_sha256=payload_sha256,
                    audience_set_sha256=audience_set_sha256,
                ),
            )
            return 1

    print(f"取得元:   {source}" + (f" ({worker_url})" if source == "worker" else ""))
    print(f"購読者:   {len(subs)} 件")
    print(f"payload:  {payload}")

    if not subs:
        print("送信対象がいないため終了します（exit 0）。")
        _write_notification_state(
            args.record_state,
            _notification_state(
                status="no_subscribers",
                ok=True,
                source=source,
                subscription_count=0,
                sent_count=0,
                payload_sha256=payload_sha256,
                audience_set_sha256=audience_set_sha256,
            ),
        )
        return 0

    if args.dry_run:
        print("DRY-RUN: 送信せず終了")
        _write_notification_state(
            args.record_state,
            _notification_state(
                status="dry_run",
                ok=True,
                source=source,
                subscription_count=len(subs),
                sent_count=0,
            ),
        )
        return 0

    # fallback 公開中 (品質確認中 notice) は通常文面の push で旧号へ誘導すると誤誘導に
    # なるため送信を抑止する。成功公開時は runner が publish_fallback mark-ok で
    # published_ok に戻すため抑止は解除される (2026-06-12 疑義 C の状態同期)。
    if publish_status_is_fallback(PUBLISH_STATUS_FILE, _today_jst_str()):
        print("fallback 公開中 (品質確認中 notice) のため push を抑止します (exit 0)。"
              "通常号が確定すれば publish_fallback mark-ok で抑止が解除されます。")
        _write_notification_state(
            args.record_state,
            _notification_state(
                status="skipped_fallback",
                ok=True,
                source=source,
                subscription_count=len(subs),
                sent_count=0,
            ),
        )
        return 0

    key_file = Path(args.vapid_key_file)
    if not key_file.exists():
        # 購読者がいるのに鍵が無い = 設定漏れ。ここだけは表面化させる。
        print(
            f"FAIL: VAPID 秘密鍵が見つかりません: {key_file}\n"
            "→ `python tools/gen_vapid_keys.py` で生成してください",
            file=sys.stderr,
        )
        _write_notification_state(
            args.record_state,
            _notification_state(
                status="config_error",
                ok=False,
                source=source,
                subscription_count=len(subs),
                sent_count=0,
                detail=f"missing_vapid_key:{key_file}",
            ),
        )
        return 1

    ok = 0
    stale_endpoints: list[str] = []
    recipient_results: list[dict] = []
    for sub in subs:
        sent, gone, detail = send_one(sub, payload, str(key_file), VAPID_CLAIMS_SUB)
        recipient_results.append(
            {
                "recipientKey": _recipient_key(sub, audience_set_sha256),
                "status": "sent" if sent else ("gone" if gone else "failed"),
            }
        )
        if sent:
            ok += 1
        else:
            print(f"  - 送信失敗: {sub.get('endpoint', '')[:60]}... ({detail})")
            if gone:
                stale_endpoints.append(sub["endpoint"])

    print(f"OK: {ok}/{len(subs)} 件に送信成功")
    _write_notification_state(
        args.record_state,
        _notification_state(
            status="sent" if ok == len(subs) else "partial_failure",
            ok=ok == len(subs),
            source=source,
            subscription_count=len(subs),
            sent_count=ok,
            payload_sha256=payload_sha256,
            audience_set_sha256=audience_set_sha256,
            run_id=args.run_id,
            run_intent=args.run_intent,
            retry_count=args.retry_count,
            recipient_results=recipient_results,
        ),
    )

    # 失効した購読を取得元から自動除去（次回以降のノイズを消す）
    if stale_endpoints:
        if source == "worker":
            for ep in stale_endpoints:
                prune_from_worker(worker_url, ep)
        else:
            remaining = [s for s in subs if s["endpoint"] not in stale_endpoints]
            Path(args.subscriptions_file).write_text(
                json.dumps(remaining, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        print(f"失効購読 {len(stale_endpoints)} 件を除去しました")

    return 0 if ok == len(subs) else 1


def _force_utf8_stdio() -> None:
    """日本語版 Windows でも UTF-8 で出力する（CP932 文字化け回避）。

    import 時ではなく **スクリプト実行時のみ** 適用する。import 時に
    sys.stdout を差し替えると pytest のキャプチャを壊すため。
    """
    import io
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


if __name__ == "__main__":
    _force_utf8_stdio()
    sys.exit(main())
