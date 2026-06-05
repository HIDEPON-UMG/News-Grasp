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


def _collect_og_image_basic() -> list[str]:
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


def _collect_twitter_image_basic() -> list[str]:
    errs: list[str] = []
    html = '''<head><meta name="twitter:image" content="https://example.com/tw.jpg"></head>'''
    og, tw = _parse(html)
    if tw != "https://example.com/tw.jpg":
        errs.append(f"twitter:image expected URL, got {tw!r}")
    if og is not None:
        errs.append(f"og:image should be None, got {og!r}")
    return errs


def _collect_twitter_image_src_alias() -> list[str]:
    """旧仕様 twitter:image:src も拾えること。"""
    errs: list[str] = []
    html = '''<head><meta name="twitter:image:src" content="https://example.com/tw2.jpg"></head>'''
    _, tw = _parse(html)
    if tw != "https://example.com/tw2.jpg":
        errs.append(f"twitter:image:src expected URL, got {tw!r}")
    return errs


def _collect_both_meta_present() -> list[str]:
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


def _collect_first_match_wins() -> list[str]:
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


def _collect_head_meta_preferred_over_body() -> list[str]:
    """<head> と <body> の両方に og:image があるときは <head> (= 最初の 1 件) が勝つ。

    かつては <body> 突入で解析停止していたが、SSR 系サイトが SEO meta を body 後方に
    出す対応で停止を外した (2026-05-31)。停止を外しても「最初の og:image が勝つ」ため
    head が先にあれば head が採用され、この契約は維持される。
    """
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


def _collect_no_meta() -> list[str]:
    errs: list[str] = []
    html = '''<html><head><title>x</title></head><body>plain</body></html>'''
    og, tw = _parse(html)
    if og is not None or tw is not None:
        errs.append(f"both should be None, got og={og!r} tw={tw!r}")
    return errs


def _collect_absolutize() -> list[str]:
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


def _collect_looks_non_html() -> list[str]:
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


def _collect_decode_html() -> list[str]:
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


def test_og_image_basic() -> None:
    errs = _collect_og_image_basic()
    assert not errs, "\n".join(errs)


def test_twitter_image_basic() -> None:
    errs = _collect_twitter_image_basic()
    assert not errs, "\n".join(errs)


def test_twitter_image_src_alias() -> None:
    errs = _collect_twitter_image_src_alias()
    assert not errs, "\n".join(errs)


def test_both_meta_present() -> None:
    errs = _collect_both_meta_present()
    assert not errs, "\n".join(errs)


def test_first_match_wins() -> None:
    errs = _collect_first_match_wins()
    assert not errs, "\n".join(errs)


def test_head_meta_preferred_over_body() -> None:
    errs = _collect_head_meta_preferred_over_body()
    assert not errs, "\n".join(errs)


def test_body_only_og_image_found() -> None:
    """<head> に og:image が無く <body> より後ろにしか無い場合でも拾えること。

    Next.js / React SSR (anthropic.com 等) は og:image を <body> 後方に出力する。
    旧 body-stop 実装はこれを取り逃して no_meta に落ち、News-Grasp トップ記事の
    サムネが汎用プレースホルダに化けた (2026-05-31 事故)。回帰を loud に封じる契約。
    """
    html = '''<html><head><title>t</title></head><body>
        <div>article</div>
        <meta property="og:image" content="https://example.com/body-only.jpg">
        <meta name="twitter:image" content="https://example.com/tw-body.jpg">
    </body></html>'''
    og, tw = _parse(html)
    assert og == "https://example.com/body-only.jpg", f"body の og:image を拾えていない: {og!r}"
    assert tw == "https://example.com/tw-body.jpg", f"body の twitter:image を拾えていない: {tw!r}"


def test_no_meta() -> None:
    errs = _collect_no_meta()
    assert not errs, "\n".join(errs)


def test_absolutize() -> None:
    errs = _collect_absolutize()
    assert not errs, "\n".join(errs)


def test_looks_non_html() -> None:
    errs = _collect_looks_non_html()
    assert not errs, "\n".join(errs)


def test_decode_html() -> None:
    errs = _collect_decode_html()
    assert not errs, "\n".join(errs)


def main() -> int:
    cases = [
        ("og:image 抽出 (基本)",            _collect_og_image_basic),
        ("twitter:image 抽出 (基本)",       _collect_twitter_image_basic),
        ("twitter:image:src 旧仕様",         _collect_twitter_image_src_alias),
        ("og + twitter 両取り",              _collect_both_meta_present),
        ("最初の og:image を採用",          _collect_first_match_wins),
        ("<head> 優先 (<body> meta も拾う)",  _collect_head_meta_preferred_over_body),
        ("meta が無いケース",                _collect_no_meta),
        ("URL 絶対化",                       _collect_absolutize),
        ("非 HTML 拡張子のスキップ判定",    _collect_looks_non_html),
        ("HTML decode (utf-8 / cp932)",      _collect_decode_html),
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
