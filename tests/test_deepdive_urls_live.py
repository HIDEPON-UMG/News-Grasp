#!/usr/bin/env python3
"""DeepDive md の URL 生存検証の契約テスト。

# 検証する「なぜ重要か」

2026-06-03 に DeepDive 本文と chart source へ `https://www.bk.mufg.jp/report/hconwnew/
FX_Monthly_0529.pdf` が捏造 URL のまま記載され、404 で公開された。LLM が「それっぽい
URL」を記憶ベースで生成しており、`WebFetch` で実際にアクセスしていない URL が混入する
構造が露出した。

`render_deepdive.py` の `_require_blocks` は必須ブロックの**存在**しか見ておらず、URL の
**生存**は素通りしていた。本テストは `tools.validate_deepdive_urls` モジュール (URL 抽出
+ HEAD/GET 判定の境界 1 箇所集約) を呼び、全 DeepDive md に対し以下を locked-in する:

  1. 抽出器が参考リンク・timeline・relations/chart/table.source の URL を正しく拾うこと
  2. fatal な URL (404/410/捏造ホスト/恒久 5xx) が含まれていれば DeepDiveUrlError で
     hard fail すること
  3. 既存の DeepDive md 全件がこの検証を通過すること (= 公開済み記事に死リンクが無い)

実行:
  pytest tests/test_deepdive_urls_live.py -v
  pytest tests/test_deepdive_urls_live.py -v -k extract  # オフラインだけ動かす単体

ネットワークが無い環境 (CI 等) では `NEWS_GRASP_SKIP_URL_CHECK=1` で生存検証は skip され、
抽出器の単体テストだけが残る。本番 runner (news-grasp-runner.ps1) は環境変数を立てないので
常時 ON。
"""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.validate_deepdive_urls import (  # noqa: E402
    DeepDiveUrlError,
    UrlRef,
    extract_urls,
    require_live_urls,
    verify_urls,
)
import tools.validate_deepdive_urls as deepdive_urls  # noqa: E402


# ── オフラインで動く単体テスト (抽出器の網羅性) ────────────────────────────────

def test_extract_refs_url_from_bullet():
    md = """## 参考リンク
- 日経「円160円突破」(2026-06) https://www.nikkei.com/article/X.html
- 普通の段落 (URL 無し)
"""
    refs = extract_urls(md)
    assert refs == [UrlRef(url="https://www.nikkei.com/article/X.html", location="refs")]


def test_extract_timeline_url():
    md = """## 背景
```timeline
[
  {"date": "2026-06-01", "title": "ev", "source": "Foo", "url": "https://example.com/a", "thumb": ""}
]
```
"""
    refs = extract_urls(md)
    assert any(r.location == "timeline" and r.url == "https://example.com/a" for r in refs)


def test_extract_chart_source_url():
    md = """## 深掘り
```chart
{"type": "line", "title": "t", "categories": [], "series": [], "source": "Foo (2026) https://example.com/chart"}
```
"""
    refs = extract_urls(md)
    assert any(r.location == "chart.source" and r.url == "https://example.com/chart" for r in refs)


def test_extract_table_and_relations_source_urls():
    md = """## 深掘り
```table
{"title": "t", "columns": [], "rows": [], "source": "Foo https://example.com/table"}
```

```relations
{"title": "t", "nodes": [], "edges": [], "source": "Bar https://example.com/rel"}
```
"""
    refs = extract_urls(md)
    locs = {(r.location, r.url) for r in refs}
    assert ("table.source", "https://example.com/table") in locs
    assert ("relations.source", "https://example.com/rel") in locs


def test_extract_strips_trailing_punctuation():
    md = """## 参考リンク
- Foo (https://example.com/a).
- Bar: https://example.com/b。
"""
    refs = extract_urls(md)
    urls = {r.url for r in refs}
    # 末尾の `.` と `。` が剥がされている (URL の正規化)。
    assert "https://example.com/a" in urls or "https://example.com/a)" in urls
    assert "https://example.com/b" in urls


def test_extract_markdown_link_excludes_closing_parenthesis_and_date_note():
    """Markdownリンクの後続注記をURLへ混入させない。"""
    md = """## 参考リンク
- [公式資料](https://example.com/report.pdf)（2026-08-01）
"""
    assert extract_urls(md) == [
        UrlRef(url="https://example.com/report.pdf", location="refs")
    ]


