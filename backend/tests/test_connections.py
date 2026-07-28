"""Direct coverage for connections.py's live-connection registry.

Every existing caller mocks this module away — scheduler tests patch
`deliver_to_session` out, and api/ws.py is only exercised through the WS
harness — so the fan-out itself had no direct test. These tests call
register/unregister/deliver_to_session synchronously with plain callables:
no WebSocket, no scheduler, no event loop.

`connections._hooks` is process-wide module state, so setUp snapshots it and
tearDown restores it; a failure mid-test can never leak a registration into a
later test.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import connections  # noqa: E402


def _boom(_content, _title):
    """A hook standing in for a stale connection: raises instead of delivering."""
    raise RuntimeError("dead connection")


class ConnectionRegistryTests(unittest.TestCase):
    def setUp(self):
        self._saved_hooks = dict(connections._hooks)

    def tearDown(self):
        connections._hooks.clear()
        connections._hooks.update(self._saved_hooks)

    def test_registered_hook_receives_delivery(self):
        hook = mock.Mock()
        connections.register("test-sess-1", hook)

        self.assertTrue(connections.deliver_to_session("test-sess-1", "body", "Titel"))
        hook.assert_called_once_with("body", "Titel")

    def test_fan_out_calls_every_hook_for_the_session(self):
        first, second = mock.Mock(), mock.Mock()
        connections.register("test-sess-2", first)
        connections.register("test-sess-2", second)

        self.assertTrue(connections.deliver_to_session("test-sess-2", "body", "Titel"))
        first.assert_called_once_with("body", "Titel")
        second.assert_called_once_with("body", "Titel")

    def test_one_failing_hook_does_not_stop_the_others(self):
        healthy = mock.Mock()
        connections.register("test-sess-3", _boom)
        connections.register("test-sess-3", healthy)

        # The raising hook is swallowed (set order is arbitrary, so it may run
        # first or last) — the healthy connection still gets the message.
        self.assertTrue(connections.deliver_to_session("test-sess-3", "body"))
        healthy.assert_called_once_with("body", "")

    def test_all_hooks_failing_reports_not_delivered(self):
        connections.register("test-sess-4", _boom)

        # scheduler._deliver falls back to _store_offline on False, so a session
        # whose every connection is dead must not report a delivery.
        self.assertFalse(connections.deliver_to_session("test-sess-4", "body"))

    def test_unregister_removes_only_the_given_hook(self):
        gone, kept = mock.Mock(), mock.Mock()
        connections.register("test-sess-5", gone)
        connections.register("test-sess-5", kept)

        connections.unregister("test-sess-5", gone)

        self.assertTrue(connections.deliver_to_session("test-sess-5", "body"))
        gone.assert_not_called()
        kept.assert_called_once_with("body", "")

    def test_unregister_last_hook_drops_the_session_key(self):
        hook = mock.Mock()
        connections.register("test-sess-6", hook)
        self.assertIn("test-sess-6", connections._hooks)

        connections.unregister("test-sess-6", hook)

        # Not just an empty set left behind — the key itself is gone.
        self.assertNotIn("test-sess-6", connections._hooks)

    def test_unregister_unknown_session_or_hook_is_a_no_op(self):
        connections.unregister("test-sess-7", mock.Mock())  # session never registered

        kept = mock.Mock()
        connections.register("test-sess-7", kept)
        connections.unregister("test-sess-7", mock.Mock())  # hook never registered

        self.assertTrue(connections.deliver_to_session("test-sess-7", "body"))
        kept.assert_called_once_with("body", "")

    def test_delivery_without_a_live_session_reports_false(self):
        other = mock.Mock()
        connections.register("test-sess-8", other)

        self.assertFalse(connections.deliver_to_session("test-sess-unknown", "body"))
        self.assertFalse(connections.deliver_to_session(None, "body"))
        other.assert_not_called()


if __name__ == "__main__":
    unittest.main()
