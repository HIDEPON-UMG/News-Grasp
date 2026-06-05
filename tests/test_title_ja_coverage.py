"""title_ja 出力カバレッジ契約テスト。

digest md の `> [!ja]` callout で和訳サブタイトルが付与された記事は、
その英文タイトルが SSG 出力 HTML に現れるすべての箇所で、対応する和訳
テキストも同 HTML 内に出力されていなければならない。

これは「テンプレ N 箇所に英文タイトルを出力していて、和訳 span を一部にしか
追加しなかった」class of bugs (= 2026-06-05 home トップ / overview / archive の
出力漏れ) を構造的に封じる locked-in テスト。

feedback_check_design_principles Lv4 = 契約テスト 1 件で不変条件を locked-in する。
個別 smoke (各テンプレ画面ごとの目視) を増やすのではなく、SSG 出力全体に対する
1 つのルールで「英文タイトルが出るなら和訳も同 HTML 内に必ず出る」を縛る。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIGEST_ROOT = ROOT / "digest"
DOCS_ROOT = ROOT / "docs"

# `### [97] Microsoft unveils new AI models ...` — オプショナルなスコア括弧を許容
_TITLE_HEAD_RE = re.compile(r"^###\s*(?:\[(\d+)\]\s*)?(.+?)\s*$", re.MULTILINE)
# `> [!ja] マイクロソフト、…` (Obsidian / GitHub callout)
_TITLE_JA_RE = re.compile(r"^>\s*\[!ja\]\s*(.+?)\s*$", re.MULTILINE)
# 記事ブロック単位で分割 (各 `### ` から次の `### ` 直前まで)
_ARTICLE_BLOCK_SPLIT = re.compile(r"^(?=### )", re.MULTILINE)


def _collect_title_ja_pairs() -> dict[str, str]:
    """digest md から `> [!ja]` callout を持つ記事の (英文タイトル, 和訳) を集める。"""
    pairs: dict[str, str] = {}
    for md in DIGEST_ROOT.rglob("*.md"):
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        for block in _ARTICLE_BLOCK_SPLIT.split(text):
            title_m = _TITLE_HEAD_RE.match(block)
            ja_m = _TITLE_JA_RE.search(block)
            if title_m and ja_m:
                title = title_m.group(2).strip()
                ja = ja_m.group(1).strip()
                pairs[title] = ja
    return pairs


def test_title_ja_pairs_collected_from_digest() -> None:
    """前提条件: digest md に > [!ja] 持ち記事が少なくとも 1 件は存在すること。

    このテスト自体の前提崩壊 (digest 構文変更で正規表現が無効化した等) を検出。
    """
    pairs = _collect_title_ja_pairs()
    assert pairs, "digest/**/*.md に `> [!ja]` callout を持つ記事が 1 件もない"


def test_title_ja_appears_in_all_html_pages() -> None:
    """title_ja を持つ記事の英文タイトルが出る全 HTML で、和訳も同 HTML に出力されていること。

    page-template / category-template / index-template / overview-template /
    archive-template のいずれかで「英文タイトル <h*> 出力 + 和訳 span 漏れ」が
    起きると検出する。
    """
    pairs = _collect_title_ja_pairs()
    if not pairs:
        # 前段テスト test_title_ja_pairs_collected_from_digest で別途 FAIL するため
        # ここではスキップ相当 (空 pass)
        return

    failures: list[str] = []
    for html_path in DOCS_ROOT.rglob("index.html"):
        rel = html_path.relative_to(DOCS_ROOT).as_posix()
        # ビルド産物 / アンダーバー始まり / .git 等は除外
        if rel.startswith("_") or "/_" in rel or ".git" in rel:
            continue
        try:
            html = html_path.read_text(encoding="utf-8")
        except OSError:
            continue
        for title_en, title_ja in pairs.items():
            if title_en in html and title_ja not in html:
                failures.append(
                    f"  - {rel}: 「{title_en[:60]}」 → 和訳「{title_ja[:40]}」欠落"
                )

    assert not failures, (
        f"\n和訳出力欠落: {len(failures)} 件\n"
        + "\n".join(failures[:20])
        + (f"\n  ... ({len(failures) - 20} 件略)" if len(failures) > 20 else "")
    )
