"""SSRF guard: fetch_url / http_request must refuse non-public targets.

fetch_url and http_request follow a URL that gathered evidence can steer, so a
public page that 302s to http://169.254.169.254/ (cloud metadata) or a bare
http://10.0.0.1/ must be refused — before the socket opens and again on every
redirect hop. This mirrors test_bind_host_guard.py: the safe behaviour is
enforced in code (fail closed), and only an explicit opt-out relaxes it.
"""

import asyncio
import contextlib
import os
import socket
import sys
import unittest
from collections.abc import Iterable
from typing import Any
from unittest import mock

import httpcore
import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import net_guard  # noqa: E402
from net_guard import BlockedHostError, check_url  # noqa: E402

# A routable public literal — used as the *initial* URL in redirect tests so the
# first hop is allowed and only the internal redirect target is blocked.
_PUBLIC_IP = "93.184.216.34"


def _fake_resolver(mapping):
    """socket.getaddrinfo stub mapping hostname -> IP string (no real DNS)."""

    def _resolve(host, *args, **kwargs):
        if host not in mapping:
            raise socket.gaierror(f"unmapped host {host!r}")
        ip = mapping[host]
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0))]

    return _resolve


def _sequential_resolver(hostname, answers):
    """getaddrinfo stub that returns a *different* IP on each successive call.

    Models DNS rebinding: the guard's lookup and the client's connect-time lookup
    hit the same name a few ms apart and get different answers. The last entry is
    reused for any further calls.
    """
    state = {"n": 0}

    def _resolve(host, *args, **kwargs):
        if host != hostname:
            raise socket.gaierror(f"unmapped host {host!r}")
        ip = answers[min(state["n"], len(answers) - 1)]
        state["n"] += 1
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0))]

    return _resolve


class _RecordingBackend(httpcore.NetworkBackend):
    """A sync backend that records every address it is asked to dial."""

    def __init__(self):
        self.dialed: list[str] = []

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        self.dialed.append(host)
        return httpcore.MockStream([])


class _AsyncRecordingBackend(httpcore.AsyncNetworkBackend):
    """Async counterpart of :class:`_RecordingBackend`."""

    def __init__(self):
        self.dialed: list[str] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.dialed.append(host)
        return httpcore.AsyncMockStream([])


@contextlib.contextmanager
def _mock_async_transport(handler):
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def fake_client(**kwargs):
        kwargs.pop("transport", None)
        return real_client(transport=transport, **kwargs)

    with mock.patch.object(httpx, "AsyncClient", fake_client):
        yield


@contextlib.contextmanager
def _mock_sync_transport(handler):
    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def fake_client(**kwargs):
        kwargs.pop("transport", None)
        return real_client(transport=transport, **kwargs)

    with mock.patch.object(httpx, "Client", fake_client):
        yield


class CheckUrlLiteralIpTests(unittest.TestCase):
    """A URL whose host is a literal internal IP is refused with no DNS lookup."""

    def test_loopback_blocked(self):
        for url in ("http://127.0.0.1/", "http://127.0.0.53/", "http://[::1]/"):
            with self.assertRaises(BlockedHostError, msg=url):
                check_url(url)

    def test_rfc1918_ranges_blocked(self):
        for url in ("http://10.0.0.5/", "http://172.16.9.9/", "http://192.168.1.1/"):
            with self.assertRaises(BlockedHostError, msg=url):
                check_url(url)

    def test_link_local_and_metadata_blocked(self):
        # 169.254.169.254 is the cloud metadata endpoint — the marquee SSRF target.
        for url in ("http://169.254.0.1/", "http://169.254.169.254/latest/meta-data/"):
            with self.assertRaises(BlockedHostError, msg=url):
                check_url(url)

    def test_ipv6_unique_local_and_link_local_blocked(self):
        for url in ("http://[fc00::1]/", "http://[fd12:3456::1]/", "http://[fe80::1]/"):
            with self.assertRaises(BlockedHostError, msg=url):
                check_url(url)

    def test_ipv4_mapped_ipv6_metadata_blocked(self):
        # ::ffff:169.254.169.254 smuggles the metadata IP inside an IPv6 literal.
        with self.assertRaises(BlockedHostError):
            check_url("http://[::ffff:169.254.169.254]/")

    def test_public_literal_ips_allowed(self):
        for url in (f"http://{_PUBLIC_IP}/", "http://[2606:4700:4700::1111]/"):
            check_url(url)  # must not raise