def test_request_construction_failure_is_fatal(monkeypatch: pytest.MonkeyPatch):
    """URLをHTTP要求へ変換できない状態を通信不能として成功扱いしない。"""
    monkeypatch.setattr(
        deepdive_urls,
        "_probe",
        lambda *args, **kwargs: (None, "UnicodeEncodeError: invalid URL"),
    )
    verdict = deepdive_urls._verify_one(
        UrlRef(url="https://example.com/a)（2026）", location="refs"),
        timeout=0.1,
    )
    assert verdict.ok is False
    assert "検証不能" in verdict.detail


def test_verify_urls_probes_duplicate_url_once(monkeypatch: pytest.MonkeyPatch):
    """同一記事内の重複URLは1回だけ実打鍵し、全出現位置へ結果を戻す。"""
    calls: list[str] = []

    def fake_verify(ref: UrlRef, *, timeout: float):
        calls.append(ref.url)
        return deepdive_urls.UrlVerdict(ref, 200, True, "HEAD 200")

    monkeypatch.setattr(deepdive_urls, "_verify_one", fake_verify)
    refs = [
        UrlRef(url="https://example.com/a", location="refs"),
        UrlRef(url="https://example.com/a", location="chart.source"),
    ]
    verdicts = verify_urls(refs)
    assert calls == ["https://example.com/a"]
    assert [v.ref.location for v in verdicts] == ["refs", "chart.source"]


def test_verify_urls_rejects_unbounded_resource_parameters() -> None:
    """生成物やcaller入力でthread/timeout/unique URLを無制限化できない。"""
    with pytest.raises(ValueError, match="workers_out_of_policy"):
        verify_urls([], max_workers=10_000)
    with pytest.raises(ValueError, match="timeout_out_of_policy"):
        verify_urls([], timeout=600)
    refs = [UrlRef(url=f"https://example.com/{index}", location="refs") for index in range(257)]
    with pytest.raises(ValueError, match="unique_budget_exceeded"):
        verify_urls(refs)


def test_system_tls_fallback_recovers_python_certificate_chain_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    """Python CAで検証不能でもWindows TLS境界で200なら生存扱いする。"""
    monkeypatch.setattr(
        deepdive_urls,
        "_probe",
        lambda *args, **kwargs: (
            None,
            "URLError: [SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate",
        ),
    )
    calls: list[str] = []

    def fake_system_tls(url: str, *, timeout: float):
        calls.append(url)
        return 200, "curl[WindowsTLS] 200"

    monkeypatch.setattr(deepdive_urls, "_probe_system_tls", fake_system_tls, raising=False)
    verdict = deepdive_urls._verify_one(
        UrlRef(url="https://example.com/report.pdf", location="refs"),
        timeout=0.1,
    )
    assert calls == ["https://example.com/report.pdf"]
    assert verdict.ok is True
    assert verdict.status == 200


def test_network_unreachable_is_fatal_without_explicit_skip(
    monkeypatch: pytest.MonkeyPatch,
):
    """本番URL gateは通信不能を生存確認へ読み替えない。"""
    monkeypatch.delenv("NEWS_GRASP_SKIP_URL_CHECK", raising=False)
    monkeypatch.setattr(
        deepdive_urls,
        "_probe",
        lambda *args, **kwargs: (None, "URLError: timed out"),
    )
    verdict = deepdive_urls._verify_one(
        UrlRef(url="https://example.com/a", location="refs"),
        timeout=0.1,
    )
    assert verdict.ok is False
    assert "検証不能" in verdict.detail


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://169.254.169.254/latest/meta-data/", "http://[::1]/"])
def test_url_probe_rejects_internal_network_before_transport(monkeypatch, url):
    calls: list[object] = []
    monkeypatch.setattr(deepdive_urls.proc, "quiet_run", lambda *args, **kwargs: calls.append(args))
    status, detail = deepdive_urls._probe(url, method="GET", timeout=0.1, range_header=True)
    assert status is None
    assert "public_fetch" in detail
    system_status, system_detail = deepdive_urls._probe_system_tls(url, timeout=0.1)
    assert system_status is None
    assert "public_fetch" in system_detail
    assert calls == []


