#!/usr/bin/env python3
"""Availability fallback publish の契約テスト。"""
from __future__ import annotations

from pathlib import Path

from tools.publish_fallback import NOTICE_START, validate_availability, write_fallback


def _write_docs(root: Path) -> Path:
    docs = root / "docs"
    docs.mkdir()
    (docs / "index.html").write_text(
        """
<!doctype html>
<html lang="ja">
<body>
<nav class="home-nav"></nav>
<section class="home-hero"><p>直近成功号の本文です。</p></section>
</body>
</html>
""" + (" " * 600),
        encoding="utf-8",
    )
    return docs


def test_write_fallback_injects_notice_and_status(tmp_path: Path) -> None:
    docs = _write_docs(tmp_path)

    write_fallback(docs, date="2026-06-09", reason="content-gate-failed")

    html = (docs / "index.html").read_text(encoding="utf-8")
    assert NOTICE_START in html
    assert "本日の更新は品質確認中です" in html
    assert (docs / "publish-status.json").exists()
    assert validate_availability(docs, expect_fallback=True) == []


def test_validate_availability_rejects_missing_fallback_notice(tmp_path: Path) -> None:
    docs = _write_docs(tmp_path)

    errors = validate_availability(docs, expect_fallback=True)

    assert any("availability notice" in e for e in errors)
