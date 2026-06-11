#!/usr/bin/env python3
"""tools/_fetch.py の契約テスト（fetch 昇格ラダーの不変条件を locked-in）。

意図: 「urllib で取れるなら昇格しない / blocked のときだけ段階昇格する /
StealthyFetcher は最終段限定で上限超過したら昇格しない」という昇格ラダーの不変条件を、
ネット非依存（urllib 段と scrapling 段を monkeypatch でモック）に固定する。

これが壊れると (1) 取れる URL にまで重いヘッドレスブラウザを起動してコスト爆発、
(2) blocked を素通りして OGP/発行日メタが null のまま、(3) StealthyFetcher 上限が
効かず 1 号の実行でブラウザを無制限起動、のいずれかが再発する。

scrapling は実際には呼ばない（_fetch_scrapling を monkeypatch するため install 不要・
network marker 不要でユニットテストできる = 遅延 import 設計の検証も兼ねる）。
"""
from __future__ import annotations

import pytest

from tools import _fetch
from tools._fetch import fetch_with_escalation


@pytest.fixture(autouse=True)
def _reset_budget():
    """各テストの前後で StealthyFetcher の起動カウンタをリセットする。"""
    _fetch.reset_stealthy_budget()
    yield
    _fetch.reset_stealthy_budget()


def _stub_stage(monkeypatch, urllib_ret, fetcher_ret=None, stealthy_ret=None):
    """各段の fetch をスタブに差し替える。

    各 *_ret は (status, html, note) タプル。fetcher/stealthy が呼ばれたら記録する。
    返り値は呼び出し記録 dict（どの段が呼ばれたか検証用）。
    """
    calls = {"urllib": 0, "fetcher": 0, "stealthy": 0}

    def fake_urllib(url, timeout):
        calls["urllib"] += 1
        return urllib_ret

    def fake_scrapling(url, timeout, *, stealthy):
        if stealthy:
            calls["stealthy"] += 1
            assert stealthy_ret is not None, "StealthyFetcher へ昇格してはいけない契約"
            return stealthy_ret
        calls["fetcher"] += 1
        assert fetcher_ret is not None, "Fetcher へ昇格してはいけない契約"
        return fetcher_ret

    monkeypatch.setattr(_fetch, "_fetch_urllib", fake_urllib)
    monkeypatch.setattr(_fetch, "_fetch_scrapling", fake_scrapling)
    return calls


# ── ① urllib 200 なら昇格しない ──────────────────────────────────────────────


def test_urllib_200_does_not_escalate(monkeypatch) -> None:
    calls = _stub_stage(monkeypatch, urllib_ret=(200, "<html>ok</html>", "ok"))
    res = fetch_with_escalation("https://example.com/a")
    assert res.ok
    assert res.stage == "urllib"
    assert res.status == 200
    assert calls == {"urllib": 1, "fetcher": 0, "stealthy": 0}


def test_non_blocked_404_does_not_escalate(monkeypatch) -> None:
    """blocked でない 404 は昇格しても無駄なので urllib 段で確定（真の死リンク）。"""
    calls = _stub_stage(monkeypatch, urllib_ret=(404, None, "HTTPError 404"))
    res = fetch_with_escalation("https://example.com/dead")
    assert not res.ok
    assert res.stage == "urllib"
    assert res.status == 404
    assert calls["fetcher"] == 0 and calls["stealthy"] == 0


# ── ② 403/blocked 検知で Fetcher へ昇格 ──────────────────────────────────────


def test_urllib_403_escalates_to_fetcher(monkeypatch) -> None:
    calls = _stub_stage(
        monkeypatch,
        urllib_ret=(403, None, "HTTPError 403"),
        fetcher_ret=(200, "<html>via fetcher</html>", "ok"),
    )
    res = fetch_with_escalation("https://nikkei.example/x")
    assert res.ok
    assert res.stage == "fetcher"
    assert calls == {"urllib": 1, "fetcher": 1, "stealthy": 0}


def test_200_challenge_page_escalates_to_fetcher(monkeypatch) -> None:
    """200 でも Cloudflare challenge 痕跡があれば blocked 扱いで昇格する。"""
    calls = _stub_stage(
        monkeypatch,
        urllib_ret=(200, "<html>Just a moment...</html>", "ok"),
        fetcher_ret=(200, "<html>real content</html>", "ok"),
    )
    res = fetch_with_escalation("https://cf.example/x")
    assert res.ok
    assert res.stage == "fetcher"
    assert calls["fetcher"] == 1


