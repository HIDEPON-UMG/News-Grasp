from __future__ import annotations

import socket

import pytest

from tools import safe_public_fetch as safe_fetch


def test_dns_unique_address_set_is_bounded(monkeypatch) -> None:
    answers = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (f"8.8.8.{index}", 443))
        for index in range(1, 10)
    ]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: answers)
    *_, addresses = safe_fetch.resolve_public_http_endpoint("https://example.test/")
    assert len(addresses) == safe_fetch.MAX_DNS_ADDRESSES
    assert "8.8.8.9" not in addresses


def test_pinned_connect_uses_one_total_deadline(monkeypatch) -> None:
    observed: list[float] = []
    clock = iter([100.0, 100.0, 100.6, 101.1])
    monkeypatch.setattr(safe_fetch.time, "monotonic", lambda: next(clock))

    def fail(_address, timeout, _source):
        observed.append(timeout)
        raise OSError("fixture")

    monkeypatch.setattr(socket, "create_connection", fail)
    with pytest.raises(TimeoutError, match="deadline"):
        safe_fetch._connect_pinned(("198.51.100.1", "198.51.100.2", "198.51.100.3"), 443, 1.0)
    assert observed == [1.0, pytest.approx(0.4)]
