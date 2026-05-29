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
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.config import BASE_URL, PUSH_WORKER_URL  # noqa: E402  URL の単一ソース

DEFAULT_VAPID_KEY_FILE = Path.home() / ".secrets" / "news-grasp-vapid.pem"
DEFAULT_TOKEN_FILE = Path.home() / ".secrets" / "news-grasp-push-token.txt"
DEFAULT_SUBSCRIPTIONS_FILE = ROOT / "data" / "push_subscriptions.secret.json"
# VAPID の "sub" クレーム: push サービスが送信者に連絡するための識別子（mailto: か https:）
VAPID_CLAIMS_SUB = "mailto:hideki.kusunoki@gmail.com"

DEFAULT_TITLE = "News Grasp — 本日の更新"
DEFAULT_BODY = "本日のニュースダイジェストを公開しました。読んでみて！"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="News-Grasp Web Push sender")
    p.add_argument("--title", default=DEFAULT_TITLE, help="通知タイトル")
    p.add_argument("--body", default=DEFAULT_BODY, help="通知本文")
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
    return p.parse_args()


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
                return 1
            # その他の HTTP エラーは付随機能として今朝はスキップ（Runner を止めない）
            print(f"警告: Worker /list が HTTP {e.code}。今朝の push をスキップします（exit 0）",
                  file=sys.stderr)
            return 0
        except urllib.error.URLError as e:
            print(f"警告: Worker に接続できません（{e.reason}）。今朝の push をスキップします（exit 0）",
                  file=sys.stderr)
            return 0
    else:
        source = "file"
        subs = load_subscriptions(args.subscriptions_file)

    payload = build_payload(args.title, args.body, args.url)

    print(f"取得元:   {source}" + (f" ({worker_url})" if source == "worker" else ""))
    print(f"購読者:   {len(subs)} 件")
    print(f"payload:  {payload}")

    if not subs:
        print("送信対象がいないため終了します（exit 0）。")
        return 0

    if args.dry_run:
        print("DRY-RUN: 送信せず終了")
        return 0

    key_file = Path(args.vapid_key_file)
    if not key_file.exists():
        # 購読者がいるのに鍵が無い = 設定漏れ。ここだけは表面化させる。
        print(
            f"FAIL: VAPID 秘密鍵が見つかりません: {key_file}\n"
            "→ `python tools/gen_vapid_keys.py` で生成してください",
            file=sys.stderr,
        )
        return 1

    ok = 0
    stale_endpoints: list[str] = []
    for sub in subs:
        sent, gone, detail = send_one(sub, payload, str(key_file), VAPID_CLAIMS_SUB)
        if sent:
            ok += 1
        else:
            print(f"  - 送信失敗: {sub.get('endpoint', '')[:60]}... ({detail})")
            if gone:
                stale_endpoints.append(sub["endpoint"])

    print(f"OK: {ok}/{len(subs)} 件に送信成功")

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

    return 0


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
