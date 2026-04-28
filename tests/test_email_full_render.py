#!/usr/bin/env python3
"""メール HTML が全構造要素を含み、Gmail クリッピング閾値内に収まることを保証する TDD テスト。

検証項目:
1. 全 5 カテゴリ名（為替/AI/IT-Consulting/経済/ゲーム）が HTML に含まれている
2. 各カテゴリのアクセントカラーが含まれている（カテゴリ帯がレンダリングされた証拠）
3. 全 5 セクション（§01〜§05）の見出しが含まれている
4. EDITORIAL / PULL QUOTE / KEY TAKEAWAYS / RELATED ISSUES / NEWS GRASP（フッター）が全て含まれている
5. minify 後の HTML 本文サイズが Gmail の自動クリッピング閾値（102 KB）以内
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

import mock_data
import render_email


GMAIL_CLIP_THRESHOLD = 102 * 1024  # 102 KB（Gmail が「メッセージの一部のみ表示」を出す境界）

REQUIRED_CATEGORY_NAMES = ["為替", "AI", "IT-Consulting", "経済", "ゲーム"]
REQUIRED_CATEGORY_ACCENTS = ["#B8860B", "#2D5BB8", "#2E6B52", "#8E2A19", "#5E3D8C"]
REQUIRED_SECTION_MARKERS = ["§01", "§02", "§03", "§04", "§05"]
REQUIRED_BLOCK_LABELS = [
    "EDITORIAL",                # テーマ考察ヘッダー
    "PULL QUOTE",               # 引用ブロック
    "KEY TAKEAWAYS",            # 結論カード
    "RELATED ISSUES",           # 関連過去号
    "NEWS GRASP",               # フッターのワードマーク
]


def render_for_send() -> str:
    """本番 send 経路と同じ条件（CDN モード + minify）でレンダリング。"""
    render_email.set_cid_mode(False)
    render_email.set_cdn_mode(True)
    return render_email.minify_html(render_email.render_email_html())


def test_all_categories_present(html: str) -> list[str]:
    errs: list[str] = []
    for name in REQUIRED_CATEGORY_NAMES:
        if name not in html:
            errs.append(f"カテゴリ名 「{name}」 が HTML に出現していない")
    return errs


def test_category_accents_present(html: str) -> list[str]:
    errs: list[str] = []
    for accent in REQUIRED_CATEGORY_ACCENTS:
        if accent not in html:
            errs.append(f"アクセント色 {accent} が HTML に出現していない（カテゴリ帯描画ミスの可能性）")
    return errs


def test_reflection_sections_present(html: str) -> list[str]:
    errs: list[str] = []
    for marker in REQUIRED_SECTION_MARKERS:
        if marker not in html:
            errs.append(f"考察セクション「{marker}」が HTML に出現していない")
    return errs


def test_required_blocks_present(html: str) -> list[str]:
    errs: list[str] = []
    for label in REQUIRED_BLOCK_LABELS:
        if label not in html:
            errs.append(f"必須ブロック「{label}」が HTML に出現していない")
    return errs


def test_body_within_gmail_clip_threshold(html: str) -> list[str]:
    errs: list[str] = []
    size = len(html.encode("utf-8"))
    if size > GMAIL_CLIP_THRESHOLD:
        errs.append(
            f"minify 後 HTML が Gmail クリッピング閾値超過: "
            f"{size:,} bytes > {GMAIL_CLIP_THRESHOLD:,} bytes"
            f"（{size / GMAIL_CLIP_THRESHOLD:.1%}）"
            f"\n  → Gmail で受信時にゲーム以降が「メッセージの一部のみ表示」リンクに隠れる"
        )
    return errs


def main() -> int:
    print(f"Mock data: {len(mock_data.CATEGORIES)} categories, "
          f"{sum(len(c['items']) for c in mock_data.CATEGORIES)} articles")
    print()

    html = render_for_send()
    size = len(html.encode("utf-8"))
    print(f"HTML size (minified, send-mode): {size:,} bytes  "
          f"({size / GMAIL_CLIP_THRESHOLD:.0%} of Gmail clip threshold)")
    print()

    cases = [
        ("全カテゴリ名の出現",        test_all_categories_present),
        ("全カテゴリアクセント色の出現", test_category_accents_present),
        ("全 5 考察セクションの出現",  test_reflection_sections_present),
        ("必須ブロックの出現",         test_required_blocks_present),
        ("Gmail クリップ閾値内 (102KB)", test_body_within_gmail_clip_threshold),
    ]
    overall_ok = True
    for label, fn in cases:
        errs = fn(html)
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