# ── ネットワーク必要テスト (本番検証) ────────────────────────────────────────

def _network_available() -> bool:
    if os.environ.get("NEWS_GRASP_SKIP_URL_CHECK") == "1":
        return False
    try:
        # 1 つの確実な公開ホストに 3 秒で connect だけ試す。完全オフラインなら fail。
        with socket.create_connection(("1.1.1.1", 443), timeout=3.0):
            return True
    except OSError:
        return False


needs_network = pytest.mark.skipif(
    not _network_available(),
    reason="ネットワーク不可 (または NEWS_GRASP_SKIP_URL_CHECK=1)",
)


@pytest.mark.network
@needs_network
def test_require_live_urls_raises_on_fabricated_url(tmp_path: Path):
    """捏造 URL (確実に 404 になるパス) を含む md が DeepDiveUrlError で弾かれる契約。"""
    md = """---
title: "t"
date: "2026-06-03"
---
## 参考リンク
- 捏造 (確実 404): https://www.bk.mufg.jp/report/hconwnew/FX_Monthly_0529.pdf
"""
    p = tmp_path / "fake.md"
    p.write_text(md, encoding="utf-8")
    with pytest.raises(DeepDiveUrlError) as ei:
        require_live_urls(p, md)
    # エラーメッセージに捏造 URL と location が含まれていること (デバッグ可能性)。
    assert "FX_Monthly_0529.pdf" in str(ei.value)
    assert "[refs]" in str(ei.value)


@pytest.mark.network
@needs_network
def test_require_live_urls_passes_on_normal_urls(tmp_path: Path):
    """安定して生存する公開 URL のみなら通る契約 (anti-bot 復旧含む)。"""
    md = """---
title: "t"
date: "2026-06-03"
---
## 参考リンク
- Cloudflare 1.1.1.1: https://one.one.one.one/
- Example: https://example.com/
"""
    p = tmp_path / "ok.md"
    p.write_text(md, encoding="utf-8")
    # raise されないことが契約。
    verdicts = require_live_urls(p, md)
    assert len(verdicts) == 2
    assert all(v.ok for v in verdicts)


@pytest.mark.parametrize("status", [401, 406])
def test_access_control_only_response_is_ambiguous_alive(monkeypatch, status):
    """認証・content negotiation拒否だけでは404/410の死リンクへ分類しない。"""

    from tools import validate_deepdive_urls as mod

    monkeypatch.setattr(mod, "_probe", lambda *args, **kwargs: (status, f"probe {status}"))
    verdict = mod._verify_one(mod.UrlRef("https://example.com/protected", "refs"), timeout=1)
    assert verdict.ok is False
    assert verdict.status == status
    assert verdict.verification_status == "ambiguous"


@pytest.mark.network
@needs_network
def test_all_published_deepdives_have_live_urls():
    """公開済み DeepDive md 全件 (digest/DeepDive/*.md) は生存 URL のみで構成されている。

    捏造事故 (2026-06-03) の再発検出。1 件でも fatal があれば assertion で内訳を出す。
    本テストが赤になったら md を修正し、runner が再生成するまで原状回復する。
    """
    src_dir = ROOT / "digest" / "DeepDive"
    if not src_dir.exists():
        pytest.skip("digest/DeepDive がまだ存在しない")
    occurrences: dict[str, list[tuple[Path, UrlRef]]] = {}
    for md_path in sorted(src_dir.glob("*.md")):
        text = md_path.read_text(encoding="utf-8")
        for ref in extract_urls(text):
            occurrences.setdefault(ref.url, []).append((md_path, ref))
    representatives = [rows[0][1] for rows in occurrences.values()]
    verdicts = []
    for offset in range(0, len(representatives), 256):
        verdicts.extend(verify_urls(representatives[offset:offset + 256], max_workers=16))
    failures: list[str] = []
    for v in verdicts:
        if v.verification_status != "fatal":
            continue
        for md_path, ref in occurrences[v.ref.url]:
            failures.append(f"{md_path.name}: [{ref.location}] {v.detail}  {v.ref.url}")
    assert not failures, (
        "公開 DeepDive に死リンクあり (捏造または恒久 404):\n  " + "\n  ".join(failures)
    )
