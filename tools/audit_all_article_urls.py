#!/usr/bin/env python3
"""articles.jsonl の全 URL を validate_deepdive_urls の境界モジュールで一括検証する。

2026-06-03 三菱UFJ FX_Monthly 捏造事故の追加学習: 日次 digest の Claude セッションも
URL を捏造することが判明 (DeepDive の捏造 URL は実は日次 digest が articles.jsonl に
入れた捏造 URL を継承していた)。本スクリプトは articles.jsonl 全件を一括検証して
捏造 URL を炙り出す監査+ゲートツール。

# 役割

- ad-hoc 監査: 開発者が手で走らせて死リンク棚卸し
- 公開ゲート: news-grasp-runner.ps1 が Claude commit 後 / git push 前に呼び、捏造混入時
  は exit 1 で push を阻止する境界 (= 二度と公開しない構造)
- 契約テスト: tests/test_all_article_urls_live.py から呼ばれ、CI/開発時にも死リンク防止

# CLI

```
./.venv/Scripts/python.exe tools/audit_all_article_urls.py             # 全期間
./.venv/Scripts/python.exe tools/audit_all_article_urls.py --recent 7  # 直近7日のみ
./.venv/Scripts/python.exe tools/audit_all_article_urls.py --gate      # push gate モード
                                                                      # (号日+前日 + 厳格 exit)
./.venv/Scripts/python.exe tools/audit_all_article_urls.py --gate --match-session
                                                                      # 案②-Lite: gate に
                                                                      # 加え当日 LLM
                                                                      # session で WebSearch
                                                                      # 確認した URL リスト
                                                                      # (data/_session_urls.json)
                                                                      # と articles.jsonl
                                                                      # 当日 URL を
                                                                      # 物理照合
```

2026-06-11 偽日付事故対応: `--gate` は日付証拠検証 (--verify-dates) もデフォルトで
実行する。当日 date のレコードを full GET し htmldate 抽出日と突合 (乖離 > 1 日 =
fatal)、抽出不能時は Wayback CDX で否定証拠を照合する。無効化は --no-verify-dates。

exit 0 = 全 URL 健全 / exit 1 = 1 件以上 fatal (= 捏造または恒久 404 または
session 未確認または偽日付疑い)。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from tools.validate_deepdive_urls import UrlRef, verify_urls  # noqa: E402
from tools.validate_digest_articles_reconcile import current_reporter_urls_for_issue  # noqa: E402


@dataclass(frozen=True)
class DropResult:
    """per-article quarantine の反映件数。"""

    jsonl_dropped: int
    digest_cards_dropped: int
    touched_digest_files: int
    search_audit_updated: int = 0


def blocking_url_dates(issue_date: date) -> set[str]:
    """daily publish を止める URL liveness 対象日。

    外部ニュースサイトの古い URL は時間とともに消えるため、日次公開 gate は
    TODAY / YESTERDAY として表示される号日と前日だけを blocking にする。
    2 日以上前は ad-hoc 監査、warning、repair candidate の領域で扱う。
    """
    return {
        issue_date.strftime("%Y-%m-%d"),
        (issue_date - timedelta(days=1)).strftime("%Y-%m-%d"),
    }


def _normalize_url_for_match(url: str) -> str:
    """session 照合用の最小限の URL 正規化。

    完全一致だけで照合すると trailing slash 1 つ違いで誤検知が頻発するため、
    `_session_urls.json` 側でも articles.jsonl 側でも同じ正規化を適用する。

    やること:
      - 前後空白を剥がす
      - 末尾 `/` を 1 段だけ削除 (`https://a/` → `https://a`)
      - fragment (`#...`) を削除

    やらないこと (誤検知防止のため意図的に弱い正規化に留める):
      - クエリパラメータの並び替え・除去 → utm 除去まで踏み込むと dedup.py と
        実装が分散するし、session ファイルは LLM が WebSearch 結果をそのまま
        書き出す形なので utm が乗っていれば articles.jsonl 側にも乗っているため
        並列性が保たれる
      - host の小文字化 → 同上、WebSearch 結果と articles.jsonl は同じ source
        なので大文字小文字がズレる経路がない
    """
    s = url.strip()
    # fragment 除去
    if "#" in s:
        s = s.split("#", 1)[0]
    # 末尾スラッシュ削除 (https:// 直後の "//" は壊さない)
    if s.endswith("/") and not s.endswith("://"):
        s = s[:-1]
    return s


def _extract_norm_urls(data: object) -> set[str]:
    """session payload dict から正規化済み URL set を取り出す純粋ヘルパ。

    `{"date": ..., "urls": [...]}` の `urls` から http(s) URL のみを拾い、
    `_normalize_url_for_match` で照合用に正規化する。形式不正なら空 set。
    """
    if not isinstance(data, dict):
        return set()
    urls_raw = data.get("urls")
    if not isinstance(urls_raw, list):
        return set()
    return {
        _normalize_url_for_match(u)
        for u in urls_raw
        if isinstance(u, str) and u.startswith("http")
    }


def _load_session_urls(
    repo_root: Path, today_str: str | None = None
) -> tuple[set[str], Path, str | None]:
    """当日 LLM が WebSearch 200 確認した URL の白リストを union 読みする。

    2026-06-12 フラグメント化対応: 共有ファイル 1 本ではなく、当日フラグメント群
    `data/_session_urls.d/{today}/*.json` と legacy `data/_session_urls.json`
    (date が当日のときのみ) を **union** して 1 つの白リストにまとめる。

    返り値:
        (正規化 URL set, 代表パス, 当日 date 文字列 or None)
        - URL が 1 件でも集まれば date は ``today_str`` を返す (= 当日白リスト)。
        - フラグメント・legacy 両方不在/空/破損なら (空 set, パス, None) を返し、
          呼び出し側で degrade (= 警告のみで通過) させる。「session を書き忘れて
          gate が全件 fatal で push 失敗 → 朝の配信が止まる事故」を避ける fallback。

    破損フラグメントは 1 件単位で stderr に WARN を出して skip する (全体は止めない)。
    """
    if today_str is None:
        today_str = date.today().strftime("%Y-%m-%d")
    legacy = repo_root / "data" / "_session_urls.json"
    frag_dir = repo_root / "data" / "_session_urls.d" / today_str

    norm: set[str] = set()

    # 1) 当日フラグメント群を union (発火 1 回 = 1 ファイルなので並列 race なし)
    if frag_dir.is_dir():
        for frag in sorted(frag_dir.glob("*.json")):
            try:
                with frag.open(encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
                print(
                    f"WARN: 破損 session フラグメントを skip: {frag.name} ({e})",
                    file=sys.stderr,
                )
                continue
            norm |= _extract_norm_urls(data)

    # 2) legacy 共有ファイル (date が当日のときのみ union。後方互換)
    if legacy.exists():
        try:
            with legacy.open(encoding="utf-8") as f:
                ldata = json.load(f)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            ldata = None
        if isinstance(ldata, dict) and ldata.get("date") == today_str:
            norm |= _extract_norm_urls(ldata)

    # 代表パスは legacy パス (degrade 警告メッセージの relative_to 基準に使われる)
    if not norm:
        return set(), legacy, None
    return norm, legacy, today_str


def claimed_publication_date(published_date: object) -> str | None:
    """日付証拠検証に使う「自己申告公開日」を返す純関数。無ければ None。

    2026-06-12 意味論確定: record の date は号日 (digest 掲載日)、published_date が
    記事の実公開日。独立証拠 (htmldate/Wayback) と突合すべきは published_date のみ。
    published_date が無い record は「公開日の自己申告が無い」ため日付証拠検証の対象外
    とし None を返す (呼び出し側で skip)。

    旧実装は published_date が無いとき号日 (issue_date) にフォールバックしていたが、
    号日を公開日として htmldate と突合すると「前々日公開の記事を号日に載せた」だけで
    偽日付 fatal になり、record-schema gate (date == 号日) と同時に満たせない矛盾を
    生んだ (2026-06-12 復旧で実証)。号日は公開日の主張ではないので突合対象にしない
    ([[feedback_check_design_principles]] の category error 根絶)。
    """
    if isinstance(published_date, str) and published_date.strip():
        return published_date.strip()
    return None


def should_skip_date_evidence(url: str, date_evidence_source: object) -> bool:
    """htmldate 再検証が不適切な URL/source の日付証拠検証を skip する。

    Google News RSS の encoded URL は canonical 記事本文ではなく中継ページであり、
    harvest_candidates.py の `when:1d` + RSS pubDate が鮮度境界になる。中継ページへ
    htmldate をかけると Google 側ページの日付を拾い、正当な RSS pubDate と衝突する。
    """
    if "://news.google.com/rss/articles/" not in url:
        return False
    return isinstance(date_evidence_source, str) and date_evidence_source == "rss-pubdate"


def _drop_cards_from_markdown(text: str, urls: set[str]) -> tuple[str, int]:
    """指定 URL を含む記事カードブロックだけを Markdown から削除する。"""
    if not urls:
        return text, 0
    parts = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    dropped = 0
    while i < len(parts):
        if parts[i].lstrip().startswith("### ["):
            start = i
            i += 1
            while i < len(parts) and not parts[i].lstrip().startswith("### ["):
                i += 1
            block = "".join(parts[start:i])
            if any(url in block for url in urls):
                dropped += 1
                continue
            out.append(block)
            continue
        out.append(parts[i])
        i += 1
    return "".join(out), dropped


def _frontmatter_value(text: str, key: str) -> str | None:
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        if k.strip() == key:
            return v.strip().strip('"').strip("'")
    return None


def _count_digest_cards(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.startswith("### ["))


def drop_article_urls(
    *,
    repo_root: Path,
    urls: set[str],
    issue_date: str,
    apply: bool = False,
) -> DropResult:
    """articles.jsonl と当日 digest から指定 URL の記事だけを削除する。"""
    if not urls:
        return DropResult(0, 0, 0)

    jsonl = repo_root / "data" / "articles.jsonl"
    jsonl_dropped = 0
    if jsonl.exists():
        kept: list[str] = []
        with jsonl.open(encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    row = json.loads(stripped)
                except json.JSONDecodeError:
                    kept.append(line)
                    continue
                if str(row.get("url") or "") in urls:
                    jsonl_dropped += 1
                    continue
                kept.append(json.dumps(row, ensure_ascii=False) + "\n")
        if apply and jsonl_dropped:
            jsonl.write_text("".join(kept), encoding="utf-8")

    digest_root = repo_root / "digest"
    digest_cards_dropped = 0
    touched = 0
    search_audit_updated = 0
    for md in sorted(digest_root.glob(f"*/*{issue_date}*.md")):
        if md.parent.name in {"Summary", "DeepDive"}:
            continue
        original = md.read_text(encoding="utf-8-sig", errors="replace")
        updated, dropped = _drop_cards_from_markdown(original, urls)
        if dropped:
            digest_cards_dropped += dropped
            touched += 1
            if apply:
                md.write_text(updated, encoding="utf-8")
                cat_id = (
                    _frontmatter_value(updated, "categoryId")
                    or _frontmatter_value(updated, "category")
                    or md.parent.name
                ).strip().casefold()
                audit_path = repo_root / "data" / "search_audit" / issue_date / f"{cat_id}.json"
                if audit_path.exists():
                    try:
                        audit = json.loads(audit_path.read_text(encoding="utf-8-sig"))
                    except json.JSONDecodeError:
                        audit = None
                    if isinstance(audit, dict):
                        audit["selected_total"] = _count_digest_cards(updated)
                        audit_path.write_text(
                            json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8",
                        )
                        search_audit_updated += 1

    return DropResult(jsonl_dropped, digest_cards_dropped, touched, search_audit_updated)


def main() -> int:
    # 日本語版 Windows の cp932 では em-dash (—) や ✓ などの記号で print が
    # UnicodeEncodeError を起こし、NG URL 一覧の表示前にプロセスがクラッシュする。
    # 標準出力/エラーを UTF-8/replace に再構成して落ちないようにする (境界 1 箇所集約)。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--recent", type=int, default=0,
                    help="直近 N 日に絞る (0 = 全件)")
    ap.add_argument("--max-workers", type=int, default=16)
    ap.add_argument("--gate", action="store_true",
                    help="push gate モード (号日+前日 + 致命的フェイルで非ゼロ exit)")
    ap.add_argument("--match-session", action="store_true",
                    help="案②-Lite: data/_session_urls.json (= 当日 LLM が WebSearch で "
                         "200 確認した URL の白リスト) に articles.jsonl の直近 N 日 URL "
                         "が全て含まれているか物理照合する。含まれない URL は記憶捏造疑い "
                         "として fatal 扱い。session ファイル不在時は degrade (警告のみ・"
                         "従来 gate のみで継続) で朝のバッチを止めない")
    ap.add_argument("--require-session", action="store_true",
                    help="--match-session の白リストが不在/空/日付不一致なら fatal にする "
                         "(本番 runner 用。ad-hoc 監査の既定 degrade は維持)")
    ap.add_argument("--issue-date",
                    help="対象号日 (YYYY-MM-DD)。resume / 翌日検証でも quarantine 対象を実行日ではなく号日に固定する")
    ap.add_argument("--verify-dates", action="store_true",
                    help="2026-06-11 偽日付事故の恒久対策: 当日 date のレコード全件を "
                         "full GET し、htmldate で独立抽出した公開日と自己申告 date を "
                         "突合する (乖離 > 1 日 = fatal)。htmldate 抽出不能時は Wayback "
                         "CDX をフォールバック照合。--gate 指定時はデフォルト ON")
    ap.add_argument("--no-verify-dates", action="store_true",
                    help="--gate 時の日付証拠検証を無効化する脱出ハッチ (障害時の手動運用用)")
    ap.add_argument("--quarantine-articles", action="store_true",
                    help="NG URL の記事だけを articles.jsonl / digest から隔離する")
    ap.add_argument("--apply", action="store_true",
                    help="--quarantine-articles の変更を実反映する (既定は dry-run)")
    args = ap.parse_args()

    jsonl = _PKG_ROOT / "data" / "articles.jsonl"
    if not jsonl.exists():
        print(f"no jsonl: {jsonl}", file=sys.stderr)
        return 2

    today = date.today()
    issue_day = today
    issue_date_str = today.strftime("%Y-%m-%d")
    if args.issue_date:
        try:
            issue_day = datetime.strptime(args.issue_date, "%Y-%m-%d").date()
        except ValueError:
            print(f"FATAL: --issue-date は YYYY-MM-DD 形式: {args.issue_date!r}", file=sys.stderr)
            return 2
        issue_date_str = issue_day.strftime("%Y-%m-%d")
    gate_dates = blocking_url_dates(issue_day) if args.gate and not args.recent else None
    cutoff = today - timedelta(days=args.recent) if args.recent else None

    items: list[tuple[str, str, str]] = []  # (date, title, url)
    # date は号日 (= digest 掲載日)、published_date は記事の実公開日 (2026-06-12 意味論確定)。
    # 日付証拠検証の「自己申告公開日」は published_date のみを使う。published_date が無い
    # record は公開日の自己申告が無いため日付証拠検証の対象外 (skip)。号日を公開日として
    # htmldate と突合すると、前々日公開の記事を号日に載せただけで偽日付扱いになり
    # record-schema gate (date==号日) と矛盾するため (2026-06-12 gate 矛盾の構造対策)。
    pub_by_url: dict[str, str] = {}
    date_source_by_url: dict[str, object] = {}
    with jsonl.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            dt_str = str(d.get("date", "")).strip()
            url = str(d.get("url", "")).strip()
            title = str(d.get("title", "")).strip()
            if not url.startswith("http"):
                continue
            if gate_dates is not None:
                if dt_str not in gate_dates:
                    continue
            elif args.issue_date and dt_str != issue_date_str:
                continue
            if cutoff:
                try:
                    dt = datetime.strptime(dt_str, "%Y-%m-%d").date()
                except ValueError:
                    continue
                if dt < cutoff:
                    continue
            pub = d.get("published_date")
            if isinstance(pub, str) and pub.strip():
                pub_by_url[url] = pub.strip()
            date_source_by_url[url] = d.get("date_evidence_source")
            items.append((dt_str, title, url))

    if not items:
        print("対象 URL が 0 件")
        return 0

    if args.issue_date:
        current_urls = current_reporter_urls_for_issue(_PKG_ROOT, issue_date_str)
        if current_urls is not None:
            current_norm = {_normalize_url_for_match(url) for url in current_urls}
            items = [
                (dt_str, title, url)
                for dt_str, title, url in items
                if dt_str != issue_date_str or _normalize_url_for_match(url) in current_norm
            ]
            if not items:
                print(f"対象 URL が 0 件 (--issue-date {issue_date_str}, current reporter manifest)")
                return 0

    if gate_dates is not None:
        scope_label = f"号日/前日 {min(gate_dates)}..{max(gate_dates)}"
    elif cutoff:
        scope_label = f"直近 {args.recent} 日"
    else:
        scope_label = "全期間"
    print(f"対象 URL: {len(items)} 件 ({scope_label})")

    # 案②-Lite: session 白リスト照合 (gate と独立に動かせるが、本番運用は --gate と同時指定)
    session_fatal: list[tuple[str, str, str]] = []  # (date, title, url)
    session_gate_errors: list[str] = []
    if args.match_session:
        session_norm, session_path, session_date = _load_session_urls(_PKG_ROOT, issue_date_str)
        if not session_norm:
            # degrade: session ファイル無し / 空 / 破損 → 物理照合は無効化して従来 gate のみで進む
            print(
                f"STRONG WARN: --match-session 指定だが {session_path.relative_to(_PKG_ROOT)} "
                f"が不在 or 空 or 破損のため session 照合を skip (従来 gate のみで継続)。"
                f"PostToolUse hook が発火していない可能性が高い。"
                f"data/_session_urls.audit.log と runner の WorkingDirectory / PromptFile loaded を確認すること",
                file=sys.stderr,
            )
            if args.require_session:
                session_gate_errors.append("session whitelist missing or empty")
        else:
            if session_date and session_date != issue_date_str:
                # session date が当日でない (前日のまま残ってる等) → degrade と同じ扱い
                print(
                    f"STRONG WARN: _session_urls.json の date={session_date} が対象号日 {issue_date_str} と "
                    f"不一致のため session 照合を skip (古い session を誤検知に使わないため)。"
                    f"hook が当日セッションを書けていない可能性があるため data/_session_urls.audit.log を確認すること",
                    file=sys.stderr,
                )
                if args.require_session:
                    session_gate_errors.append(
                        f"session date mismatch: {session_date} != {issue_date_str}"
                    )
            else:
                # session の date と同じ date の articles.jsonl エントリのみを照合対象にする。
                # 7 日窓全体ではなく当日分だけ照合する理由:
                #   - 過去の article は別の session で書かれた = 当日の session に居なくて当然
                #   - 全 7 日窓を照合するとロールアウト初日 (session 導入前の article が
                #     7 日窓に残っている時期) で全件 fatal になり朝のバッチが止まる
                #   - LLM の URL 捏造は「今日 LLM が書いた article」でしか起きないので、
                #     当日分だけ照合すれば必要十分 (歴史的 URL は別 ad-hoc audit でカバー)
                target_items = [it for it in items if it[0] == session_date]
                print(
                    f"session 照合: white-list {len(session_norm)} 件と "
                    f"当日 ({session_date}) の articles.jsonl {len(target_items)} 件を物理照合"
                )
                for dt_str, title, url in target_items:
                    norm = _normalize_url_for_match(url)
                    if norm not in session_norm:
                        session_fatal.append((dt_str, title, url))
                if session_fatal:
                    print(f"\n=== session 未確認 URL {len(session_fatal)} 件 (= LLM 捏造疑い) ===")
                    for dt_str, title, url in session_fatal:
                        print(f"  [{dt_str}|{title[:40]}] session に未登録")
                        print(f"    {url}")

    # 2026-06-11 偽日付事故の恒久対策: 当日 date のレコード全件について、自己申告
    # date を独立証拠 (htmldate → Wayback CDX) と突合する最終防衛線。
    # 既存の URL パス日付チェック (dedup._freshness_drop_reason /
    # validate_daily_quality._stale_*) は URL に日付が無い記事を素通りさせる
    # fail-open だったため、本検証が「日付なし URL × 偽メタ日付」の死角を塞ぐ。
    date_fatal: list = []
    verify_dates = args.verify_dates or (args.gate and not args.no_verify_dates)
    if verify_dates and os.environ.get("NEWS_GRASP_SKIP_URL_CHECK") == "1":
        print("NEWS_GRASP_SKIP_URL_CHECK=1: 日付証拠検証も skip")
    elif verify_dates:
        from concurrent.futures import ThreadPoolExecutor as _TPE

        from tools.date_evidence import evaluate_date_evidence, fetch_html

        # 当日 + 前日のレコードを対象にする。当日のみだと「古い記事に date=前日を
        # 付ける」変種が検証対象外になり、既存 _stale_* (issue-1 まで許容) も
        # 素通りさせるため。digest 掲載が許される date 範囲 = 検証対象範囲で揃える。
        target_dates = blocking_url_dates(issue_day)
        targets = [it for it in items if it[0] in target_dates]
        if not targets:
            print(f"日付証拠検証: 当日/前日 ({min(target_dates)}..{max(target_dates)}) "
                  f"のレコード 0 件 → skip")
        else:
            print(f"\n日付証拠検証: 当日/前日の {len(targets)} 件を htmldate/Wayback と突合")

            def _check_date(item):
                dt_str, title, url = item
                claimed_str = claimed_publication_date(pub_by_url.get(url))
                if claimed_str is None:
                    # published_date が無い = 公開日の自己申告なし → 日付証拠検証の対象外。
                    # 号日 (dt_str) を公開日扱いして htmldate と突合すると偽 fatal になり
                    # record-schema gate と矛盾するため skip (2026-06-12 gate 矛盾の構造対策)。
                    return None
                if should_skip_date_evidence(url, date_source_by_url.get(url)):
                    return None
                try:
                    claimed = datetime.strptime(claimed_str, "%Y-%m-%d").date()
                except ValueError:
                    return None
                html = fetch_html(url)
                return evaluate_date_evidence(claimed, url, html, record_title=title)

            with _TPE(max_workers=8) as ex:
                evidences = [e for e in ex.map(_check_date, targets) if e is not None]
            for ev in evidences:
                for w in ev.warnings:
                    print(f"  WARN [{ev.claimed}] {w}", file=sys.stderr)
                    print(f"       {ev.url}", file=sys.stderr)
            date_fatal = [ev for ev in evidences if not ev.ok]
            print(f"日付証拠: {len(evidences) - len(date_fatal)}/{len(evidences)} OK, "
                  f"{len(date_fatal)} NG")
            if date_fatal:
                print(f"\n=== 偽日付疑い {len(date_fatal)} 件 (独立証拠と乖離) ===")
                for ev in date_fatal:
                    print(f"  [claimed {ev.claimed}|{ev.method}] {ev.fatal_reason}")
                    print(f"    {ev.url}")

    if os.environ.get("NEWS_GRASP_SKIP_URL_CHECK") == "1":
        # validate_deepdive_urls.require_live_urls と同じ環境変数で HEAD/GET を全スキップ。
        # オフライン CI・契約テスト (session 照合だけ見たい場合) の動作合わせのため。
        print("NEWS_GRASP_SKIP_URL_CHECK=1: HEAD/GET 検証を skip")
        verdicts = []
    else:
        refs = [UrlRef(url=url, location=f"{dt}|{title[:40]}") for dt, title, url in items]
        verdicts = verify_urls(refs, max_workers=args.max_workers)

    fatal = [v for v in verdicts if not v.ok]

    # 2026-06-12 収集改善: HEAD/GET (urllib) で fatal 判定された URL を fetch 昇格ラダー
    # (_fetch: Scrapling Fetcher → StealthyFetcher) で再検証し、anti-bot 由来の false-fatal を
    # 救済する。urllib では 403/blocked だが curl_cffi 偽装やヘッドレスブラウザでは 200 が
    # 返る発行元 (bloomberg/nikkei 等) の生存 URL を「捏造/死リンク」と誤って push 阻止する
    # 事故を防ぐ。404/410 由来の fatal は _verify_one で既に確定済みなので救済しない
    # (detail に 404/410 を含む verdict は対象外 = 真の死リンクは昇格でも復活させない)。
    if fatal and os.environ.get("NEWS_GRASP_SKIP_URL_CHECK") != "1":
        from tools._fetch import fetch_with_escalation

        rescued: list = []
        for v in fatal:
            d = (v.detail or "")
            if "404" in d or "410" in d:
                continue  # 真の死リンクは昇格ラダーでも救済しない
            res = fetch_with_escalation(v.ref.url, allow_stealthy=True)
            if res.ok:
                rescued.append(v)
                print(
                    f"  RESCUED (escalation {res.stage} {res.status}): "
                    f"[{v.ref.location}] {v.ref.url}",
                    file=sys.stderr,
                )
        if rescued:
            rescued_set = {id(v) for v in rescued}
            fatal = [v for v in fatal if id(v) not in rescued_set]
            print(
                f"escalation 救済: {len(rescued)} 件を anti-bot false-fatal として除外",
                file=sys.stderr,
            )

    if verdicts:
        print(f"\n結果: {len(verdicts) - len(fatal)}/{len(verdicts)} OK (HEAD/GET+escalation), {len(fatal)} NG")

    if fatal:
        print("\n=== NG URL 一覧 (要差し替え) ===")
        for v in fatal:
            print(f"  [{v.ref.location}] {v.detail}")
            print(f"    {v.ref.url}")

    # session 未確認 / session gate degraded / HEAD/GET fatal / 偽日付疑い の和集合が exit 判定に使われる
    total_fatal = len(fatal) + len(session_fatal) + len(session_gate_errors) + len(date_fatal)
    if total_fatal:
        if args.quarantine_articles:
            bad_urls = {v.ref.url for v in fatal}
            bad_urls |= {ev.url for ev in date_fatal}
            if args.apply and bad_urls:
                ledger = _PKG_ROOT / "build" / "quarantine" / issue_date_str / "bad-urls.json"
                ledger.parent.mkdir(parents=True, exist_ok=True)
                ledger.write_text(
                    json.dumps(sorted(bad_urls), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            result = drop_article_urls(
                repo_root=_PKG_ROOT,
                urls=bad_urls,
                issue_date=issue_date_str,
                apply=args.apply,
            )
            print(
                "quarantine: "
                f"jsonl_dropped={result.jsonl_dropped}, "
                f"digest_cards_dropped={result.digest_cards_dropped}, "
                f"touched_digest_files={result.touched_digest_files}, "
                f"search_audit_updated={result.search_audit_updated}, "
                f"apply={args.apply}"
            )
            if args.apply and (result.jsonl_dropped or result.digest_cards_dropped):
                return 0
        if session_fatal or session_gate_errors or date_fatal:
            print(
                f"\nFATAL: session gate {len(session_gate_errors)} 件 + "
                f"session 未確認 {len(session_fatal)} 件 + HEAD/GET NG {len(fatal)} 件 + "
                f"偽日付疑い {len(date_fatal)} 件 = {total_fatal} 件。push を中止します。",
                file=sys.stderr,
            )
            for err in session_gate_errors:
                print(f"  session gate: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
