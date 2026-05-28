#!/usr/bin/env python3
"""articles.jsonl の NUL バイト破損行を修復する保守ユーティリティ。

OneDrive 同期の中断などで、行頭に大量の NUL バイト (`\x00`) が前置され、その後ろに
無傷の JSON が続く破損行が発生することがある (例: 2026-05-14 FX 記事行)。
generate_pages.py は当該行を JSONDecodeError として skip するため記事が実質欠損する。

本スクリプトは「行頭の NUL バイトのみを除去」し、除去後が valid JSON であることを
検証してから書き戻す。NUL 除去以外のバイト差分が出た場合は中断する (安全側)。
BOM (line 1) を含め、健全な行は一切変更しない。

実行:
    python tools/repair_articles_nul.py            # 修復を適用
    python tools/repair_articles_nul.py --dry-run  # 検出のみ (書き込まない)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JSONL = ROOT / "data" / "articles.jsonl"


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    data = JSONL.read_bytes()
    lines = data.split(b"\n")

    repaired: list[tuple[int, int]] = []  # (lineno, removed_nul_count)
    for idx, line in enumerate(lines):
        if b"\x00" not in line:
            continue
        stripped = line.lstrip(b"\x00")
        removed = len(line) - len(stripped)
        # 行頭以外に NUL が残るなら未知の破損形態 → 触らず中断
        if b"\x00" in stripped:
            print(f"line {idx + 1}: NUL bytes are not a clean leading prefix; aborting")
            return 1
        # 除去後が valid JSON であることを確認
        try:
            obj = json.loads(stripped.decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"line {idx + 1}: post-strip JSON still invalid ({e}); aborting")
            return 1
        lines[idx] = stripped
        repaired.append((idx + 1, removed))
        title = (obj.get("title") or "").encode("ascii", "replace").decode()
        print(
            f"line {idx + 1}: removed {removed} NUL bytes -> "
            f"genre={obj.get('genre')} seen_at={obj.get('seen_at')} "
            f"thumb_key={'thumb' in obj} title={title[:50]!r}"
        )

    if not repaired:
        print("no NUL-corrupted lines found")
        return 0

    out = b"\n".join(lines)
    total_removed = sum(n for _, n in repaired)
    # NUL 除去以外の差分が無いことを保証
    if len(data) - len(out) != total_removed:
        print(
            f"byte-diff mismatch (data={len(data)} out={len(out)} removed={total_removed}); aborting"
        )
        return 1

    if dry_run:
        print(f"[dry-run] would repair {len(repaired)} line(s), remove {total_removed} NUL bytes")
        return 0

    JSONL.write_bytes(out)
    print(f"repaired {len(repaired)} line(s), removed {total_removed} NUL bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
