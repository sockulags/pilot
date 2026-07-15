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

Set ``PILOT_ALLOW_PRIVATE_FETCH=1`` to allow loopback/private targets (local
test fixtures, deliberate localhost API calls). Like the bind-host downgrade in
``config.py``, the safe default holds unless you explicitly opt out.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit

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
