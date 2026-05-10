#!/usr/bin/env python3
"""tools/fetch_ogp.py の単体テスト。

HTTP レイヤーは tests/test_fetch_ogp_smoke.py の実機スモークに任せ、
ここでは HTML パース / URL 絶対化 / 拡張子判定 / decode の純関数だけ検証する。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import fetch_ogp  # type: ignore


def _parse(html: str) -> tuple[str | None, str | None]:
    parser = fetch_ogp._OGPParser()
    parser.feed_until_stop(html)
    return parser.og_image, parser.twitter_image


def test_og_image_basic() -> list[str]:
    errs: list[str] = []
    html = '''<html><head>
        <meta property="og:image" content="https://example.com/og.jpg">
    </head><body>...</body></html>'''
    og, tw = _parse(html)
    if og != "https://example.com/og.jpg":
        errs.append(f"og:image expected absolute URL, got {og!r}")
    if tw is not None:
        errs.append(f"twitter:image should be None, got {tw!r}")
    return errs


def test_twitter_image_basic() -> list[str]:
    errs: list[str] = []
    html = '''<head><meta name="twitter:image" content="https://example.com/tw.jpg"></head>'''
    og, tw = _parse(html)
    if tw != "https://example.com/tw.jpg":
        errs.append(f"twitter:image expected URL, got {tw!r}")
    if og is not None:
        errs.append(f"og:image should be None, got {og!r}")
    return errs


def test_twitter_image_src_alias() -> list[str]:
    """旧仕様 twitter:image:src も拾えること。"""
    errs: list[str] = []
    html = '''<head><meta name="twitter:image:src" content="https://example.com/tw2.jpg"></head>'''
    _, tw = _parse(html)
    if tw != "https://example.com/tw2.jpg":
        errs.append(f"twitter:image:src expected URL, got {tw!r}")
    return errs


def test_both_meta_present() -> list[str]:
    errs: list[str] = []
    html = '''<head>
        <meta property="og:image" content="https://example.com/og.jpg">
        <meta name="twitter:image" content="https://example.com/tw.jpg">
    </head>'''
    og, tw = _parse(html)
    if og != "https://example.com/og.jpg":
        errs.append(f"og expected, got {og!r}")
    if tw != "https://example.com/tw.jpg":
        errs.append(f"tw expected, got {tw!r}")
    return errs


def test_first_match_wins() -> list[str]:
    """同じプロパティが複数あるとき、最初の値だけ採用。"""
    errs: list[str] = []
    html = '''<head>
        <meta property="og:image" content="https://example.com/first.jpg">
        <meta property="og:image" content="https://example.com/second.jpg">
    </head>'''
    og, _ = _parse(html)
    if og != "https://example.com/first.jpg":
        errs.append(f"first og:image should win, got {og!r}")
    return errs


def test_stops_at_body() -> list[str]:
    """<body> に入ったら以降の <meta> は無視。"""
    errs: list[str] = []
    html = '''<head>
        <meta property="og:image" content="https://example.com/head.jpg">
    </head><body>
        <meta property="og:image" content="https://example.com/body.jpg">
    </body>'''
    og, _ = _parse(html)
    if og != "https://example.com/head.jpg":
        errs.append(f"only head meta should win, got {og!r}")
    return errs


def test_no_meta() -> list[str]:
    errs: list[str] = []
    html = '''<html><head><title>x</title></head><body>plain</body></html>'''
    og, tw = _parse(html)
    if og is not None or tw is not None:
        errs.append(f"both should be None, got og={og!r} tw={tw!r}")
    return errs


def test_absolutize() -> list[str]:
    errs: list[str] = []
    f = fetch_ogp._absolutize
    cases = [
        # (base, given, expected)
        ("https://x.com/a/b", "https://cdn.com/og.jpg", "https://cdn.com/og.jpg"),
        ("https://x.com/a/b", "/static/og.jpg",         "https://x.com/static/og.jpg"),
        ("https://x.com/a/b", "og.jpg",                  "https://x.com/a/og.jpg"),
        ("https://x.com/a/b", "//cdn.com/og.jpg",        "https://cdn.com/og.jpg"),
        ("https://x.com/a/b", None,                      None),
        ("https://x.com/a/b", "",                        None),
    ]
    for base, given, expected in cases:
        got = f(base, given)
        if got != expected:
            errs.append(f"_absolutize({base!r}, {given!r}) expected {expected!r}, got {got!r}")
    return errs


def test_looks_non_html() -> list[str]:
    errs: list[str] = []
    f = fetch_ogp._looks_non_html
    truthy = [
        "https://example.com/a.pdf",
        "https://example.com/path/to/doc.docx",
        "https://example.com/file.PPTX",
    ]
    falsy = [
        "https://example.com/article",
        "https://example.com/article.html",
        "https://example.com/article?id=123",
    ]
    for u in truthy:
        if not f(u):
            errs.append(f"_looks_non_html({u!r}) expected True")
    for u in falsy:
        if f(u):
            errs.append(f"_looks_non_html({u!r}) expected False")
    return errs


def test_decode_html() -> list[str]:
    errs: list[str] = []
    raw = "テスト本文".encode("utf-8")
    out = fetch_ogp._decode_html(raw, "text/html; charset=UTF-8")
    if out != "テスト本文":
        errs.append(f"utf-8 decode failed: got {out!r}")

    raw_cp932 = "テスト本文".encode("cp932")
    out2 = fetch_ogp._decode_html(raw_cp932, "text/html; charset=Shift_JIS")
    if out2 != "テスト本文":
        errs.append(f"cp932 decode failed: got {out2!r}")
    return errs


def main() -> int:
    cases = [
        ("og:image 抽出 (基本)",            test_og_image_basic),
        ("twitter:image 抽出 (基本)",       test_twitter_image_basic),
        ("twitter:image:src 旧仕様",         test_twitter_image_src_alias),
        ("og + twitter 両取り",              test_both_meta_present),
        ("最初の og:image を採用",          test_first_match_wins),
        ("<body> 突入で停止",                test_stops_at_body),
        ("meta が無いケース",                test_no_meta),
        ("URL 絶対化",                       test_absolutize),
        ("非 HTML 拡張子のスキップ判定",    test_looks_non_html),
        ("HTML decode (utf-8 / cp932)",      test_decode_html),
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