class CheckUrlResolutionTests(unittest.TestCase):
    """Hostnames are resolved and judged by the IP they map to."""

    def test_hostname_resolving_to_private_ip_blocked(self):
        with mock.patch.object(
            net_guard.socket, "getaddrinfo",
            _fake_resolver({"intranet.example": "10.1.2.3"}),
        ):
            with self.assertRaises(BlockedHostError):
                check_url("https://intranet.example/admin")

    def test_hostname_resolving_to_public_ip_allowed(self):
        with mock.patch.object(
            net_guard.socket, "getaddrinfo",
            _fake_resolver({"example.com": _PUBLIC_IP}),
        ):
            check_url("https://example.com/page")  # must not raise

    def test_any_private_answer_blocks_split_horizon(self):
        # If a name resolves to both a public and a private address, refuse it.
        def _resolve(host, *a, **k):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (_PUBLIC_IP, 0)),
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("192.168.0.9", 0)),
            ]

        with mock.patch.object(net_guard.socket, "getaddrinfo", _resolve):
            with self.assertRaises(BlockedHostError):
                check_url("https://rebind.example/")

    def test_unresolvable_host_is_left_to_the_client(self):
        # No internal target to reach -> not an SSRF risk; the client will error.
        with mock.patch.object(
            net_guard.socket, "getaddrinfo", _fake_resolver({}),
        ):
            check_url("https://nx.invalid/")  # must not raise


class PinnedConnectionRebindingTests(unittest.TestCase):
    """The address validated is the address dialed — no connect-time re-resolve.

    These exercise the pinning network backend directly (the mechanism wired into
    both httpx clients), simulating two sequential lookups for one host: the first
    (public) passes the guard, the second (internal) is what a rebinding attacker
    would swap in at connect time. Pinning validates its own resolution, so the
    internal address is refused and never dialed.
    """

    def test_public_host_is_dialed_by_validated_ip_not_hostname(self):
        # The connection targets the resolved+validated IP, so httpcore has no
        # hostname left to re-resolve independently.
        with mock.patch.object(
            net_guard.socket, "getaddrinfo",
            _fake_resolver({"example.com": _PUBLIC_IP}),
        ):
            inner = _RecordingBackend()
            net_guard._PinnedBackend(inner).connect_tcp("example.com", 443)
        self.assertEqual(inner.dialed, [_PUBLIC_IP])

    def test_connect_time_rebinding_to_metadata_is_blocked_sync(self):
        resolver = _sequential_resolver("rebind.example", [_PUBLIC_IP, "169.254.169.254"])
        inner = _RecordingBackend()
        with mock.patch.object(net_guard.socket, "getaddrinfo", resolver):
            # Lookup #1 (public) — the guard would let the request through.
            check_url("https://rebind.example/")
            backend = net_guard._PinnedBackend(inner)
            # Lookup #2 at connect time now answers with the metadata IP.
            with self.assertRaises(BlockedHostError):
                backend.connect_tcp("rebind.example", 443)
        # The internal address was never dialed — the socket was never opened.
        self.assertEqual(inner.dialed, [])

    def test_connect_time_rebinding_to_loopback_is_blocked_async(self):
        resolver = _sequential_resolver("rebind.example", [_PUBLIC_IP, "127.0.0.1"])
        inner = _AsyncRecordingBackend()
        with mock.patch.object(net_guard.socket, "getaddrinfo", resolver):
            check_url("https://rebind.example/")
            backend = net_guard._AsyncPinnedBackend(inner)
            with self.assertRaises(BlockedHostError):
                asyncio.run(backend.connect_tcp("rebind.example", 443))
        self.assertEqual(inner.dialed, [])

    def test_opt_out_passes_hostname_through_unpinned(self):
        # With the private opt-out, the backend must not resolve/pin — the client
        # is trusted to reach local/private targets by name.
        with mock.patch.dict(os.environ, {"PILOT_ALLOW_PRIVATE_FETCH": "1"}):
            inner = _RecordingBackend()
            net_guard._PinnedBackend(inner).connect_tcp("localhost", 8080)
        self.assertEqual(inner.dialed, ["localhost"])


