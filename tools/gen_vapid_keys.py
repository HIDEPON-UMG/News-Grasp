#!/usr/bin/env python3
"""VAPID 鍵ペアを 1 回だけ生成するヘルパー（Web Push 用）。

Web Push は「送信サーバーが秘密鍵で署名し、ブラウザは対応する公開鍵で検証する」
VAPID 方式で本人性を担保する。この鍵ペアは **News-Grasp 固定で 1 組** あればよく、
作り直すと既存購読がすべて無効化される（全端末の再登録が必要）ため通常は再生成しない。

生成物:
  - 秘密鍵 (PEM)        → ~/.secrets/news-grasp-vapid.pem
        tools/send_push.py が pywebpush の署名鍵として読む。git には含めない。
  - 公開鍵 (base64url)  → 標準出力に表示
        docs/push.js の VAPID_PUBLIC_KEY 定数に貼る（ブラウザの applicationServerKey）。

楕円曲線は EC P-256 (prime256v1)。Web Push 標準で唯一相互運用される曲線。

使い方:
    python tools/gen_vapid_keys.py
"""
from __future__ import annotations

import base64
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

DEFAULT_KEY_FILE = Path.home() / ".secrets" / "news-grasp-vapid.pem"


def b64url(raw: bytes) -> str:
    """RFC 7515 の base64url（末尾パディング無し）。applicationServerKey 用。"""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def application_server_key(private_key: ec.EllipticCurvePrivateKey) -> str:
    """ブラウザの pushManager.subscribe に渡す公開鍵（非圧縮 65 byte → base64url）。"""
    raw = private_key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return b64url(raw)


def main() -> int:
    key_file = DEFAULT_KEY_FILE

    if key_file.exists():
        # 既存鍵を尊重する。上書きは購読全消去に直結するため自動では行わない。
        print(f"既に鍵が存在します: {key_file}")
        print("上書きすると既存の全購読が無効化されます（全端末の再登録が必要）。")
        print("再生成する場合は手動でこのファイルを削除してから再実行してください。")
        private_key = serialization.load_pem_private_key(
            key_file.read_bytes(), password=None
        )
    else:
        private_key = ec.generate_private_key(ec.SECP256R1())
        key_file.parent.mkdir(parents=True, exist_ok=True)
        pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        key_file.write_bytes(pem)
        print(f"OK: 秘密鍵を保存しました → {key_file}")

    print()
    print("=== application server key（公開鍵 / base64url）===")
    print("docs/push.js の VAPID_PUBLIC_KEY にこの 1 行を貼ってください:")
    print()
    print(application_server_key(private_key))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
