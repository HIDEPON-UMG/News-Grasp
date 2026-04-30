#!/usr/bin/env python3
"""News-Grasp 本番用メール送信スクリプト（直接 SMTP）。

GAS Webhook を撤廃し、Python から Gmail SMTP+TLS で直送する。
利点:
- GAS GmailApp の htmlBody ~200KB 上限を撤廃（Gmail SMTP は 25MB 上限のみ）
- 中継サービスを 1 段減らし、障害点を削減
- 失敗ログが Runner ローカルに統合される

HTML 中の `<img src="cid:KEY">` を自動検出し、`assets/KEY.jpg` を MIME inline として添付する。
Sonnet は HTML を書くだけでよく、cid: マップや base64 化を意識しなくてよい。

使い方:
    python tools/send_email.py \\
        --html-file out.html \\
        --subject "News Grasp #YYYYMMDD ..." \\
        --to "addr1@example.com,addr2@example.com"

App Password は既定で `~/.secrets/news-grasp-smtp.txt` から読み込む。
"""
import argparse
import os
import re
import smtplib
import sys
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

DEFAULT_SENDER = "news.grasp.magazine@gmail.com"
DEFAULT_PASSWORD_FILE = Path.home() / ".secrets" / "news-grasp-smtp.txt"
DEFAULT_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587  # STARTTLS

CID_PATTERN = re.compile(r'src="cid:([^"]+)"')


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="News-Grasp SMTP sender")
    p.add_argument("--html-file", required=True, help="HTML 本文ファイル（UTF-8）")
    p.add_argument("--subject", required=True, help="件名")
    p.add_argument("--to", required=True,
                   help="宛先（カンマ区切り）。例: a@x.com,b@y.com")
    p.add_argument("--from", dest="from_addr", default=DEFAULT_SENDER,
                   help=f"差出人（既定: {DEFAULT_SENDER}）")
    p.add_argument("--password-file", default=str(DEFAULT_PASSWORD_FILE),
                   help=f"App Password ファイル（既定: {DEFAULT_PASSWORD_FILE}）")
    p.add_argument("--assets-dir", default=str(DEFAULT_ASSETS_DIR),
                   help=f"cid: 参照画像のディレクトリ（既定: {DEFAULT_ASSETS_DIR}）")
    p.add_argument("--display-name", default="News Grasp",
                   help="差出人表示名（既定: News Grasp）")
    p.add_argument("--dry-run", action="store_true",
                   help="送信せず、ビルドした MIME メッセージのサマリだけ表示")
    return p.parse_args()


def load_password(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise SystemExit(
            f"FAIL: App Password ファイルが見つかりません: {p}\n"
            "→ https://myaccount.google.com/apppasswords で 16 文字のパスを発行し、"
            f"`{p}` に保存してください（空白は除去されます）"
        )
    raw = p.read_text(encoding="utf-8").strip()
    return raw.replace(" ", "")  # Google 表示の "xxxx xxxx ..." を許容


def discover_inline_images(html: str, assets_dir: Path) -> dict[str, Path]:
    """HTML 中の cid: 参照を抽出し、対応する assets/KEY.jpg を辞書化。"""
    keys = set(CID_PATTERN.findall(html))
    result: dict[str, Path] = {}
    missing: list[str] = []
    for key in keys:
        candidate = assets_dir / f"{key}.jpg"
        if candidate.exists():
            result[key] = candidate
        else:
            missing.append(key)
    if missing:
        raise SystemExit(
            f"FAIL: HTML が参照する cid: のうち、assets に存在しないキーがあります: {missing}\n"
            f"→ {assets_dir} に該当 JPG を置くか、HTML 側の src を見直してください"
        )
    return result


def build_message(
    *, html: str, subject: str, sender: str, recipients: list[str],
    inline_images: dict[str, Path], display_name: str,
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f'"{display_name}" <{sender}>'
    msg["To"] = ", ".join(recipients)

    # cid: KEY を Content-ID 付きに置換しつつ、HTML を最終形へ
    cid_map: dict[str, str] = {}
    for key in inline_images:
        cid_map[key] = make_msgid(domain="news-grasp.local")[1:-1]  # <...> を外す
    final_html = CID_PATTERN.sub(
        lambda m: f'src="cid:{cid_map[m.group(1)]}"' if m.group(1) in cid_map else m.group(0),
        html,
    )

    # 代替プレーンテキスト（最低限のフォールバック）
    msg.set_content(
        f"{subject}\n\n本メールは HTML 形式での閲覧を推奨します。\n"
        f"プレビューが崩れる場合は News Grasp リポジトリの digest フォルダを参照してください。"
    )
    msg.add_alternative(final_html, subtype="html")

    # inline 添付（multipart/related の中に入れる必要がある → html part に対して付ける）
    # disposition="inline" を明示しないと Gmail が「添付ファイル」一覧に表示してしまう
    html_part = msg.get_payload()[1]  # alternative の 2 番目 = html
    for key, path in inline_images.items():
        cid = cid_map[key]
        with path.open("rb") as f:
            data = f.read()
        ext = path.suffix.lower().lstrip(".")  # jpg / png 等
        maintype = "image"
        subtype = "jpeg" if ext in ("jpg", "jpeg") else ext
        html_part.add_related(
            data, maintype=maintype, subtype=subtype,
            cid=f"<{cid}>", disposition="inline",
        )
    return msg


def send(msg: EmailMessage, sender: str, password: str) -> None:
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(sender, password)
        smtp.send_message(msg)


def main() -> int:
    args = parse_args()
    html_path = Path(args.html_file)
    if not html_path.exists():
        print(f"FAIL: --html-file が存在しません: {html_path}", file=sys.stderr)
        return 1
    html = html_path.read_text(encoding="utf-8")
    recipients = [a.strip() for a in args.to.split(",") if a.strip()]
    if not recipients:
        print("FAIL: --to が空です", file=sys.stderr)
        return 1

    assets_dir = Path(args.assets_dir)
    inline_images = discover_inline_images(html, assets_dir)

    msg = build_message(
        html=html, subject=args.subject, sender=args.from_addr,
        recipients=recipients, inline_images=inline_images,
        display_name=args.display_name,
    )

    print(f"From:      {msg['From']}")
    print(f"To:        {msg['To']}")
    print(f"Subject:   {msg['Subject']}")
    print(f"HTML size: {len(html):,} bytes")
    print(f"Inline:    {len(inline_images)} images "
          f"({sum(p.stat().st_size for p in inline_images.values()):,} bytes total)")

    if args.dry_run:
        print("DRY-RUN: 送信せず終了")
        return 0

    password = load_password(args.password_file)
    try:
        send(msg, args.from_addr, password)
    except smtplib.SMTPException as e:
        print(f"FAIL: SMTP 送信エラー: {e}", file=sys.stderr)
        return 1
    print(f"OK: sent to {len(recipients)} recipient(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
