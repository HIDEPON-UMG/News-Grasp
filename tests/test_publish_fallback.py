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


def test_mark_ok_resets_status_after_fallback(tmp_path: Path) -> None:
    """成功公開時 mark_ok が publish-status.json を published_ok に戻す契約 (2026-06-12 疑義 C)。

    # なぜ重要か
    fallback publish は published_fallback_with_notice を残すが、通常号が成功しても
    これを戻す機構が無く stale なままだった。send_push はこの状態を読んで fallback 中の
    通知を抑止するため、mark_ok が status を戻さないと成功公開後も push が永久に抑止
    される。本テストは「fallback → mark_ok で result=published_ok・date 更新」を locked-in。
    """
    import json

    from tools.publish_fallback import STATUS_FILE, mark_ok

    docs = _write_docs(tmp_path)
    write_fallback(docs, date="2026-06-12", reason="url-liveness-gate-failed")
    status = json.loads((docs / STATUS_FILE).read_text(encoding="utf-8"))
    assert status["result"] == "published_fallback_with_notice"  # 前提: fallback 状態

    mark_ok(docs, date="2026-06-12")
    status = json.loads((docs / STATUS_FILE).read_text(encoding="utf-8"))
    assert status["result"] == "published_ok"
    assert status["date"] == "2026-06-12"
