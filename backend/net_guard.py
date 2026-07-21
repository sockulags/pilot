"""SSRF guard for outbound HTTP (fetch_url, http_request).

Both tools take a URL that may be steered by gathered evidence (a search result,
a page the model was told to read) and, until now, followed it anywhere —
including loopback, the LAN, and the cloud metadata endpoint (169.254.169.254),
re-following redirects with no re-check. That is a server-side request forgery
hole: a public page can 302 to ``http://169.254.169.254/`` and exfiltrate
instance credentials.

This mirrors ``config.py``'s fail-closed philosophy. By default a request whose
host resolves to a non-public address is refused *before* the socket is opened,
and the same check runs again on *every* redirect hop (via an httpx request
event hook), so the redirect-to-internal case is closed too. Classification uses
stdlib :mod:`ipaddress` (``is_private`` / ``is_loopback`` / ``is_link_local`` …)
exactly as ``config.py`` already does — no new dependency.

Validation alone is not enough: a hostname resolved once for the check and again
by the HTTP client at connect time can answer differently between the two
lookups (DNS rebinding — TTL=0, public IP for the guard, internal IP for the
socket). To close that window the two callers build their ``httpx`` clients with
a pinning network backend (:func:`pinned_transport` / :func:`pinned_async_transport`):
at TCP-connect time it resolves the host, validates the answer, and connects the
socket *to that exact validated IP*, so the address checked is the address dialed
— httpcore never re-resolves the name independently. The hostname is still used
for TLS SNI and the ``Host`` header (httpcore keys those on the request origin,
not on the connect address), so HTTPS to public hosts is unaffected. The
per-hop event-hook check (:func:`guard_request` / :func:`aguard_request`) still
runs on the initial request and every redirect, and pinning applies on each hop
too.

Set ``PILOT_ALLOW_PRIVATE_FETCH=1`` to allow loopback/private targets (local
test fixtures, deliberate localhost API calls). Like the bind-host downgrade in
``config.py``, the safe default holds unless you explicitly opt out — the pinning
backend then passes the hostname through untouched.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

import httpcore
import httpx

_IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

_TRUTHY = {"1", "true", "yes", "on"}


class BlockedHostError(Exception):
    """Raised when a request target resolves to a non-public address.

    Propagates out of the httpx call and is caught by each tool's existing
    error handling, so a blocked request returns that tool's normal failure
    value rather than crashing.
    """


def _allow_private() -> bool:
    """Read the opt-out at call time so tests/config can toggle it per-process."""
    return os.getenv("PILOT_ALLOW_PRIVATE_FETCH", "").strip().lower() in _TRUTHY


def _is_blocked_ip(ip: _IpAddress) -> bool:
    """True for any address that is not a routable public host.

    Covers loopback (127.0.0.0/8, ::1), RFC1918 (10/8, 172.16/12, 192.168/16),
    link-local incl. the cloud metadata address (169.254.0.0/16, fe80::/10),
    IPv6 unique-local (fc00::/7), and the reserved/multicast/unspecified spaces
    — all of which ``ipaddress`` already classifies. IPv4 smuggled inside an
    IPv4-mapped IPv6 address (``::ffff:169.254.169.254``) is unwrapped first so
    it cannot slip past the v6 checks.
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _resolve_ips(host: str) -> list[_IpAddress]:
    """Return every IP ``host`` maps to (a literal IP resolves to itself)."""
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return [ipaddress.ip_address(sockaddr[0]) for *_, sockaddr in infos]


def check_url(url: str) -> None:
    """Raise :class:`BlockedHostError` if ``url``'s host is non-public.

    A host that cannot be resolved is left for the HTTP client to fail on: there
    is no internal target to reach, so it is not an SSRF risk, and this keeps the
    guard from needing the network for hosts that were never going to connect.
    """
    if _allow_private():
        return
    host = urlsplit(url).hostname
    if not host:
        return
    try:
        ips = _resolve_ips(host)
    except socket.gaierror:
        return
    for ip in ips:
        if _is_blocked_ip(ip):
            raise BlockedHostError(
                f"refusing to reach {host!r}: resolves to non-public address "
                f"{ip} (set PILOT_ALLOW_PRIVATE_FETCH=1 to allow local/private "
                f"targets)"
            )


def guard_request(request) -> None:
    """httpx sync request event hook: validate one hop (initial or redirect)."""
    check_url(str(request.url))


async def aguard_request(request) -> None:
    """httpx async request event hook: validate one hop (initial or redirect)."""
    check_url(str(request.url))


def _pin_ip(host: str) -> str:
    """Resolve ``host`` to a validated public IP and return it as the dial target.

    Called by the pinning network backend at TCP-connect time. Because the same
    resolution that is validated here is the address the socket then connects to,
    there is no second, unchecked lookup for a rebinding answer to slip through.
    A blocked address raises :class:`BlockedHostError`; an unresolvable host is
    passed through unchanged (no internal target to reach — the client fails on
    it), matching :func:`check_url`. With the private opt-out set, the hostname
    is returned as-is so the client resolves and connects normally.
    """
    if _allow_private():
        return host
    try:
        ips = _resolve_ips(host)
    except socket.gaierror:
        return host
    for ip in ips:
        if _is_blocked_ip(ip):
            raise BlockedHostError(
                f"refusing to reach {host!r}: resolves to non-public address "
                f"{ip} (set PILOT_ALLOW_PRIVATE_FETCH=1 to allow local/private "
                f"targets)"
            )
    return str(ips[0])


class _PinnedBackend(httpcore.NetworkBackend):
    """Sync backend that dials the validated IP instead of re-resolving the host.

    Wraps the pool's real backend and only rewrites ``connect_tcp``'s target;
    everything else (TLS, unix sockets, sleeping) is delegated untouched.
    """

    def __init__(self, inner: httpcore.NetworkBackend) -> None:
        self._inner = inner

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        return self._inner.connect_tcp(
            _pin_ip(host), port, timeout=timeout,
            local_address=local_address, socket_options=socket_options,
        )

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        return self._inner.connect_unix_socket(
            path, timeout=timeout, socket_options=socket_options,
        )

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


class _AsyncPinnedBackend(httpcore.AsyncNetworkBackend):
    """Async counterpart of :class:`_PinnedBackend`."""

    def __init__(self, inner: httpcore.AsyncNetworkBackend) -> None:
        self._inner = inner

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        return await self._inner.connect_tcp(
            _pin_ip(host), port, timeout=timeout,
            local_address=local_address, socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        return await self._inner.connect_unix_socket(
            path, timeout=timeout, socket_options=socket_options,
        )

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def pinned_transport(**kwargs: Any) -> httpx.HTTPTransport:
    """An ``httpx.HTTPTransport`` that pins each connection to the validated IP.

    Pass to ``httpx.Client(transport=...)``. ``**kwargs`` forward to the normal
    transport (verify, retries, …). Wrapping the pool's own network backend keeps
    httpx's TLS/proxy/HTTP2 setup intact and only redirects the dial target.
    """
    transport = httpx.HTTPTransport(**kwargs)
    transport._pool._network_backend = _PinnedBackend(
        transport._pool._network_backend
    )
    return transport


def pinned_async_transport(**kwargs: Any) -> httpx.AsyncHTTPTransport:
    """Async counterpart of :func:`pinned_transport` for ``httpx.AsyncClient``."""
    transport = httpx.AsyncHTTPTransport(**kwargs)
    transport._pool._network_backend = _AsyncPinnedBackend(
        transport._pool._network_backend
    )
    return transport
