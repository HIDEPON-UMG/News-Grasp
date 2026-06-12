#!/usr/bin/env python3
"""articles.jsonl の URL 生存検証 契約テスト。

# 検証する「なぜ重要か」

2026-06-03 三菱UFJ FX_Monthly 捏造事故の事後監査で `articles.jsonl` の URL のうち
**33 件 / 803 件 (約 4%) が 404/410 の捏造**だったことが判明。日次 digest の Claude
セッションが「ありそうな URL」を記憶ベースで生成し、`runner.ps1` がそのまま push、
GitHub Pages で読者が踏んで死リンクに当たる構図が常態化していた。

本テストは `tools/audit_all_article_urls.py --recent 7` を呼び、直近 7 日に
追加された URL の 404/410 を locked-in で防ぐ:

  1. `runner.ps1` の push gate と同じロジックを CI/開発時にも適用 (=境界の二重化)
  2. 直近窓に限定することでテスト時間を ~30 秒以内に抑える
  3. 歴史的死リンク (リンク切れになった真正記事) は対象外 (別 ad-hoc 監査で扱う)

実行:
  pytest tests/test_all_article_urls_live.py -v

ネットワーク不可環境では `NEWS_GRASP_SKIP_URL_CHECK=1` で skip される (validator
モジュールが共通で見る環境変数)。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _network_available() -> bool:
    if os.environ.get("NEWS_GRASP_SKIP_URL_CHECK") == "1":
        return False
    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=3.0):
            return True
    except OSError:
        return False


needs_network = pytest.mark.skipif(
    not _network_available(),
    reason="ネットワーク不可 (または NEWS_GRASP_SKIP_URL_CHECK=1)",
)


def test_date_evidence_uses_published_date_over_issue_date(monkeypatch, tmp_path):
    """日付証拠検証は published_date を号日 (date) より優先して突合する契約。

    # なぜ重要か
    2026-06-12 復旧で実証された gate 矛盾の再発防止: record-schema gate は
    date == 号日を要求する一方、旧実装の日付証拠検証は date を「自己申告公開日」
    として htmldate と突合していた。前々日公開の記事 (published_date=号日-2) を
    号日に載せただけで偽日付 fatal となり、両 gate を同時に満たせる状態が存在
    しない = 復旧不能ループに陥る。本テストは「published_date がある record は
    published_date が claimed として evaluate_date_evidence に渡る」境界を
    locked-in する。
    """
    import json as _json
    from datetime import date as _date, timedelta as _timedelta
    from types import SimpleNamespace

    from tools import audit_all_article_urls as mod
    from tools import date_evidence as de

    today = _date.today()
    pub = today - _timedelta(days=2)
    record = {
        "date": today.strftime("%Y-%m-%d"),          # 号日
        "published_date": pub.strftime("%Y-%m-%d"),  # 実公開日 (号日-2)
        "title": "contract fixture",
        "url": "https://example.com/contract-fixture",
    }
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "articles.jsonl").write_text(
        _json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    captured: list = []

    def _fake_evaluate(claimed, url, html, *, record_title=None, **kw):
        captured.append(claimed)
        return SimpleNamespace(ok=True, warnings=[], claimed=claimed, url=url,
                               method="htmldate", fatal_reason=None)

    monkeypatch.setattr(mod, "_PKG_ROOT", tmp_path)
    monkeypatch.setattr(de, "fetch_html", lambda url, **kw: "<html></html>")
    monkeypatch.setattr(de, "evaluate_date_evidence", _fake_evaluate)
    monkeypatch.setattr(mod, "verify_urls", lambda refs, max_workers=0: [])
    monkeypatch.delenv("NEWS_GRASP_SKIP_URL_CHECK", raising=False)
    monkeypatch.setattr(sys, "argv", ["audit_all_article_urls", "--verify-dates", "--recent", "7"])

    rc = mod.main()
    assert rc == 0, "published_date 優先照合なら fatal 0 件で exit 0 のはず"
    assert captured == [pub], (
        f"claimed に published_date ({pub}) が渡るべきところ {captured} が渡った "
        "(号日 date を公開日として突合する旧バグの再発)"
    )

    # skip 契約: published_date が無い record は None を返し日付証拠検証の対象外。
    # 号日を公開日扱いする旧フォールバックは偽 fatal を生むため廃止 (2026-06-12)。
    assert mod.claimed_publication_date(None) is None
    assert mod.claimed_publication_date("  ") is None
    assert mod.claimed_publication_date("2026-06-10") == "2026-06-10"


def test_date_evidence_skips_record_without_published_date(monkeypatch, tmp_path):
    """published_date が無い record は日付証拠検証を skip する契約 (2026-06-12 gate 矛盾の構造対策)。

    # なぜ重要か
    号日 (date) は記事公開日の自己申告ではない。旧実装は published_date が無いとき
    号日を claimed として htmldate と突合し、前々日公開の記事を号日に載せただけで
    偽 fatal を出して record-schema gate (date==号日) と矛盾した = 復旧不能ループ。
    本テストは「published_date が無い record は evaluate_date_evidence に渡されず
    skip される」を locked-in する。
    """
    import json as _json
    from datetime import date as _date
    from types import SimpleNamespace

    from tools import audit_all_article_urls as mod
    from tools import date_evidence as de

    today = _date.today()
    record = {
        "date": today.strftime("%Y-%m-%d"),  # 号日 (published_date キーなし)
        "title": "no-pubdate fixture",
        "url": "https://example.com/no-pubdate",
    }
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "articles.jsonl").write_text(
        _json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    captured: list = []

    def _fake_evaluate(claimed, url, html, *, record_title=None, **kw):
        captured.append(claimed)
        return SimpleNamespace(ok=True, warnings=[], claimed=claimed, url=url,
                               method="htmldate", fatal_reason=None)

    monkeypatch.setattr(mod, "_PKG_ROOT", tmp_path)
    monkeypatch.setattr(de, "fetch_html", lambda url, **kw: "<html></html>")
    monkeypatch.setattr(de, "evaluate_date_evidence", _fake_evaluate)
    monkeypatch.setattr(mod, "verify_urls", lambda refs, max_workers=0: [])
    monkeypatch.delenv("NEWS_GRASP_SKIP_URL_CHECK", raising=False)
    monkeypatch.setattr(sys, "argv", ["audit_all_article_urls", "--verify-dates", "--recent", "7"])

    rc = mod.main()
    assert rc == 0, "published_date が無い record は skip され fatal 0 件のはず"
    assert captured == [], (
        f"published_date が無い record は日付証拠検証の対象外のはずだが claimed {captured} が渡った "
        "(号日を公開日扱いする旧バグの再発)"
    )


@pytest.mark.network
@needs_network
def test_recent_article_urls_are_alive():
    """直近 7 日の articles.jsonl URL がすべて生存している契約。

    audit_all_article_urls.py --gate を CLI 経由で呼び、exit 0 を確認する。runner.ps1
    の URL liveness gate と同じ境界モジュールを通すので、本テストが通れば push gate も
    通る (二重ガードのうち先発)。
    """
    py = sys.executable
    cmd = [py, "-m", "tools.audit_all_article_urls", "--gate"]
    result = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert result.returncode == 0, (
        "直近 7 日の articles.jsonl に死リンクあり (捏造または恒久 404)。\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )
