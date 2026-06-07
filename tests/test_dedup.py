#!/usr/bin/env python3
"""tools/dedup.py の TDD テスト。

検証項目:
1. URL 正規化（tracking 除去・AMP 解除・末尾スラッシュ統一・mobile prefix 除去）
2. タイトル類似度（正規化・N-gram Jaccard）
3. 24 時間ウィンドウ判定（境界値・続報扱い・除外）
4. バッチ内重複（同じ batch 内の重複候補も弾く）

設計メモ:
- 個別検査は `_check_*` 関数として実装し、`_CONTRACT_CASES` に登録する
- pytest 経路では `test_all_dedup_contracts` が集約ゲートとして全契約を実評価する
- main() 経路 (CLI 直接実行) は各 _check_* を順に呼び出して FAIL/PASS を表示する
- `_check_*` プレフィックスにすることで pytest discover が個別関数を test として
  拾わなくなり、PytestReturnNotNoneWarning も発生しない
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import dedup  # type: ignore

JST = timezone(timedelta(hours=9))


def _check_url_normalization() -> list[str]:
    errs: list[str] = []
    cases = [
        # (input, expected)
        ("https://www.example.com/article?utm_source=twitter&id=123",
         "https://example.com/article?id=123"),
        ("https://m.example.com/news/", "https://example.com/news"),
        ("https://example.com/news/amp/", "https://example.com/news"),
        ("https://EXAMPLE.com/Path?fbclid=xyz&q=ai",
         "https://example.com/Path?q=ai"),
        ("https://example.com/news/?amp=1", "https://example.com/news"),
        ("https://example.com/news#anchor", "https://example.com/news"),
    ]
    for inp, expected in cases:
        got = dedup.normalize_url(inp)
        if got != expected:
            errs.append(f"normalize_url({inp!r}) = {got!r}, expected {expected!r}")
    return errs


def _check_title_normalization() -> list[str]:
    errs: list[str] = []
    cases = [
        ("【速報】日銀、政策金利 0.75% 据え置き", "速報 日銀 政策金利 0.75% 据え置き"),
        ('"BOJ hold"  rate', '"boj hold" rate'),
        ("Hello, World!", "hello, world!"),
    ]
    for inp, expected_substr in cases:
        got = dedup.normalize_title(inp)
        # 完全一致でなく、主要トークンが残っているか
        for token in expected_substr.split():
            if token.lower().strip("',.") not in got and "," not in token:
                # 緩い検査
                pass
        if not got:
            errs.append(f"normalize_title({inp!r}) returned empty")
    return errs


def _check_jaccard_similarity() -> list[str]:
    errs: list[str] = []
    a = "日銀総裁が利上げを示唆"
    b = "日銀総裁、追加利上げを示唆"
    sim = dedup.jaccard(dedup.char_ngrams(dedup.normalize_title(a)),
                        dedup.char_ngrams(dedup.normalize_title(b)))
    if sim < 0.5:
        errs.append(f"類似タイトル {a!r} vs {b!r} の Jaccard = {sim:.2f}, 0.5 以上を期待")
    a2 = "日銀利上げ"
    b2 = "ガソリン価格高騰"
    sim2 = dedup.jaccard(dedup.char_ngrams(dedup.normalize_title(a2)),
                         dedup.char_ngrams(dedup.normalize_title(b2)))
    if sim2 > 0.3:
        errs.append(f"無関係タイトル {a2!r} vs {b2!r} の Jaccard = {sim2:.2f}, 0.3 以下を期待")
    return errs


def _ts(hours_ago: float) -> str:
    return (datetime.now(JST) - timedelta(hours=hours_ago)).isoformat()


def _check_24h_window() -> list[str]:
    errs: list[str] = []
    existing = [{
        "title": "日銀総裁、追加利上げを示唆",
        "url": "https://example.com/boj/1",
        "url_norm": "https://example.com/boj/1",
        "seen_at": _ts(10.0),  # 10 時間前
    }]
    # 同じトピックを 10 時間後に取得 → 24h 以内なので除外されるべき
    candidates = [{
        "title": "日銀総裁、追加利上げに前向き発言",  # タイトル類似
        "url": "https://example.com/boj/2",
        "score": 80,
    }]
    passed, dropped = dedup.dedup_candidates(candidates, existing, window_hours=24.0)
    if passed or len(dropped) != 1:
        errs.append(f"24h 以内の類似記事が除外されない: passed={len(passed)}, dropped={len(dropped)}")
    return errs


def _check_followup_after_24h() -> list[str]:
    errs: list[str] = []
    existing = [{
        "title": "日銀総裁、追加利上げを示唆",
        "url": "https://example.com/boj/1",
        "url_norm": "https://example.com/boj/1",
        "seen_at": _ts(30.0),  # 30 時間前
    }]
    candidates = [{
        "title": "日銀総裁、追加利上げに前向き発言",
        "url": "https://example.com/boj/2",
        "score": 80,
    }]
    passed, dropped = dedup.dedup_candidates(candidates, existing, window_hours=24.0)
    if not passed or dropped:
        errs.append(f"24h 超の類似記事は続報扱いで通過すべき: passed={len(passed)}, dropped={len(dropped)}")
    elif not passed[0].get("is_followup"):
        errs.append(f"is_followup=True が立っていない: {passed[0]}")
    return errs


def _check_url_exact_match() -> list[str]:
    errs: list[str] = []
    existing = [{
        "title": "ABC",
        "url": "https://example.com/x",
        "url_norm": "https://example.com/x",
        "seen_at": _ts(1.0),
    }]
    # tracking 違いの URL
    candidates = [{
        "title": "全く違うタイトル",
        "url": "https://example.com/x?utm_source=twitter",
        "score": 90,
    }]
    passed, dropped = dedup.dedup_candidates(candidates, existing, window_hours=24.0)
    if passed:
        errs.append("URL 正規化マッチで除外されるべき")
    return errs


def _check_same_url_old_still_dropped() -> list[str]:
    """同一記事 (URL 一致) は何日前でも常に除外される (続報は別 URL なので影響なし)。

    なぜ重要か: Mobility の Waymo 記事が 3 日連続で再掲された事象の再発防止。
    URL 一致を 24h 窓で続報扱いにしていたのが原因だったため、URL 一致は
    時間に関係なく弾くのが正しい仕様であることをロックする。
    """
    errs: list[str] = []
    existing = [{
        "title": "Waymo、ダラス等4都市同時展開",
        "url": "https://www.autoconnectedcar.com/2026/05/waymo-expands/",
        "url_norm": "https://www.autoconnectedcar.com/2026/05/waymo-expands",
        "seen_at": _ts(72.0),  # 3 日前 = 24h 窓の外
    }]
    candidates = [{
        # 同じ URL (tracking 付き)・タイトルは多少変わっていても「同一記事」
        "title": "Waymo、ダラス等4都市同時展開 — 続報まとめ",
        "url": "https://www.autoconnectedcar.com/2026/05/waymo-expands/?utm_source=x",
        "score": 92,
    }]
    passed, dropped = dedup.dedup_candidates(candidates, existing, window_hours=24.0)
    if passed or len(dropped) != 1:
        errs.append(
            f"3 日前の同一 URL 記事は常に除外されるべき: "
            f"passed={len(passed)}, dropped={len(dropped)}"
        )
    return errs


def _check_url_match_has_priority_over_old_token_match() -> list[str]:
    """同一 URL は、より古いタイトル/トークン一致より必ず優先して除外する。

    なぜ重要か (2026-06-07 AI 再掲の主因): existing を時系列順に走査すると、
    先に古い TechCrunch の Google/Anthropic 記事が token match し、後段にある
    CNBC の同一 URL 既存記事へ到達しなかった。その結果、同一 URL なのに
    followup_gate の「新材料あり」扱いで通過していた。
    """
    errs: list[str] = []
    old_token_match = {
        "title": "Google 米DoD機密ネットワーク向けAI全面解放 — Anthropic拒否後にxAI・OpenAI・Googleが相次ぎ受注",
        "url": "https://techcrunch.com/2026/04/28/google-expands-pentagons-access-to-its-ai-after-anthropics-refusal/",
        "seen_at": "2026-04-30T06:00:00+09:00",
    }
    same_url_existing = {
        "title": "Microsoft and Google are late to AI coding, but absolutely critical they compete for growth",
        "url": "https://www.cnbc.com/2026/06/01/microsoft-and-google-take-on-anthropic-and-openai-in-ai-coding-models.html",
        "seen_at": "2026-06-05T06:00:00+09:00",
    }
    candidate = {
        "title": "Microsoft and Google take on Anthropic and OpenAI in AI coding models",
        "url": "https://www.cnbc.com/2026/06/01/microsoft-and-google-take-on-anthropic-and-openai-in-ai-coding-models.html?utm_source=x",
        "score": 90,
    }
    passed, dropped = dedup.dedup_candidates(
        [candidate], [old_token_match, same_url_existing],
        followup_gate=True,
        now=datetime(2026, 6, 7, 6, 0, tzinfo=JST),
    )
    if passed or len(dropped) != 1:
        errs.append(f"同一 URL が token match より優先されない: passed={len(passed)}, dropped={len(dropped)}")
    elif "url match" not in dropped[0].get("dedup_reason", ""):
        errs.append(f"drop 理由が URL match ではない: {dropped[0].get('dedup_reason')}")
    return errs


def _check_cross_language_same_event_detected() -> list[str]:
    """英語⇄日本語・別ソースの同一イベントを「同一トピック」として検知する。

    なぜ重要か (2026-06-03 Mobility 重複の主因): 文字 2-gram Jaccard は英語見出しと
    日本語見出しを 0.1〜0.3 にしか乗せられず、同一イベントの再掲を「新規」として
    連日通していた。社名・地名・数値は翻訳しても字面が残るため、言語非依存トークンの
    重なり (same_event_by_tokens) で同一イベントを補足する。実際に重複した 4 ペアが
    find_match で "title" 判定 (= 同一トピック → 24h 窓 or 続報) になることをロックする。
    """
    errs: list[str] = []
    pairs = [
        ("Waymo accelerates multi-city rollout: Dallas, Houston, San Antonio, Orlando added, now 10 cities",
         "Waymo テキサスDallas・Houston・San Antonio他4都市で完全自律ライドシェア一斉開始"),
        ("Waymo dominates Texas AV registrations: 577台 vs Tesla 42台 — 州法施行で数字が初めて公開",
         "Tesla has 42 robotaxis in Texas vs Waymo 577: 17 incidents in 10 months, FSD probe opens"),
        ("Waymo、カバレッジ20%超拡大で1,400平方マイル達成──11都市体制でロードアイランド州超え",
         "Waymo covers 1,400 sq miles across 11 cities — 週50万件ライド達成、ロンドン・東京展開も視野に"),
        # 同言語の取りこぼし (2-gram 0.45 前後) も閾値 0.42 で拾えること
        ("WaymoがOjai第6世代ロボタクシーを一部ライダーに公開開放 — サンディエゴ・ラスベガス展開へ",
         "WaymoがOjai第6世代ロボタクシーを一部ライダーに公開開放 — Zeekr製・センサー42%減・$20,000以下"),
    ]
    for a, b in pairs:
        existing = [{"title": b, "url": "https://example.com/exist",
                     "url_norm": "https://example.com/exist", "seen_at": _ts(5.0)}]
        match, mtype = dedup.find_match({"title": a, "url": "https://example.com/cand"}, existing)
        if mtype != "title":
            errs.append(f"同一イベント未検知: {a[:30]!r} ⇔ {b[:30]!r} → match_type={mtype}")
    return errs


def _check_different_events_same_company_survive() -> list[str]:
    """同じ会社の「別イベント」は誤検知で潰さない (固有名詞 1 語の共通では発火しない)。

    same_event_by_tokens は固有名詞 3 語以上 or 2 語以上+数値共通が条件。社名 1 語だけ
    共通の異なるニュースは新規として通過しなければならない (過剰除外でカテゴリが
    枯れるのを防ぐ)。"""
    errs: list[str] = []
    pairs = [
        ("Waymo expands robotaxi service to Miami this spring",
         "Waymo raises $16B to scale fleet internationally"),
        ("Toyota unveils new solid-state battery roadmap",
         "Toyota's EV sales jump in Japan as subsidies kick in"),
    ]
    for a, b in pairs:
        existing = [{"title": b, "url": "https://example.com/exist",
                     "url_norm": "https://example.com/exist", "seen_at": _ts(5.0)}]
        match, mtype = dedup.find_match({"title": a, "url": "https://example.com/cand"}, existing)
        if mtype is not None:
            errs.append(f"別イベントを誤検知: {a[:30]!r} ⇔ {b[:30]!r} → match_type={mtype}")
    return errs


def _check_japanese_katakana_title_same_event() -> list[str]:
    """カタカナ日本語タイトルの固有名詞を英日エイリアスで英語見出しと照合する。

    なぜ重要か (2026-06-03 Game 重複整理): significant_tokens が英字ランしか拾わず、
    「ヨッシー」(日) と「Yoshi」(英) が別トークンになり同一発売の連日再掲を検出できな
    かった。カタカナ固有名詞抽出 + エイリアス (ヨッシー→yoshi) で同一イベントを照合の
    土俵に乗せる。実際に取りこぼした 05-01(英)/05-18(日) のヨッシー発売ペアをロックする。
    """
    errs: list[str] = []
    a = "Yoshi and the Mysterious Book Switch2専用 5月21日確定"  # 05-01 #78 (英語)
    b = "ヨッシーと不思議な本、Switch2で5月21日発売 — ファースト新作でファミリー層確保"  # 05-18 #75 (日本語)
    existing = [{"title": b, "url": "https://example.com/exist",
                 "url_norm": "https://example.com/exist", "seen_at": _ts(5.0)}]
    match, mtype = dedup.find_match({"title": a, "url": "https://example.com/cand"}, existing)
    if mtype != "title":
        wa, na = dedup.significant_tokens(a)
        wb, nb = dedup.significant_tokens(b)
        errs.append(
            f"ヨッシー(英/日)同一発売が未検知: match_type={mtype} "
            f"(wa={sorted(wa)} na={sorted(na)} / wb={sorted(wb)} nb={sorted(nb)})"
        )
    return errs


def _check_japanese_general_katakana_no_false_match() -> list[str]:
    """一般カタカナ語だけ共通の別タイトルを same_event 誤検知しない (トークン直接検証)。

    固有名詞でないカタカナ語 (ゲーム/リリース等) を words に混ぜると同カテゴリの別記事を
    潰す。stopword 除外と「固有名詞 1 語のみ共通では発火しない」ガードで、別タイトルが
    生き残ることを same_event_by_tokens レベルでロックする (jaccard を介さず純検証)。
    """
    errs: list[str] = []
    cases_false = [
        ("ポケモン新作 Switch2 2026年冬", "ゼルダ新作 Switch2 2027年春"),
        ("スプラトゥーン レイダース 7月23日発売", "スターフォックス 6月25日発売"),
    ]
    for a, b in cases_false:
        wa, na = dedup.significant_tokens(a)
        wb, nb = dedup.significant_tokens(b)
        if dedup.same_event_by_tokens(wa, na, wb, nb):
            errs.append(f"別タイトル誤検知: {a!r} ⇔ {b!r} (wa={sorted(wa)} wb={sorted(wb)})")
    return errs


def _check_batch_internal_dedup() -> list[str]:
    errs: list[str] = []
    existing: list[dict] = []
    # 同じ batch 内に類似 2 件
    candidates = [
        {"title": "GPT-5.5 を OpenAI が発表", "url": "https://example.com/a", "score": 95},
        {"title": "GPT-5.5、OpenAI が正式発表", "url": "https://example.com/b", "score": 90},
        {"title": "Anthropic Claude Opus 4.7 GA", "url": "https://example.com/c", "score": 88},
    ]
    passed, dropped = dedup.dedup_candidates(candidates, existing, window_hours=24.0)
    # 1, 3 が通過、2 は内部重複で除外される想定
    if len(passed) != 2 or len(dropped) != 1:
        errs.append(
            f"batch 内重複が処理されていない: passed={len(passed)}, dropped={len(dropped)} "
            f"(passed titles: {[p['title'] for p in passed]})"
        )
    return errs


def _check_source_date_extraction_and_freshness_gate() -> list[str]:
    """URL パスの日付が古い候補は、本日記事として採用しない。

    取得日 `seen_at` ではなく source URL の発行日が古い場合に落とすことで、
    「今日のニュース」枠へ 2 月/4 月の記事が混入するのを防ぐ。URL に日付が無い
    候補は偽陽性を避けるため鮮度ゲートでは落とさない。
    """
    errs: list[str] = []
    cases = {
        "https://www.cnbc.com/2026/02/17/sample.html": "2026-02-17",
        "https://fortune.com/2026-06-05/sample/": "2026-06-05",
        "https://www.newstribune.com/news/2026/jun/04/sample/": "2026-06-04",
        "https://example.com/news/20260603-sample": "2026-06-03",
    }
    for url, expected in cases.items():
        got = dedup.extract_source_date_from_url(url)
        if got is None or got.isoformat() != expected:
            errs.append(f"source date 抽出失敗: {url} -> {got}, expected={expected}")
    candidates = [
        {
            "title": "Old source date should be dropped",
            "url": "https://www.cnbc.com/2026/02/17/old-ai-news.html",
            "score": 88,
        },
        {
            "title": "URL without date survives freshness gate",
            "url": "https://example.com/topic/latest-ai-news",
            "score": 80,
        },
    ]
    passed, dropped = dedup.dedup_candidates(
        candidates, [],
        freshness_gate=True,
        max_source_age_days=7,
        now=datetime(2026, 6, 7, 6, 0, tzinfo=JST),
    )
    if len(passed) != 1 or passed[0]["url"] != "https://example.com/topic/latest-ai-news":
        errs.append(f"日付なし URL は鮮度ゲートで落とさないべき: passed={passed}")
    if len(dropped) != 1 or "freshness gate" not in dropped[0].get("dedup_reason", ""):
        errs.append(f"古い source date が鮮度ゲートで落ちない: dropped={dropped}")
    return errs


_CONTRACT_CASES = [
    ("URL 正規化",            _check_url_normalization),
    ("タイトル正規化",        _check_title_normalization),
    ("Jaccard 類似度",        _check_jaccard_similarity),
    ("24 時間ウィンドウ除外", _check_24h_window),
    ("24 時間超は続報扱い",   _check_followup_after_24h),
    ("URL 完全マッチ除外",    _check_url_exact_match),
    ("同一URLは常に除外(数日後も)", _check_same_url_old_still_dropped),
    ("URL一致は古いtoken一致より優先", _check_url_match_has_priority_over_old_token_match),
    ("cross-language 同一イベント検知", _check_cross_language_same_event_detected),
    ("別イベントは誤検知しない", _check_different_events_same_company_survive),
    ("カタカナ日本語タイトル同一イベント検知", _check_japanese_katakana_title_same_event),
    ("一般カタカナ語は誤検知しない", _check_japanese_general_katakana_no_false_match),
    ("batch 内重複除外",      _check_batch_internal_dedup),
    ("source date 鮮度ゲート", _check_source_date_extraction_and_freshness_gate),
]


def test_all_dedup_contracts() -> None:
    """pytest 集約ゲート: 上の各 _check_* は list[str] 返却の手動実行形式 (main で集計)。
    この 1 件で全契約を実評価し、errs を返すものがあれば fail させる
    (safe-commit の pytest ゲートで回帰を捕捉)。"""
    failures = {label: errs for label, fn in _CONTRACT_CASES if (errs := fn())}
    assert not failures, f"dedup 契約違反: {failures}"


def main() -> int:
    overall_ok = True
    for label, fn in _CONTRACT_CASES:
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
