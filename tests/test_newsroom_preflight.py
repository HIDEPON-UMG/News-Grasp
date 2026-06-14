#!/usr/bin/env python3
"""E2E 前 no-Codex preflight の契約テスト。"""
from __future__ import annotations

from pathlib import Path

from tools.newsroom_preflight import run


ROOT = Path(__file__).resolve().parent.parent


def test_newsroom_preflight_passes_current_contracts() -> None:
    assert run(ROOT) == []
