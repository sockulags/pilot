import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config import resolve_bind_host


class BindHostGuardTests(unittest.TestCase):
    """The README promises exposure beyond loopback only happens behind a
    private network AND with tokens set. resolve_bind_host enforces the token
    half in code (fail closed): a non-loopback bind host with an empty token is
    downgraded to 127.0.0.1 with a warning, so an unauthenticated server is
    never reachable from the network by accident."""

    def test_non_loopback_without_token_falls_back_to_loopback(self):
        self.assertEqual(
            "127.0.0.1",
            resolve_bind_host("Backend", "0.0.0.0", "", "PILOT_AUTH_TOKEN"),
        )

    def test_non_loopback_without_token_warns(self):
        with self.assertLogs("config", level="WARNING") as logs:
            resolve_bind_host("MCP", "0.0.0.0", "", "PILOT_MCP_AUTH_TOKEN")
        self.assertTrue(any("PILOT_MCP_AUTH_TOKEN" in line for line in logs.output))
        self.assertTrue(any("127.0.0.1" in line for line in logs.output))

    def test_non_loopback_with_token_is_honored(self):
        self.assertEqual(
            "0.0.0.0",
            resolve_bind_host("Backend", "0.0.0.0", "s3cret", "PILOT_AUTH_TOKEN"),
        )

    def test_loopback_without_token_is_allowed(self):
        for host in ("127.0.0.1", "localhost", "LOCALHOST", "::1", "127.0.0.2"):
            self.assertEqual(
                host, resolve_bind_host("Backend", host, "", "PILOT_AUTH_TOKEN")
            )

    def test_lan_ip_without_token_falls_back(self):
        self.assertEqual(
            "127.0.0.1",
            resolve_bind_host("Backend", "192.168.1.20", "", "PILOT_AUTH_TOKEN"),
        )

    def test_hostname_without_token_falls_back(self):
        # A hostname could resolve to anything, so it counts as exposed.
        self.assertEqual(
            "127.0.0.1",
            resolve_bind_host("MCP", "my-pc.tailnet.ts.net", "", "PILOT_MCP_AUTH_TOKEN"),
        )

    def test_hostname_with_token_is_honored(self):
        self.assertEqual(
            "my-pc.tailnet.ts.net",
            resolve_bind_host(
                "MCP", "my-pc.tailnet.ts.net", "s3cret", "PILOT_MCP_AUTH_TOKEN"
            ),
        )

    def test_whitespace_only_token_does_not_count(self):
        self.assertEqual(
            "127.0.0.1",
            resolve_bind_host("Backend", "0.0.0.0", "   ", "PILOT_AUTH_TOKEN"),
        )

    def test_empty_host_defaults_to_loopback(self):
        self.assertEqual(
            "127.0.0.1", resolve_bind_host("Backend", "", "", "PILOT_AUTH_TOKEN")
        )
        self.assertEqual(
            "127.0.0.1", resolve_bind_host("Backend", "   ", "", "PILOT_AUTH_TOKEN")
        )

    def test_ipv6_any_without_token_falls_back(self):
        self.assertEqual(
            "127.0.0.1", resolve_bind_host("Backend", "::", "", "PILOT_AUTH_TOKEN")
        )


if __name__ == "__main__":
    unittest.main()