# ── ③ Fetcher も blocked のときだけ StealthyFetcher（最終段限定・直接飛ばない）──


def test_fetcher_blocked_escalates_to_stealthy(monkeypatch) -> None:
    calls = _stub_stage(
        monkeypatch,
        urllib_ret=(403, None, "HTTPError 403"),
        fetcher_ret=(403, None, "blocked"),
        stealthy_ret=(200, "<html>via stealthy</html>", "ok"),
    )
    res = fetch_with_escalation("https://bloomberg.example/x")
    assert res.ok
    assert res.stage == "stealthy"
    assert calls == {"urllib": 1, "fetcher": 1, "stealthy": 1}


def test_does_not_jump_directly_to_stealthy(monkeypatch) -> None:
    """urllib が blocked でも、Fetcher を飛ばして StealthyFetcher へ直行しない契約。"""
    calls = _stub_stage(
        monkeypatch,
        urllib_ret=(403, None, "HTTPError 403"),
        fetcher_ret=(200, "<html>fetcher saved it</html>", "ok"),
        stealthy_ret=(200, "<html>should not reach</html>", "ok"),
    )
    res = fetch_with_escalation("https://x.example/y")
    assert res.stage == "fetcher"
    assert calls["stealthy"] == 0  # Fetcher で取れたら StealthyFetcher は呼ばない


# ── ④ StealthyFetcher 上限カウンタ超過で昇格拒否 ─────────────────────────────


def test_stealthy_budget_exhausted_refuses_escalation(monkeypatch) -> None:
    """StealthyFetcher の起動が上限に達したら昇格せず失敗を返す（暴発防止）。"""
    # budget=1 で 2 回 blocked URL を投げる。1 回目は stealthy 起動、2 回目は拒否。
    _stub_stage(
        monkeypatch,
        urllib_ret=(403, None, "HTTPError 403"),
        fetcher_ret=(403, None, "blocked"),
        stealthy_ret=(403, None, "still blocked"),  # stealthy も blocked → 起動はする
    )
    # 1 回目: budget=1 内なので stealthy を起動する（結果は blocked だが起動した）
    res1 = fetch_with_escalation("https://a.example/1", stealthy_budget=1)
    assert res1.stage == "stealthy"  # 起動した
    assert not res1.ok

    # 2 回目: budget 超過 → stealthy を起動せず fetcher 段で打ち切り、error に上限超過を記録
    res2 = fetch_with_escalation("https://a.example/2", stealthy_budget=1)
    assert res2.stage == "fetcher"  # stealthy へ昇格しなかった
    assert not res2.ok
    assert "上限" in (res2.error or "")


def test_allow_stealthy_false_skips_final_stage(monkeypatch) -> None:
    """allow_stealthy=False なら 3 段目を一切使わない（urllib + Fetcher のみ）。"""
    calls = _stub_stage(
        monkeypatch,
        urllib_ret=(403, None, "HTTPError 403"),
        fetcher_ret=(403, None, "blocked"),
        stealthy_ret=(200, "<html>never</html>", "ok"),
    )
    res = fetch_with_escalation("https://x.example/z", allow_stealthy=False)
    assert res.stage == "fetcher"
    assert calls["stealthy"] == 0
    assert not res.ok


# ── blocked 判定の純関数 ─────────────────────────────────────────────────────


def test_looks_blocked_statuses() -> None:
    assert _fetch._looks_blocked(403, None) is True
    assert _fetch._looks_blocked(429, None) is True
    assert _fetch._looks_blocked(503, None) is True
    assert _fetch._looks_blocked(200, "<html>fine content</html>") is False
    assert _fetch._looks_blocked(404, None) is False


def test_looks_blocked_challenge_markers() -> None:
    assert _fetch._looks_blocked(200, "<html>cf-challenge here</html>") is True
    assert _fetch._looks_blocked(200, "Checking your browser before accessing") is True


def test_non_http_scheme_rejected_without_fetch(monkeypatch) -> None:
    """SSRF 予防: http(s) 以外のスキームは 0 段目で拒否し、どの段の fetch も呼ばない契約。"""
    def _boom(url, timeout):
        raise AssertionError("スキームガードを素通りして urllib 段が呼ばれた")

    monkeypatch.setattr(_fetch, "_fetch_urllib", _boom)
    res = fetch_with_escalation("file:///C:/Windows/win.ini")
    assert res.ok is False
    assert res.stage == "guard"
    assert "unsupported url scheme" in (res.error or "")
