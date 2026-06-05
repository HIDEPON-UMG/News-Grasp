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
import html as _html_lib
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
# メタ行: 真の記事ブロックには 📅 / 🔗 / 📰 のいずれかが含まれる。これが無い `### ` は
# `### KEY TAKEAWAYS` 等のセクション見出しなので和訳対象から除外する。
_META_RE = re.compile(r"📅|🔗|📰")


def _is_english_title(title: str) -> bool:
    """タイトル文字列が「日本語文字を 1 字も含まない」= 英文とみなす。

    ひらがな / カタカナ / CJK / 全角記号を 1 字でも含めば False。
    company 名・URL のみの記事タイトル (英数字+記号) は True 扱いになるが、
    その場合も和訳付与の対象として問題ない (運用上はそういう記事も和訳しておく)。
    """
    return not any(
        "぀" <= c <= "ヿ"
        or "一" <= c <= "鿿"
        or "＀" <= c <= "￯"
        for c in title
    )


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


def test_english_articles_require_ja_callout() -> None:
    """**全 digest md** の「メタ行 (📅/🔗/📰) を持つ英文 `### ...` 記事」には
    `> [!ja]` callout 必須。1 件でも欠落していれば fail。

    これが本ファイルの核心となる Lv1 (illegal state unrepresentable) 契約テスト。
    feedback_check_design_principles Lv4 = 不変条件を 1 件で locked-in する立場。

    2026-06-05 の根本失敗: AI/Mobility/FX/Summary の過去日付 digest に 34 件の
    英文記事が和訳未付与のまま放置されていた事実が、ユーザー指摘の 3 度目で発覚した。
    page-template 等の SSG 側ばかり修正して「日本語表示が出る場所だけ Lv4 で縛り」、
    肝心の「英文記事に和訳を付与する」側を locked-in していなかった。本テストで補う。
    """
    missing: list[str] = []
    for md in sorted(DIGEST_ROOT.rglob("*.md")):
        # DeepDive は構造が違う (テーマ考察記事) ので対象外
        if "DeepDive" in md.name:
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        for block in _ARTICLE_BLOCK_SPLIT.split(text):
            tm = _TITLE_HEAD_RE.match(block)
            if not tm:
                continue
            title = tm.group(2).strip()
            if not _is_english_title(title):
                continue
            # メタ行 (📅/🔗/📰) を持たない `### KEY TAKEAWAYS` 等のセクション見出しは除外
            if not _META_RE.search(block):
                continue
            if not _TITLE_JA_RE.search(block):
                rel = md.relative_to(DIGEST_ROOT).as_posix()
                missing.append(f"  - {rel}: {title[:80]}")

    assert not missing, (
        f"\n英文 ### 記事に `> [!ja]` 和訳 callout が欠落: {len(missing)} 件\n"
        + "\n".join(missing[:30])
        + (f"\n  ... ({len(missing) - 30} 件略)" if len(missing) > 30 else "")
    )


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
            # Jinja autoescape で `'` → `&#39;` 等になるため、escape 後の文字列も
            # マッチ候補にする。生 / quote=False / quote=True / 旧 #39 系の 4 形式を試す。
            ja_variants = {
                title_ja,
                _html_lib.escape(title_ja, quote=False),
                _html_lib.escape(title_ja, quote=True),
                title_ja.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                        .replace("'", "&#39;").replace('"', "&#34;"),
            }
            en_variants = {title_en, _html_lib.escape(title_en, quote=False)}

            en_present = any(v in html for v in en_variants)
            if not en_present:
                continue
            ja_present = any(v in html for v in ja_variants)
            if ja_present:
                continue
            failures.append(
                f"  - {rel}: 「{title_en[:60]}」 → 和訳「{title_ja[:40]}」欠落"
            )

    assert not failures, (
        f"\n和訳出力欠落: {len(failures)} 件\n"
        + "\n".join(failures[:20])
        + (f"\n  ... ({len(failures) - 20} 件略)" if len(failures) > 20 else "")
    )
