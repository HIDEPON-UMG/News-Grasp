"""News-Grasp pytest 共通設定 (Plan v3 P2)。

- `network` marker を定義 (= `-m "not network"` で外部 HTTP test を一括除外)。
- 移行期の後方互換: `NEWS_GRASP_SKIP_URL_CHECK=1` が立っていれば `-m "not network"`
  相当 (= network marker 付き test を skip) に自動切替する。本番 runner.ps1 と
  CI/開発者環境の両方で同じ marker が効くようにする。
- sys.path にリポジトリ root を入れて `tools.*` の import を統一する
  (既存 test は各ファイルで `sys.path.insert(0, str(ROOT))` していたが、
  conftest 集約で重複削減)。

> 由来: Plan v3 (`~/.claude/plans/quiet-foraging-floyd.md`) P2 で
> `safe-commit` ゲート 7 を `python -m pytest tests/ -q -m "not network"` 方式に
> 標準化、同時に各プロジェクトの conftest で marker を定義する横展開設計。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_configure(config: pytest.Config) -> None:
    """marker を pytest に登録 (= `pytest --strict-markers` 互換)。"""
    config.addinivalue_line(
        "markers",
        "network: 外部 HTTP を実打鍵する test (CI/オフラインでは "
        "`-m \"not network\"` で除外、ローカルで `-m network` で個別実行)",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """`NEWS_GRASP_SKIP_URL_CHECK=1` で network marker test を自動 skip。

    移行期 (Plan v3 後 1-2 ヶ月) の後方互換。安定したら環境変数を削除する別タスクで完了。
    本番 runner.ps1 step 2.8 は `-m "not network"` を直接渡すよう改訂され、
    本 wrapper は env 経由の呼び出し (旧コード / safe-commit 旧ゲート 7) を吸収する。
    """
    if os.environ.get("NEWS_GRASP_SKIP_URL_CHECK") != "1":
        return
    skip_network = pytest.mark.skip(
        reason="NEWS_GRASP_SKIP_URL_CHECK=1 で network test を skip (移行期互換)"
    )
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip_network)
