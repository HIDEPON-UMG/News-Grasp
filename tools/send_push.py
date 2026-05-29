#!/usr/bin/env python3
"""News-Grasp 本番用 Web Push 送信スクリプト。

毎朝の digest 更新後、購読済みの全端末へ「更新したよ、読んでみて！」を push する。
News Grasp はサーバーを持たない静的サイトのため、購読情報は管理人が手動で集める
（手動登録運用）。docs/push.js の「通知を受け取る」ボタンが出力した JSON を、管理人が
下記ファイルの配列に 1 行ずつ追記する。

購読ファイル（既定 data/push_subscriptions.secret.json、*.secret.json で gitignore 済）:
    [
      {"endpoint": "https://...", "keys": {"p256dh": "...", "auth": "..."}},
      ...
    ]

VAPID 秘密鍵は ~/.secrets/news-grasp-vapid.pem（tools/gen_vapid_keys.py で生成）。

設計上の約束:
    - 購読者が 0 人でも、鍵が無くても **毎朝の Runner を失敗させない**（exit 0）。
      push は付随的機能であり、digest 生成・公開の成否を左右してはならない。
      鍵が無いのに購読者がいる場合だけ、設定漏れとして exit 1 で表面化する。
    - 期限切れ購読（HTTP 404/410）は送信時に検出し、購読ファイルから自動除去する。

使い方:
    python tools/send_push.py                  # 既定文面で全購読へ送信
    python tools/send_push.py --dry-run         # 送信せず対象数と payload を表示
    python tools/send_push.py --url https://... # 遷移先(タップで開く URL)を上書き
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.config import BASE_URL  # noqa: E402  BASE_URL の単一ソース

DEFAULT_VAPID_KEY_FILE = Path.home() / ".secrets" / "news-grasp-vapid.pem"
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
    p.add_argument("--subscriptions-file", default=str(DEFAULT_SUBSCRIPTIONS_FILE),
                   help=f"購読 JSON 配列（既定: {DEFAULT_SUBSCRIPTIONS_FILE}）")
    p.add_argument("--vapid-key-file", default=str(DEFAULT_VAPID_KEY_FILE),
                   help=f"VAPID 秘密鍵 PEM（既定: {DEFAULT_VAPID_KEY_FILE}）")
    p.add_argument("--dry-run", action="store_true",
                   help="送信せず、対象数と payload だけ表示")
    return p.parse_args()


def load_subscriptions(path: str | Path) -> list[dict]:
    """購読 JSON 配列を読む。ファイルが無ければ空リスト（エラーにしない）。"""
    p = Path(path)
    if not p.exists():
        print(f"購読ファイルがまだありません: {p}（購読者 0 人として扱います）")
        return []
    raw = p.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    data = json.loads(raw)
    if not isinstance(data, list):
        raise SystemExit(
            f"FAIL: 購読ファイルは購読オブジェクトの配列である必要があります: {p}"
        )
    subs: list[dict] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict) or "endpoint" not in item:
            raise SystemExit(
                f"FAIL: {p} の {i} 番目に 'endpoint' がありません（PushSubscription.toJSON() を貼ってください）"
            )
        subs.append(item)
    return subs


def build_payload(title: str, body: str, url: str) -> str:
    """SW の push ハンドラが解釈する JSON 文字列を作る。"""
    return json.dumps({"title": title, "body": body, "url": url}, ensure_ascii=False)


def send_one(subscription: dict, payload: str, vapid_key_file: str, claims_sub: str):
    """1 購読へ送信。(ok: bool, gone: bool, detail: str) を返す。

    gone=True は購読が失効（404/410）= ファイルから除去すべき、の意味。
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

    subs = load_subscriptions(args.subscriptions_file)
    payload = build_payload(args.title, args.body, args.url)

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

    # 失効した購読をファイルから自動除去（次回以降のノイズを消す）
    if stale_endpoints:
        remaining = [s for s in subs if s["endpoint"] not in stale_endpoints]
        Path(args.subscriptions_file).write_text(
            json.dumps(remaining, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"失効購読 {len(stale_endpoints)} 件を除去しました（残り {len(remaining)} 件）")

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
