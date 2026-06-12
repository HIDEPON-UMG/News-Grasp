#!/usr/bin/env python3
"""Content Gate 失敗時に公開面を fallback notice 付きへ退避する。"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path


NOTICE_START = "<!-- NEWS_GRASP_AVAILABILITY_NOTICE_START -->"
NOTICE_END = "<!-- NEWS_GRASP_AVAILABILITY_NOTICE_END -->"
STATUS_FILE = "publish-status.json"
DEFAULT_NOTICE = "本日の更新は品質確認中です。直近の公開済み号を表示しています。"


def _notice_html(date: str, reason: str, notice: str) -> str:
    safe_reason = reason.replace("<", "&lt;").replace(">", "&gt;")
    safe_notice = notice.replace("<", "&lt;").replace(">", "&gt;")
    return (
        f"{NOTICE_START}\n"
        "<section class=\"ng-availability-notice\" role=\"status\" "
        "style=\"background:#F2EEE3;border-block:1px solid #E2DED4;"
        "padding:14px 24px;color:#1A1A1A;font-family:'Noto Serif JP','Yu Mincho',serif;\">\n"
        "  <div style=\"max-width:1180px;margin:0 auto;display:grid;gap:4px;\">\n"
        "    <strong style=\"font-family:Inter,-apple-system,'Segoe UI',sans-serif;\">"
        f"{safe_notice}</strong>\n"
        f"    <span style=\"font-size:.88rem;color:#5C5A52;\">date={date} / reason={safe_reason}</span>\n"
        "  </div>\n"
        "</section>\n"
        f"{NOTICE_END}"
    )


def inject_notice(html: str, *, date: str, reason: str, notice: str = DEFAULT_NOTICE) -> str:
    block = _notice_html(date, reason, notice)
    if NOTICE_START in html and NOTICE_END in html:
        pattern = re.compile(re.escape(NOTICE_START) + r".*?" + re.escape(NOTICE_END), re.S)
        return pattern.sub(block, html)
    nav_end = html.find("</nav>")
    if nav_end >= 0:
        insert_at = nav_end + len("</nav>")
        return html[:insert_at] + "\n" + block + "\n" + html[insert_at:]
    body_start = re.search(r"<body[^>]*>", html, re.I)
    if body_start:
        insert_at = body_start.end()
        return html[:insert_at] + "\n" + block + "\n" + html[insert_at:]
    return block + "\n" + html


def write_fallback(docs_dir: Path, *, date: str, reason: str, notice: str = DEFAULT_NOTICE) -> None:
    index_path = docs_dir / "index.html"
    if not index_path.exists():
        raise FileNotFoundError(f"docs/index.html が存在しません: {index_path}")
    html = index_path.read_text(encoding="utf-8-sig", errors="replace")
    index_path.write_text(inject_notice(html, date=date, reason=reason, notice=notice), encoding="utf-8")
    status = {
        "result": "published_fallback_with_notice",
        "date": date,
        "reason": reason,
        "notice": notice,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    (docs_dir / STATUS_FILE).write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def mark_ok(docs_dir: Path, *, date: str) -> None:
    """通常号の成功公開時に publish-status.json を published_ok へリセットする。

    fallback publish は publish-status.json に published_fallback_with_notice を残すが、
    通常号が成功公開されても誰もこれを戻さず stale なままだった (2026-06-12 発覚)。
    send_push.py はこの状態を読んで「fallback 公開中は通知を抑止」するため、成功経路で
    必ず本関数を呼び published_ok に戻すことが fallback 抑止を解除する状態同期点になる
    (publish-status.json の所有者を本モジュールに一本化 = 境界 1 箇所集約)。
    """
    status = {
        "result": "published_ok",
        "date": date,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    (docs_dir / STATUS_FILE).write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def validate_availability(docs_dir: Path, *, expect_fallback: bool = False) -> list[str]:
    errors: list[str] = []
    index_path = docs_dir / "index.html"
    if not index_path.exists():
        return [f"docs/index.html が存在しません: {index_path}"]
    html = index_path.read_text(encoding="utf-8-sig", errors="replace")
    if len(html.strip()) < 500:
        errors.append("docs/index.html が短すぎます。公開面が空に近い可能性があります。")
    if "home-hero" not in html and NOTICE_START not in html:
        errors.append("docs/index.html に home-hero も availability notice もありません。")
    if expect_fallback:
        if NOTICE_START not in html:
            errors.append("fallback publish なのに availability notice がありません。")
        status_path = docs_dir / STATUS_FILE
        if not status_path.exists():
            errors.append(f"fallback publish なのに {STATUS_FILE} がありません。")
        else:
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                errors.append(f"{STATUS_FILE} が JSON として読めません。")
            else:
                if status.get("result") != "published_fallback_with_notice":
                    errors.append(f"{STATUS_FILE} の result が fallback ではありません。")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="News-Grasp availability fallback publish")
    sub = parser.add_subparsers(dest="cmd", required=True)
    fb = sub.add_parser("fallback")
    fb.add_argument("--docs-dir", type=Path, default=Path("docs"))
    fb.add_argument("--date", required=True)
    fb.add_argument("--reason", required=True)
    fb.add_argument("--notice", default=DEFAULT_NOTICE)
    ok = sub.add_parser("mark-ok")
    ok.add_argument("--docs-dir", type=Path, default=Path("docs"))
    ok.add_argument("--date", required=True)
    val = sub.add_parser("validate")
    val.add_argument("--docs-dir", type=Path, default=Path("docs"))
    val.add_argument("--expect-fallback", action="store_true")
    args = parser.parse_args(argv)

    if args.cmd == "fallback":
        write_fallback(args.docs_dir, date=args.date, reason=args.reason, notice=args.notice)
        print(f"PASS: fallback notice written ({args.docs_dir / 'index.html'})")
        return 0

    if args.cmd == "mark-ok":
        mark_ok(args.docs_dir, date=args.date)
        print(f"PASS: publish-status marked ok ({args.docs_dir / STATUS_FILE})")
        return 0

    errors = validate_availability(args.docs_dir, expect_fallback=args.expect_fallback)
    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1
    print("PASS: availability HTML OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
