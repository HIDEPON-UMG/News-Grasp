#!/usr/bin/env python3
"""articles.jsonl に append される全レコードが thumb キーを持つことを検証する契約テスト。

routine-system.md 3-B の仕様変更 (2026-05-10) に伴い、append の thumb キー欠落を
構造的に検出するために導入した。値は URL でも null でもよいが、**キー自体の欠落は禁止**
(これまではキーごと落ちる pipeline 不整合が 92.5% を占めていた)。

Cutoff: 2026-05-11 以降の全レコードを検証対象とする。それ以前の legacy レコードは
許容する (改修より前の append のため)。

実行:
    py tests/test_thumb_contract.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JSONL = ROOT / "data" / "articles.jsonl"
CUTOFF_DATE = "2026-05-11"


def test_thumb_key_present_after_cutoff() -> list[str]:
    errs: list[str] = []
    if not JSONL.exists():
        errs.append(f"articles.jsonl not found: {JSONL}")
        return errs

    with JSONL.open(encoding="utf-8-sig") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                errs.append(f"line {lineno}: JSON decode error: {e}")
                continue
            if rec.get("date", "") < CUTOFF_DATE:
                continue
            if "thumb" not in rec:
                title = (rec.get("title") or "")[:50]
                errs.append(
                    f"line {lineno}: thumb key missing "
                    f"(date={rec.get('date')!r}, title={title!r})"
                )
    return errs


def main() -> int:
    # 日本語版 Windows の既定 cp932 では、エラー文中の em-dash 等で print が
    # UnicodeEncodeError を起こしテスト自体がクラッシュする。標準出力を UTF-8/replace
    # に再構成して落ちないようにする。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    cases = [
        (f"thumb キー必須 (date >= {CUTOFF_DATE})", test_thumb_key_present_after_cutoff),
    ]
    overall_ok = True
    for label, fn in cases:
        errs = fn()
        if errs:
            overall_ok = False
            print(f"FAIL: {label}")
            for e in errs[:20]:
                print(f"  - {e}")
            if len(errs) > 20:
                print(f"  ... and {len(errs) - 20} more")
        else:
            print(f"PASS: {label}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
