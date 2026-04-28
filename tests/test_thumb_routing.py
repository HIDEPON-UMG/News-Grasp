#!/usr/bin/env python3
"""thumb_url() のルーティングテスト（TDD）。

「OGP URL が取れている記事」と「NG フォールバック」で、
cid: モードでの inline registry 投入挙動が正しく分岐するか検証する。

期待:
1. item.thumb が non-null（OGP URL）なら cid_mode 関係なく URL 文字列を返し、
   inline registry には何も登録しない（→ メール本文サイズに加算ゼロ）
2. item.thumb が None なら cid_mode=True で `cid:KEY` を返し、
   registry にカテゴリ別 NG の data URI を登録する
3. 同じ NG キーが複数記事で使われても registry は de-dup して 1 エントリのみ
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

import render_email


def test_ogp_url_passes_through() -> list[str]:
    errors: list[str] = []
    render_email.set_cid_mode(True)
    item_with_ogp = {"thumb": "https://example.com/article-og.jpg"}

    url = render_email.thumb_url(item_with_ogp, "fx", is_top=True)
    if url != "https://example.com/article-og.jpg":
        errors.append(f"OGP URL passthrough 失敗: 返却={url!r}")

    inline = render_email.get_inline_images()
    if len(inline) != 0:
        errors.append(f"OGP URL 記事は registry に登録すべきでない: {list(inline.keys())}")
    return errors


def test_ng_fallback_registers_cid() -> list[str]:
    errors: list[str] = []
    render_email.set_cid_mode(True)
    item_no_thumb = {"thumb": None}

    cid_top = render_email.thumb_url(item_no_thumb, "fx", is_top=True)
    if cid_top != "cid:ng-thumb-fx":
        errors.append(f"FEATURED の cid 形式が不正: {cid_top!r}")

    cid_side = render_email.thumb_url(item_no_thumb, "ai", is_top=False)
    if cid_side != "cid:ng-thumb-common-ai":
        errors.append(f"サイドの cid 形式が不正: {cid_side!r}")

    inline = render_email.get_inline_images()
    if "ng-thumb-fx" not in inline or "ng-thumb-common-ai" not in inline:
        errors.append(f"必要キーが registry にない: {list(inline.keys())}")
    for key, uri in inline.items():
        if not uri.startswith("data:image/jpeg;base64,"):
            errors.append(f"{key}: data URI 形式違反")
    return errors


def test_dedup() -> list[str]:
    errors: list[str] = []
    render_email.set_cid_mode(True)
    item_no_thumb = {"thumb": None}

    # 同じ NG キーを 5 回呼ぶ → registry は 1 エントリのみのはず
    for _ in range(5):
        render_email.thumb_url(item_no_thumb, "economy", is_top=False)

    inline = render_email.get_inline_images()
    if list(inline.keys()) != ["ng-thumb-common-economy"]:
        errors.append(f"de-dup 失敗: {list(inline.keys())}")
    return errors


def test_mixed_realistic_mix() -> list[str]:
    """OGP 5 件 + NG 5 件のリアル混合。inline は 5 → ぐっと減る想定。"""
    errors: list[str] = []
    render_email.set_cid_mode(True)

    items = [
        {"thumb": "https://reuters.com/og/1.jpg"},
        {"thumb": "https://nikkei.com/og/2.jpg"},
        {"thumb": None},
        {"thumb": "https://bloomberg.com/og/4.jpg"},
        {"thumb": None},
    ]
    for i, it in enumerate(items):
        render_email.thumb_url(it, "fx", is_top=(i == 0))

    inline = render_email.get_inline_images()
    # 期待: 1 番目以外で NG 落ちした 2 件は同じ ng-thumb-common-fx に集約
    if set(inline.keys()) != {"ng-thumb-common-fx"}:
        errors.append(f"混合シナリオ: registry={list(inline.keys())}, 期待=ng-thumb-common-fx のみ")
    return errors


def main() -> int:
    cases = [
        ("OGP URL passthrough",      test_ogp_url_passes_through),
        ("NG fallback → cid 登録",   test_ng_fallback_registers_cid),
        ("同一キー de-dup",          test_dedup),
        ("OGP/NG 混合シナリオ",      test_mixed_realistic_mix),
    ]
    overall_ok = True
    for label, fn in cases:
        errs = fn()
        if errs:
            overall_ok = False
            print(f"FAIL: {label}")
            for e in errs:
                print(f"  - {e}")
        else:
            print(f"PASS: {label}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
