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
