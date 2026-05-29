#!/usr/bin/env python3
"""PWA 化 (2026-05-26) の構造契約テスト。

build_all(full=True) が生成する全テンプレ (home / overview / summary / page /
archive / category) の <head> に以下の PWA snippet が **6 種すべて** で揃って
いることを pin する。1 つのテンプレで snippet が抜けると WebAPK install criteria
が落ちるため。

加えて、docs/ 直下の以下静的アセットが揃っていることも検証する:
    - docs/manifest.webmanifest  (valid JSON, scope=/News-Grasp/)
    - docs/sw.js                  (SW_VERSION と scope の文字列を含む)
    - docs/offline.html           (SW fallback)
    - docs/assets/icons/icon-{192,512,maskable-512}.png

実行: pytest tests/test_pwa_meta.py -v
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.generate_pages import (  # noqa: E402
    _collect_entries,
    build_all,
    build_all_overviews,
    build_all_summaries,
    build_archive,
    build_category_pages,
    build_index,
    scan_digests,
)


PWA_REQUIRED_SNIPPETS = (
    '<link rel="manifest" href="/News-Grasp/manifest.webmanifest">',
    '<meta name="theme-color" content="#181C2A">',
    '<meta name="apple-mobile-web-app-capable" content="yes">',
    '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">',
    '<meta name="apple-mobile-web-app-title" content="News Grasp">',
    'navigator.serviceWorker.register',
    "'/News-Grasp/sw.js'",
)


# ============================================================
# fixture: 実 digest を tmp に build
# ============================================================

@pytest.fixture(scope="module")
def built_root(tmp_path_factory) -> Path:
    """generate_pages.main(--full) と同等に tmp へ全種ページを build し、docs root を返す。"""
    docs_root = tmp_path_factory.mktemp("pwa")
    written = build_all(full=True, docs_root=docs_root)
    assert written, "build_all が 0 件: digest が無い、または build に失敗"
    entries = _collect_entries(scan_digests())
    assert entries, "_collect_entries が空: digest scan に失敗"
    build_index(entries, docs_root)
    build_category_pages(entries, docs_root)
    build_archive(entries, docs_root)
    build_all_overviews(entries, docs_root)
    build_all_summaries(entries, docs_root)
    return docs_root


def _pick_one(docs_root: Path, *patterns: str) -> Path:
    """patterns のいずれかにマッチする最初の html を返す。"""
    for pat in patterns:
        for p in docs_root.glob(pat):
            return p
    raise AssertionError(f"none of {patterns} matched under {docs_root}")


# ============================================================
# 各テンプレ生成 HTML に PWA snippet が揃っているか
# ============================================================

def _assert_pwa_snippets(html: str, where: str) -> None:
    for snippet in PWA_REQUIRED_SNIPPETS:
        assert snippet in html, f"{where}: PWA snippet missing → {snippet!r}"


def test_pwa_snippet_in_home(built_root: Path):
    html = (built_root / "index.html").read_text(encoding="utf-8")
    _assert_pwa_snippets(html, "home (index.html)")


def test_pwa_snippet_in_overview(built_root: Path):
    p = _pick_one(built_root, "2026-*/index.html")
    _assert_pwa_snippets(p.read_text(encoding="utf-8"), f"overview ({p.name})")


def test_pwa_snippet_in_summary(built_root: Path):
    p = _pick_one(built_root, "2026-*/summary/index.html")
    _assert_pwa_snippets(p.read_text(encoding="utf-8"), f"summary ({p.relative_to(built_root)})")


def test_pwa_snippet_in_category_detail(built_root: Path):
    # /{cat}/{YYYY-MM-DD}/index.html (Variant B Magazine)
    p = _pick_one(
        built_root,
        "fx/2026-*/index.html",
        "ai/2026-*/index.html",
        "it/2026-*/index.html",
        "economy/2026-*/index.html",
        "game/2026-*/index.html",
    )
    _assert_pwa_snippets(p.read_text(encoding="utf-8"), f"category detail ({p.relative_to(built_root)})")


def test_pwa_snippet_in_archive(built_root: Path):
    p = built_root / "archive" / "index.html"
    assert p.exists(), f"archive index missing: {p}"
    _assert_pwa_snippets(p.read_text(encoding="utf-8"), "archive")


def test_pwa_snippet_in_category_archive(built_root: Path):
    p = _pick_one(
        built_root,
        "fx/index.html",
        "ai/index.html",
        "it/index.html",
        "economy/index.html",
        "game/index.html",
        "summary/index.html",
    )
    _assert_pwa_snippets(p.read_text(encoding="utf-8"), f"category archive ({p.relative_to(built_root)})")


# ============================================================
# 静的アセット (manifest / sw / offline / icons)
# ============================================================

def test_manifest_is_valid_json_with_scope():
    """docs/manifest.webmanifest が JSON で start_url=/News-Grasp/。"""
    p = ROOT / "docs" / "manifest.webmanifest"
    assert p.exists(), f"manifest missing: {p}"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["scope"] == "/News-Grasp/", f"scope must be /News-Grasp/, got {data['scope']!r}"
    assert data["start_url"] == "/News-Grasp/", f"start_url must be /News-Grasp/, got {data['start_url']!r}"
    assert data["display"] == "standalone"
    assert data["theme_color"] == "#181C2A"
    assert data["background_color"] == "#F0EBE0"
    # any purpose 192 + 512 + maskable 512
    sizes_and_purposes = {(i["sizes"], i["purpose"]) for i in data["icons"]}
    assert ("192x192", "any") in sizes_and_purposes, "any-purpose 192x192 icon missing"
    assert ("512x512", "any") in sizes_and_purposes, "any-purpose 512x512 icon missing"
    assert ("512x512", "maskable") in sizes_and_purposes, "maskable 512x512 icon missing"


def test_sw_contains_version_and_scope():
    """docs/sw.js が SW_VERSION と scope prefix を持つ (キャッシュ無効化と境界)。"""
    p = ROOT / "docs" / "sw.js"
    assert p.exists(), f"sw.js missing: {p}"
    text = p.read_text(encoding="utf-8")
    assert "SW_VERSION" in text, "SW_VERSION 定数が無い (キャッシュ無効化に必須)"
    assert "/News-Grasp/" in text, "SCOPE_PREFIX 文字列が無い"
    assert "addEventListener('fetch'" in text, "fetch handler が無い"


def test_offline_html_exists_and_links_manifest():
    """docs/offline.html が SW fallback として存在し、最低限 navy/cream を使う。"""
    p = ROOT / "docs" / "offline.html"
    assert p.exists(), f"offline.html missing: {p}"
    text = p.read_text(encoding="utf-8")
    assert "manifest.webmanifest" in text
    assert "#181C2A" in text or "navy" in text.lower()


def test_pwa_icons_exist():
    """3 サイズの icon が docs/assets/icons/ に存在する。"""
    icons_dir = ROOT / "docs" / "assets" / "icons"
    for name in ("icon-192.png", "icon-512.png", "icon-maskable-512.png"):
        p = icons_dir / name
        assert p.exists(), f"icon missing: {p}"
        # PNG signature 確認 (最低限 0 byte ファイルを防ぐ)
        head = p.read_bytes()[:8]
        assert head == b"\x89PNG\r\n\x1a\n", f"{name} is not a valid PNG (head={head!r})"


# ============================================================
# Web Push (2026-05-29): SW ハンドラ / クライアント / 購読 UI
# ============================================================

def test_sw_has_push_handlers():
    """docs/sw.js に push と notificationclick のハンドラがある。

    どちらか欠けると、push を受けても通知が出ない / タップで開かない。
    """
    text = (ROOT / "docs" / "sw.js").read_text(encoding="utf-8")
    assert "addEventListener('push'" in text, "push ハンドラが無い（通知が表示されない）"
    assert "addEventListener('notificationclick'" in text, \
        "notificationclick ハンドラが無い（タップで記事を開けない）"
    assert "showNotification" in text, "showNotification 呼び出しが無い"


def test_push_js_has_real_vapid_key_and_subscribe():
    """docs/push.js に実在の VAPID 公開鍵と subscribe ロジックがある。

    VAPID_PUBLIC_KEY が空 / プレースホルダのまま deploy されると、
    pushManager.subscribe が必ず失敗する（誰も購読できない）ため pin する。
    """
    text = (ROOT / "docs" / "push.js").read_text(encoding="utf-8")
    assert "pushManager.subscribe" in text
    assert "userVisibleOnly" in text, "userVisibleOnly:true は Web Push 仕様上必須"

    # VAPID_PUBLIC_KEY = '....'; から鍵を抜き、形式を検証
    m = re.search(r"VAPID_PUBLIC_KEY\s*=\s*'([^']*)'", text)
    assert m, "VAPID_PUBLIC_KEY 定数が見つからない"
    key = m.group(1)
    # P-256 非圧縮点(65byte) の base64url は 87 文字・先頭 'B'（0x04 プレフィックス）
    assert key.startswith("B"), f"VAPID 公開鍵の形式が不正（先頭が B でない）: {key[:8]!r}"
    assert len(key) >= 80, f"VAPID 公開鍵が短すぎる（プレースホルダの疑い）: len={len(key)}"


def test_push_js_is_self_service_via_worker():
    """購読はユーザー操作だけで完結する = Worker へ自動 POST する実装である。

    手動で JSON を管理人へ渡す旧 UX に退行していないことを pin する
    （= 今回の要件「ユーザー操作だけで設定完結」を構造的に守る）。
    """
    text = (ROOT / "docs" / "push.js").read_text(encoding="utf-8")
    assert re.search(r"WORKER_URL\s*=\s*'", text), "WORKER_URL 定数が無い（購読の自動保存先）"
    assert "'/subscribe'" in text, "Worker への購読 POST (/subscribe) が無い"
    assert "fetch(" in text, "Worker への送信 (fetch) が無い"
    # 旧・手動コピー UX の痕跡が残っていないこと
    assert "管理人にお渡し" not in text, "手動 JSON 渡しの旧 UX が残っている"
    assert "push-sub-json" not in text, "手動コピー用 textarea 参照が残っている"


def test_home_has_push_subscribe_ui(built_root: Path):
    """生成された Home に購読ボタンと push.js 読込がある。"""
    html = (built_root / "index.html").read_text(encoding="utf-8")
    assert 'id="push-subscribe-btn"' in html, "購読ボタンが Home に無い"
    assert "/News-Grasp/push.js" in html, "push.js の読込が Home に無い"
