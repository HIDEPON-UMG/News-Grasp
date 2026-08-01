#!/usr/bin/env python3
"""tools/verify_reporter_output.py の契約テスト (Newsroom Phase 1)。

# 検証する「なぜ重要か」

記者サブエージェントの出力 (records.jsonl / search_audit / digest md) を編集長が
マージする前に機械検証する境界。LLM の自己申告を信用せず、5 項目それぞれが PASS と
FAIL の両系で正しく判定されることを locked-in する:

  1. records.jsonl の各行 schema PASS かつ date == 号日
  2. 件数 1〜5 件。5 件未満なら quality_shortfall_reason 必須
  3. search_audit の存在 + 必須フィールド
  4. digest md カード数 == records 件数
  5. digest md に ng-thumb-common- 直書きなし

実行:
  pytest tests/test_verify_reporter_output.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.verify_reporter_output import verify  # noqa: E402

ISSUE = "2026-06-11"
CAT = "ai"
GENRE = "AI"


def _record(idx: int, *, date_v: str = ISSUE, extra: dict | None = None) -> dict:
    """validate_record を通過する canonical な記者レコードを作る。"""
    rec = {
        "date": date_v,
        "seen_at": f"{ISSUE}T06:00:00+09:00",
        "genre": "AI",
        "title": f"Sample AI story number {idx}",
        "title_ja": f"AI サンプル記事 {idx}",
        "url": f"https://example.com/ai/story-{idx}",
        "thumb": f"https://example.com/ai/thumb-{idx}.png",
        "published_date": ISSUE,
        "date_evidence_source": "fixture",
    }
    if extra:
        rec.update(extra)
    return rec


def _card(idx: int) -> str:
    """digest md のカード 1 枚 (### [score] title ... 形式) を作る。"""
    return (
        f"### [88] Sample AI story number {idx}\n\n"
        f"📅 {ISSUE} 06:00 · 📰 Example · 🔗 [元記事](https://example.com/ai/story-{idx})\n\n"
        f"#cat/ai #co/example #score/高\n\n"
        f"![thumb](https://example.com/ai/thumb-{idx}.png)\n\n"
        f"- 本文 bullet {idx}。\n"
    )


def _digest_md(n_cards: int, *, thumb_fallback: bool = False) -> str:
    """n_cards 枚のカードを持つ digest md 本文を組み立てる。"""
    fm = (
        "---\n"
        f'title: "News Grasp #20260611 — AI"\n'
        f"date: {ISSUE}\n"
        "category: AI\n"
        "categoryId: ai\n"
        "---\n\n"
        "# 🤖 AI — AI\n\n"
    )
    cards = []
    for i in range(1, n_cards + 1):
        card = _card(i)
        if thumb_fallback and i == 1:
            # 共通サムネ fallback を直書き (= 検査 5 で FAIL すべき)
            card = card.replace(
                f"https://example.com/ai/thumb-1.png",
                "https://raw.githubusercontent.com/x/y/main/ng-thumb-common-ai.jpg",
            )
        cards.append(card)
    return fm + "\n---\n".join(cards)


def _setup(
    tmp_path: Path,
    *,
    records: list[dict],
    audit: dict | None,
    digest_cards: int,
    thumb_fallback: bool = False,
    write_audit: bool = True,
    write_digest: bool = True,
) -> Path:
    """tmp repo に records.jsonl / search_audit / digest md を配置して repo root を返す。"""
    repo = tmp_path
    # records.jsonl
    rec_dir = repo / "tmp" / "newsroom" / ISSUE
    rec_dir.mkdir(parents=True)
    with (rec_dir / f"{CAT}.records.jsonl").open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    # search_audit
    if write_audit:
        audit_dir = repo / "data" / "search_audit" / ISSUE
        audit_dir.mkdir(parents=True)
        if audit is None:
            audit = {
                "date": ISSUE,
                "category_id": CAT,
                "queries": ["q1", "q2", "q3"],
                "raw_results_total": 25,
                "candidates_total": 8,
                "selected_total": len(records),
            }
        (audit_dir / f"{CAT}.json").write_text(
            json.dumps(audit, ensure_ascii=False), encoding="utf-8"
        )
    # digest md
    if write_digest:
        digest_dir = repo / "digest" / GENRE
        digest_dir.mkdir(parents=True)
        (digest_dir / f"{ISSUE}-{GENRE}.md").write_text(
            _digest_md(digest_cards, thumb_fallback=thumb_fallback), encoding="utf-8"
        )
    return repo


# ── 全 PASS (基準ケース) ──────────────────────────────────────────────────────


def test_all_pass(tmp_path: Path):
    """5 項目すべて満たす記者出力は errs 空 (= exit 0 相当)。"""
    recs = [_record(i) for i in range(1, 6)]  # 5 件
    repo = _setup(tmp_path, records=recs, audit=None, digest_cards=5)
    errs = verify(repo_root=repo, issue_date=ISSUE, category=CAT)
    assert errs == [], f"基準ケースは全 PASS のはず: {errs}"


# ── 1. records schema / date 整合 ────────────────────────────────────────────


def test_fail_record_date_mismatch(tmp_path: Path):
    """record の date が号日と違えば FAIL。"""
    recs = [_record(i) for i in range(1, 5)]
    recs.append(_record(5, date_v="2026-06-10"))  # date が号日とズレ
    repo = _setup(tmp_path, records=recs, audit=None, digest_cards=5)
    errs = verify(repo_root=repo, issue_date=ISSUE, category=CAT)
    assert any("号日" in e for e in errs), f"date 不整合を検出するはず: {errs}"


def test_fail_record_schema_violation(tmp_path: Path):
    """record の schema 違反 (thumb 欠落) を検出する。"""
    recs = [_record(i) for i in range(1, 5)]
    bad = _record(5)
    del bad["thumb"]
    recs.append(bad)
    repo = _setup(tmp_path, records=recs, audit=None, digest_cards=5)
    errs = verify(repo_root=repo, issue_date=ISSUE, category=CAT)
    assert any("schema" in e for e in errs), f"schema 違反を検出するはず: {errs}"


def test_fail_record_without_date_evidence_source(tmp_path: Path):
    """published_date だけで date_evidence_source が無い記者 record は FAIL。"""
    recs = [_record(i) for i in range(1, 6)]
    recs[0].pop("date_evidence_source")
    repo = _setup(tmp_path, records=recs, audit=None, digest_cards=5)
    errs = verify(repo_root=repo, issue_date=ISSUE, category=CAT)
    assert any("date_evidence_source" in e for e in errs), (
        f"date_evidence_source 欠落を検出するはず: {errs}"
    )


@pytest.mark.parametrize("source", ["rss-pubDate", "rss_pubDate", "google-news-rss"])
def test_fail_record_using_rss_timestamp_as_publication_evidence(
    tmp_path: Path,
    source: str,
):
    """RSS掲載時刻を元記事の公開日根拠へ読み替えた record は FAIL。"""
    recs = [_record(i) for i in range(1, 6)]
    recs[0]["date_evidence_source"] = source
    repo = _setup(tmp_path, records=recs, audit=None, digest_cards=5)
    errs = verify(repo_root=repo, issue_date=ISSUE, category=CAT)
    assert any("RSS" in e and "date_evidence_source" in e for e in errs), (
        f"RSS時刻の誤採用を検出するはず: {errs}"
    )


def test_fail_google_news_rss_url_in_records(tmp_path: Path):
    """Google News RSS URL のままなら元記事 URL 未解決として FAIL。"""
    recs = [_record(i) for i in range(1, 6)]
    recs[0]["url"] = "https://news.google.com/rss/articles/CBMiExample?oc=5"
    recs[0]["url_norm"] = "news.google.com/rss/articles/cbmiexample"
    repo = _setup(tmp_path, records=recs, audit=None, digest_cards=5)
    errs = verify(repo_root=repo, issue_date=ISSUE, category=CAT)
    assert any("Google News RSS URL" in e for e in errs), (
        f"Google News RSS URL を検出するはず: {errs}"
    )


def test_fail_homepage_rounded_url_in_records(tmp_path: Path):
    """媒体トップやカテゴリトップに丸まった URL は記事 URL ではないため FAIL。"""
    recs = [_record(i) for i in range(1, 6)]
    recs[0]["url"] = "https://www.nikkei.com/"
    repo = _setup(tmp_path, records=recs, audit=None, digest_cards=5)
    errs = verify(repo_root=repo, issue_date=ISSUE, category=CAT)
    assert any("媒体トップまたはカテゴリトップ" in e for e in errs), (
        f"丸まり URL を検出するはず: {errs}"
    )


def test_fail_all_null_thumbnails_in_records(tmp_path: Path):
    """記者 1 カテゴリの全記事が thumb=null なら、低品質な一括 fallback を招くため FAIL。"""
    recs = [_record(i, extra={"thumb": None}) for i in range(1, 6)]
    repo = _setup(tmp_path, records=recs, audit=None, digest_cards=5)
    errs = verify(repo_root=repo, issue_date=ISSUE, category=CAT)
    assert any("thumb が全件 null" in e for e in errs), (
        f"全件 thumb=null を検出するはず: {errs}"
    )


def test_fail_news_grasp_self_reference_thumbnail_in_records(tmp_path: Path):
    """記事 thumb に News-Grasp 自己参照 URL を保存したら、個別記事サムネではないため FAIL。"""
    recs = [_record(i) for i in range(1, 6)]
    recs[0]["thumb"] = "https://hidepon-umg.github.io/News-Grasp/assets/og/ai.jpg"
    repo = _setup(tmp_path, records=recs, audit=None, digest_cards=5)

    errs = verify(repo_root=repo, issue_date=ISSUE, category=CAT)

    assert any("News-Grasp 自己参照 thumb" in e for e in errs), (
        f"News-Grasp 自己参照 thumb を検出するはず: {errs}"
    )


def test_fail_google_news_proxy_thumbnail_in_records(tmp_path: Path):
    """Google News 代理サムネを記者 record の thumb として保存したら FAIL。"""
    recs = [_record(i) for i in range(1, 6)]
    recs[0]["thumb"] = "https://lh3.googleusercontent.com/J6_proxy=s0-w300-rw"
    repo = _setup(tmp_path, records=recs, audit=None, digest_cards=5)

    errs = verify(repo_root=repo, issue_date=ISSUE, category=CAT)

    assert any("Google News 代理サムネ" in e for e in errs), (
        f"Google News 代理サムネを検出するはず: {errs}"
    )


# ── 2. 件数 1〜5 + quality_shortfall_reason ──────────────────────────────────


def test_pass_shortfall_with_reason(tmp_path: Path):
    """3 件 (5 件未満) でも quality_shortfall_reason があれば PASS。"""
    recs = [_record(i) for i in range(1, 4)]
    recs[0]["quality_shortfall_reason"] = "低ニュース性候補を除外したため"
    repo = _setup(tmp_path, records=recs, audit=None, digest_cards=3)
    errs = verify(repo_root=repo, issue_date=ISSUE, category=CAT)
    assert errs == [], f"理由付き不足は PASS のはず: {errs}"


def test_fail_shortfall_without_reason(tmp_path: Path):
    """3 件 (5 件未満) で quality_shortfall_reason が無ければ FAIL。"""
    recs = [_record(i) for i in range(1, 4)]
    repo = _setup(tmp_path, records=recs, audit=None, digest_cards=3)
    errs = verify(repo_root=repo, issue_date=ISSUE, category=CAT)
    assert any("quality_shortfall_reason" in e for e in errs), (
        f"理由なし不足を検出するはず: {errs}"
    )


def test_fail_too_many_records(tmp_path: Path):
    """6 件 (上限 5 件超過) は FAIL。"""
    recs = [_record(i) for i in range(1, 7)]
    repo = _setup(tmp_path, records=recs, audit=None, digest_cards=6)
    errs = verify(repo_root=repo, issue_date=ISSUE, category=CAT)
    assert any("上限 5 件" in e for e in errs), f"件数超過を検出するはず: {errs}"


# ── 3. search_audit ──────────────────────────────────────────────────────────


def test_fail_search_audit_missing(tmp_path: Path):
    """search_audit ファイルが無ければ FAIL。"""
    recs = [_record(i) for i in range(1, 6)]
    repo = _setup(tmp_path, records=recs, audit=None, digest_cards=5, write_audit=False)
    errs = verify(repo_root=repo, issue_date=ISSUE, category=CAT)
    assert any("search_audit" in e and "存在しない" in e for e in errs), (
        f"search_audit 不在を検出するはず: {errs}"
    )


def test_fail_search_audit_missing_field(tmp_path: Path):
    """search_audit に必須フィールドが欠けていれば FAIL。"""
    recs = [_record(i) for i in range(1, 6)]
    audit = {
        "date": ISSUE,
        "category_id": CAT,
        "queries": ["q1", "q2", "q3"],
        # raw_results_total / candidates_total / selected_total を欠落させる
    }
    repo = _setup(tmp_path, records=recs, audit=audit, digest_cards=5)
    errs = verify(repo_root=repo, issue_date=ISSUE, category=CAT)
    assert any("必須フィールド欠落" in e for e in errs), (
        f"必須フィールド欠落を検出するはず: {errs}"
    )


# ── 4. digest カード数 == records 件数 ───────────────────────────────────────


def test_fail_card_count_mismatch(tmp_path: Path):
    """digest カード数が records 件数と違えば FAIL。"""
    recs = [_record(i) for i in range(1, 6)]  # 5 件
    repo = _setup(tmp_path, records=recs, audit=None, digest_cards=4)  # カードは 4 枚
    errs = verify(repo_root=repo, issue_date=ISSUE, category=CAT)
    assert any("カード数" in e for e in errs), f"カード数不一致を検出するはず: {errs}"


# ── 5. ng-thumb-common- 直書き禁止 ───────────────────────────────────────────


def test_fail_thumb_fallback_in_digest(tmp_path: Path):
    """digest md に ng-thumb-common- が直書きされていれば FAIL。"""
    recs = [_record(i) for i in range(1, 6)]
    repo = _setup(tmp_path, records=recs, audit=None, digest_cards=5, thumb_fallback=True)
    errs = verify(repo_root=repo, issue_date=ISSUE, category=CAT)
    assert any("ng-thumb-common-" in e for e in errs), (
        f"共通サムネ直書きを検出するはず: {errs}"
    )


def test_fail_news_grasp_self_reference_thumbnail_in_digest(tmp_path: Path):
    """digest md の thumb に News-Grasp 自己参照 URL があれば FAIL。"""
    recs = [_record(i) for i in range(1, 6)]
    repo = _setup(tmp_path, records=recs, audit=None, digest_cards=5)
    digest = repo / "digest" / GENRE / f"{ISSUE}-{GENRE}.md"
    digest.write_text(
        digest.read_text(encoding="utf-8").replace(
            "https://example.com/ai/thumb-1.png",
            "https://hidepon-umg.github.io/News-Grasp/assets/og/ai.jpg",
        ),
        encoding="utf-8",
    )

    errs = verify(repo_root=repo, issue_date=ISSUE, category=CAT)

    assert any("News-Grasp 自己参照 thumb" in e for e in errs), (
        f"News-Grasp 自己参照 thumb を検出するはず: {errs}"
    )


def test_fail_google_news_proxy_thumbnail_in_digest(tmp_path: Path):
    """digest md の thumb に Google News 代理サムネ URL があれば FAIL。"""
    recs = [_record(i) for i in range(1, 6)]
    repo = _setup(tmp_path, records=recs, audit=None, digest_cards=5)
    digest = repo / "digest" / GENRE / f"{ISSUE}-{GENRE}.md"
    digest.write_text(
        digest.read_text(encoding="utf-8").replace(
            "https://example.com/ai/thumb-1.png",
            "https://lh3.googleusercontent.com/J6_proxy=s0-w300-rw",
        ),
        encoding="utf-8",
    )

    errs = verify(repo_root=repo, issue_date=ISSUE, category=CAT)

    assert any("Google News 代理サムネ" in e for e in errs), (
        f"Google News 代理サムネを検出するはず: {errs}"
    )


def test_fatal_category_with_path_traversal():
    """--category にパス区切り等を含む値は exit 2 で拒否する契約（トラバーサル予防）。"""
    from tools.verify_reporter_output import main

    assert main(["--date", ISSUE, "--category", "../../etc"]) == 2
    assert main(["--date", ISSUE, "--category", "ai/../fx"]) == 2
