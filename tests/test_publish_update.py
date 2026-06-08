#!/usr/bin/env python3
"""tools.publish_update の契約テスト。"""
from __future__ import annotations

import sys

import tools.publish_update as pub


def test_publish_does_not_notify_by_default(monkeypatch):
    """微細修正の手動公開では、既定で Web Push 通知を送らない。"""
    calls: list[list[str]] = []

    def fake_run(args, *, dry_run=False):
        calls.append(args)
        return 0

    monkeypatch.setattr(pub, "_run", fake_run)

    assert pub.publish() == 0
    assert calls == [["git", "push", "origin", "main"]]


def test_publish_runs_send_push_when_notify_is_explicit(monkeypatch):
    """通知が必要な更新では --notify 相当を明示した時だけ Web Push へ進む。"""
    calls: list[list[str]] = []

    def fake_run(args, *, dry_run=False):
        calls.append(args)
        return 0

    monkeypatch.setattr(pub, "_run", fake_run)

    assert pub.publish(notify=True) == 0
    assert calls[0] == ["git", "push", "origin", "main"]
    assert calls[1] == [sys.executable, str(pub.ROOT / "tools" / "send_push.py")]


def test_publish_does_not_notify_when_git_push_fails(monkeypatch):
    """push 失敗時は更新されていないため通知しない。"""
    calls: list[list[str]] = []

    def fake_run(args, *, dry_run=False):
        calls.append(args)
        return 1

    monkeypatch.setattr(pub, "_run", fake_run)

    assert pub.publish() == 1
    assert calls == [["git", "push", "origin", "main"]]


def test_publish_can_dry_run_notification_after_real_push(monkeypatch):
    """復旧確認では push 後の通知だけ dry-run にできる。"""
    calls: list[list[str]] = []

    def fake_run(args, *, dry_run=False):
        calls.append(args)
        return 0

    monkeypatch.setattr(pub, "_run", fake_run)

    assert pub.publish(notify_dry_run=True) == 0
    assert calls[1] == [
        sys.executable,
        str(pub.ROOT / "tools" / "send_push.py"),
        "--dry-run",
    ]
