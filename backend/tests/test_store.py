import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class PersistedMessageCapTests(unittest.TestCase):
    def test_save_keeps_only_most_recent_messages_when_capped(self):
        import store

        messages = [{"role": "user", "content": str(i)} for i in range(10)]
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(store, "SESSIONS_DIR", tmp), \
                    mock.patch.object(store, "MAX_PERSISTED_MESSAGES", 3), \
                    mock.patch.object(store, "SESSIONS_MAX_AGE_DAYS", 0):
                store.save_session("sess", messages, turn=10)
                loaded = store.load_session("sess")

        self.assertEqual(3, len(loaded["messages"]))
        # The most recent tail is what survives; turn (resume state) is intact.
        self.assertEqual(
            [{"role": "user", "content": c} for c in ("7", "8", "9")],
            loaded["messages"],
        )
        self.assertEqual(10, loaded["turn"])

    def test_cap_zero_disables_trim(self):
        import store

        messages = [{"role": "user", "content": str(i)} for i in range(10)]
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(store, "SESSIONS_DIR", tmp), \
                    mock.patch.object(store, "MAX_PERSISTED_MESSAGES", 0), \
                    mock.patch.object(store, "SESSIONS_MAX_AGE_DAYS", 0):
                store.save_session("sess", messages, turn=10)
                loaded = store.load_session("sess")

        self.assertEqual(10, len(loaded["messages"]))

    def test_under_cap_is_unchanged(self):
        import store

        messages = [{"role": "user", "content": str(i)} for i in range(2)]
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(store, "SESSIONS_DIR", tmp), \
                    mock.patch.object(store, "MAX_PERSISTED_MESSAGES", 200), \
                    mock.patch.object(store, "SESSIONS_MAX_AGE_DAYS", 0):
                store.save_session("sess", messages, turn=2)
                loaded = store.load_session("sess")

        self.assertEqual(2, len(loaded["messages"]))


class AtomicWriteCleanupTests(unittest.TestCase):
    def test_failed_save_leaves_no_temp_file(self):
        import json as _json

        import store

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(store, "SESSIONS_DIR", tmp), \
                    mock.patch.object(store, "MAX_PERSISTED_MESSAGES", 0), \
                    mock.patch.object(store, "SESSIONS_MAX_AGE_DAYS", 0), \
                    mock.patch.object(
                        _json, "dump", side_effect=OSError("simulated disk full")
                    ):
                # save_session swallows the failure (best-effort persistence),
                # so this must not raise.
                store.save_session(
                    "abc123", [{"role": "user", "content": "hi"}], turn=1
                )

            # The atomic write failed, but its temp file must not linger.
            leftovers = os.listdir(tmp)
            self.assertEqual(
                [], leftovers, f"leaked temp file(s): {leftovers}"
            )


class SessionPruneTests(unittest.TestCase):
    def _touch(self, path: str, age_days: float) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write("{}")
        past = time.time() - age_days * 86400
        os.utime(path, (past, past))

    def test_prune_removes_only_stale_files(self):
        import store

        with tempfile.TemporaryDirectory() as tmp:
            old = os.path.join(tmp, "old.json")
            fresh = os.path.join(tmp, "fresh.json")
            other = os.path.join(tmp, "keep.txt")  # non-session file untouched
            self._touch(old, age_days=120)
            self._touch(fresh, age_days=1)
            self._touch(other, age_days=120)

            with mock.patch.object(store, "SESSIONS_DIR", tmp):
                removed = store.prune_old_sessions(max_age_days=90)

            self.assertEqual(1, removed)
            self.assertFalse(os.path.exists(old))
            self.assertTrue(os.path.exists(fresh))
            self.assertTrue(os.path.exists(other))

    def test_prune_disabled_when_max_age_non_positive(self):
        import store

        with tempfile.TemporaryDirectory() as tmp:
            old = os.path.join(tmp, "old.json")
            self._touch(old, age_days=1000)
            with mock.patch.object(store, "SESSIONS_DIR", tmp):
                removed = store.prune_old_sessions(max_age_days=0)
            self.assertEqual(0, removed)
            self.assertTrue(os.path.exists(old))

    def test_prune_missing_dir_is_safe(self):
        import store

        with mock.patch.object(store, "SESSIONS_DIR", os.path.join(tempfile.gettempdir(), "no_such_pilot_dir_xyz")):
            self.assertEqual(0, store.prune_old_sessions(max_age_days=1))

    def test_save_prunes_old_sibling_but_keeps_current(self):
        import store

        with tempfile.TemporaryDirectory() as tmp:
            stale = os.path.join(tmp, "stale.json")
            self._touch(stale, age_days=200)
            with mock.patch.object(store, "SESSIONS_DIR", tmp), \
                    mock.patch.object(store, "MAX_PERSISTED_MESSAGES", 200), \
                    mock.patch.object(store, "SESSIONS_MAX_AGE_DAYS", 90):
                store.save_session(
                    "current", [{"role": "user", "content": "hi"}], turn=1
                )

            # The freshly saved session survives; the stale sibling is pruned.
            self.assertTrue(os.path.exists(os.path.join(tmp, "current.json")))
            self.assertFalse(os.path.exists(stale))


if __name__ == "__main__":
    unittest.main()