class OptOutTests(unittest.TestCase):
    def test_env_flag_allows_private_targets(self):
        with mock.patch.dict(os.environ, {"PILOT_ALLOW_PRIVATE_FETCH": "1"}):
            check_url("http://127.0.0.1/")  # must not raise
            check_url("http://169.254.169.254/")  # must not raise

    def test_default_still_blocks(self):
        with mock.patch.dict(os.environ, {"PILOT_ALLOW_PRIVATE_FETCH": ""}):
            with self.assertRaises(BlockedHostError):
                check_url("http://127.0.0.1/")


class FetchUrlGuardTests(unittest.TestCase):
    """End-to-end through the real fetch_url path (offline mock transport)."""

    def test_redirect_to_internal_is_blocked_and_not_fetched(self):
        from tools.web import fetch_url

        seen: list[str] = []

        def handler(request):
            path = request.url.path
            seen.append(str(request.url))
            if path == "/start":
                return httpx.Response(
                    302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
                )
            return httpx.Response(200, text="SECRET-CREDENTIALS", headers={"content-type": "text/plain"})

        with _mock_async_transport(handler):
            out = asyncio.run(fetch_url(f"http://{_PUBLIC_IP}/start"))

        # The tool's normal failure contract, naming the block — not a crash.
        self.assertTrue(out.startswith("fetch_url failed:"), out)
        self.assertIn("BlockedHostError", out)
        # The internal hop was refused before it was ever sent to the transport.
        self.assertFalse(any("169.254.169.254" in u for u in seen), seen)
        self.assertNotIn("SECRET-CREDENTIALS", out)

    def test_legitimate_external_url_unchanged(self):
        from tools.web import fetch_url

        page = "<html><body><main><p>The real article body says hello world.</p></main></body></html>"

        def handler(request):
            return httpx.Response(200, text=page, headers={"content-type": "text/html"})

        with _mock_async_transport(handler):
            out = asyncio.run(fetch_url(f"http://{_PUBLIC_IP}/a", max_chars=4000))

        self.assertIn("The real article body says hello world.", out)


class HttpRequestGuardTests(unittest.TestCase):
    def test_direct_internal_url_blocked(self):
        from tools import extras

        def handler(request):
            return httpx.Response(200, json={"leak": "creds"})

        with _mock_sync_transport(handler):
            result = extras.http_request("http://169.254.169.254/latest/meta-data/")

        self.assertIn("error", result)
        self.assertIn("BlockedHostError", result["error"])

    def test_redirect_to_internal_is_blocked_and_not_fetched(self):
        from tools import extras

        seen: list[str] = []

        def handler(request):
            seen.append(str(request.url))
            if request.url.path == "/start":
                return httpx.Response(302, headers={"location": "http://10.0.0.9/admin"})
            return httpx.Response(200, json={"leak": "creds"})

        with _mock_sync_transport(handler):
            result = extras.http_request(f"http://{_PUBLIC_IP}/start")

        self.assertIn("error", result)
        self.assertIn("BlockedHostError", result["error"])
        self.assertFalse(any("10.0.0.9" in u for u in seen), seen)

    def test_legitimate_external_url_unchanged(self):
        from tools import extras

        def handler(request):
            return httpx.Response(200, json={"hello": "world"})

        with _mock_sync_transport(handler):
            result = extras.http_request(f"http://{_PUBLIC_IP}/x")

        self.assertEqual(result["json"], {"hello": "world"})
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
