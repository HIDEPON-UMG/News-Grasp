"""公開URL取得でloopback/private networkと危険なredirectを拒否する境界。"""

from __future__ import annotations

import ipaddress
import http.client
import itertools
import socket
import time
import urllib.request
from typing import Any
from urllib.parse import urljoin, urlsplit


MAX_DNS_ADDRESSES = 8


def resolve_public_http_endpoint(url: str) -> tuple[str, str, int, tuple[str, ...]]:
    """URLと公開address集合を一度だけ解決し、pin可能な形で返す。"""
    raw = str(url).strip()
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("public_fetch_url_invalid") from exc
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise ValueError("public_fetch_scheme_invalid")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("public_fetch_userinfo_forbidden")
    host = (parsed.hostname or "").rstrip(".").casefold()
    if not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("public_fetch_host_forbidden")
    expected_port = 443 if parsed.scheme.casefold() == "https" else 80
    if port not in {None, expected_port}:
        raise ValueError("public_fetch_port_forbidden")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            answers = socket.getaddrinfo(host, expected_port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ValueError("public_fetch_dns_unverified") from exc
        addresses: set[str] = set()
        for item in itertools.islice(iter(answers), MAX_DNS_ADDRESSES * 4):
            if item[4]:
                addresses.add(str(item[4][0]).split("%", 1)[0])
            if len(addresses) >= MAX_DNS_ADDRESSES:
                break
        if not addresses:
            raise ValueError("public_fetch_dns_unverified")
        try:
            resolved = [ipaddress.ip_address(value) for value in sorted(addresses)]
        except ValueError as exc:
            raise ValueError("public_fetch_dns_invalid") from exc
    else:
        resolved = [literal]
    if any(not address.is_global for address in resolved):
        raise ValueError("public_fetch_address_forbidden")
    return raw, host, expected_port, tuple(str(address) for address in resolved)


def validate_public_http_url(url: str) -> str:
    return resolve_public_http_endpoint(url)[0]


def _connect_pinned(addresses: tuple[str, ...], port: int, timeout: float | object, source_address: Any = None) -> socket.socket:
    if len(addresses) > MAX_DNS_ADDRESSES:
        raise OSError("public_fetch_address_limit_exceeded")
    if timeout is socket._GLOBAL_DEFAULT_TIMEOUT:  # noqa: SLF001 - stdlib sentinel.
        budget = socket.getdefaulttimeout() or 30.0
    elif timeout is None:
        budget = 30.0
    else:
        budget = max(0.001, float(timeout))
    deadline = time.monotonic() + budget
    last_error: OSError | None = None
    for address in addresses:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("public_fetch_connect_deadline_exceeded")
        try:
            return socket.create_connection((address, port), remaining, source_address)
        except OSError as exc:
            last_error = exc
    raise last_error or OSError("public_fetch_connect_failed")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, *, addresses: tuple[str, ...], pinned_port: int, **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        self._addresses = addresses
        self._pinned_port = pinned_port

    def connect(self) -> None:
        self.sock = _connect_pinned(self._addresses, self._pinned_port, self.timeout, self.source_address)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, *, addresses: tuple[str, ...], pinned_port: int, **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        self._addresses = addresses
        self._pinned_port = pinned_port

    def connect(self) -> None:
        raw = _connect_pinned(self._addresses, self._pinned_port, self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req: urllib.request.Request):  # noqa: ANN201
        _, _, port, addresses = resolve_public_http_endpoint(req.full_url)
        return self.do_open(lambda host, **kwargs: _PinnedHTTPConnection(host, addresses=addresses, pinned_port=port, **kwargs), req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req: urllib.request.Request):  # noqa: ANN201
        _, _, port, addresses = resolve_public_http_endpoint(req.full_url)
        context = self._context
        return self.do_open(lambda host, **kwargs: _PinnedHTTPSConnection(host, addresses=addresses, pinned_port=port, context=context, **kwargs), req)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str):  # noqa: ANN401
        validated = validate_public_http_url(urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, validated)


def safe_urlopen(request: urllib.request.Request | str, *, timeout: float, context: Any = None):  # noqa: ANN201, ANN401
    """初期URLと各redirect先をpublic-address gateへ通して取得する。"""
    url = request.full_url if isinstance(request, urllib.request.Request) else str(request)
    validate_public_http_url(url)
    handlers: list[Any] = [urllib.request.ProxyHandler({}), _SafeRedirectHandler(), _PinnedHTTPHandler(), _PinnedHTTPSHandler(context=context)]
    opener = urllib.request.build_opener(*handlers)
    response = opener.open(request, timeout=timeout)
    try:
        validate_public_http_url(response.geturl())
    except Exception:
        response.close()
        raise
    return response
