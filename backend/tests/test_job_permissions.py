import asyncio
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class ProfileMappingTests(unittest.TestCase):
    """The capability policy: which tools each named profile permits."""

    def test_reminder_only_permits_nothing(self):
        from job_permissions import tool_allowed

        for tool in ("read_file", "run_command", "click", "web_search"):
            self.assertFalse(tool_allowed(tool, "reminder-only"))

    def test_read_only_denies_shell_and_desktop_allows_read(self):
        from job_permissions import tool_allowed

        self.assertTrue(tool_allowed("read_file", "read-only"))
        self.assertTrue(tool_allowed("list_dir", "read-only"))
        self.assertFalse(tool_allowed("run_command", "read-only"))
        self.assertFalse(tool_allowed("click", "read-only"))
        self.assertFalse(tool_allowed("web_search", "read-only"))

    def test_web_only_allows_web_denies_shell_desktop(self):
        from job_permissions import tool_allowed

        self.assertTrue(tool_allowed("web_search", "web-only"))
        self.assertTrue(tool_allowed("web_research", "web-only"))
        self.assertTrue(tool_allowed("read_file", "web-only"))
        self.assertFalse(tool_allowed("run_command", "web-only"))
        self.assertFalse(tool_allowed("type_text", "web-only"))

    def test_shell_profile_allows_run_command_not_desktop(self):
        from job_permissions import tool_allowed

        self.assertTrue(tool_allowed("run_command", "shell"))
        self.assertFalse(tool_allowed("click", "shell"))

    def test_desktop_control_allows_desktop_not_shell(self):
        from job_permissions import tool_allowed

        for tool in ("click", "click_element", "type_text", "key_press", "hotkey", "scroll"):
            self.assertTrue(tool_allowed(tool, "desktop-control"))
        self.assertFalse(tool_allowed("run_command", "desktop-control"))

    def test_unknown_profile_falls_back_to_read_only(self):
        from job_permissions import tool_allowed

        self.assertTrue(tool_allowed("read_file", "bogus"))
        self.assertFalse(tool_allowed("run_command", "bogus"))

    def test_normalize_profile_defaults_by_kind(self):
        from job_permissions import normalize_profile

        self.assertEqual("reminder-only", normalize_profile(None, "reminder"))
        self.assertEqual("read-only", normalize_profile(None, "task"))
        self.assertEqual("shell", normalize_profile("shell", "task"))
        self.assertEqual("read-only", normalize_profile("nope", "task"))


class JobPermissionPersistenceTests(unittest.TestCase):
    """create_job persistence + back-compat for the permissions field."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        os.unlink(self.tmp.name)
        self._patch = mock.patch("jobs.JOBS_FILE", self.tmp.name)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def test_reminder_defaults_to_reminder_only(self):
        from jobs import create_job

        job = create_job(
            session_id="s", title="t", payload="p",
            schedule={"type": "interval", "interval_seconds": 60}, now=1000,
        )
        self.assertEqual("reminder-only", job["permissions"])

    def test_task_defaults_to_read_only(self):
        from jobs import create_job

        job = create_job(
            session_id="s", title="t", payload="p", kind="task",
            schedule={"type": "interval", "interval_seconds": 60}, now=1000,
        )
        self.assertEqual("read-only", job["permissions"])

    def test_explicit_permissions_persist(self):
        from jobs import create_job, get_job

        job = create_job(
            session_id="s", title="t", payload="p", kind="task", permissions="shell",
            schedule={"type": "interval", "interval_seconds": 60}, now=1000,
        )
        self.assertEqual("shell", get_job(job["id"])["permissions"])

    def test_invalid_permissions_fall_back_to_kind_default(self):
        from jobs import create_job

        job = create_job(
            session_id="s", title="t", payload="p", kind="task", permissions="bogus",
            schedule={"type": "interval", "interval_seconds": 60}, now=1000,
        )
        self.assertEqual("read-only", job["permissions"])

    def test_legacy_job_without_permissions_backfilled(self):
        import json
        from jobs import _load

        # A job written before the permissions field existed.
        with open(self.tmp.name, "w", encoding="utf-8") as f:
            json.dump({"jobs": [{"id": "x", "kind": "task", "schedule": {}}]}, f)
        loaded = _load()["jobs"][0]
        self.assertEqual("read-only", loaded["permissions"])

    def test_command_grammar_parses_profile_prefix(self):
        from jobs import parse_job_command

        spec = parse_job_command("every 10m task:[shell] run the build")
        self.assertEqual("task", spec["kind"])
        self.assertEqual("shell", spec["permissions"])
        self.assertEqual("run the build", spec["payload"])

        # Plain task -> no explicit profile (create_job applies read-only default).
        spec2 = parse_job_command("every 10m task: do a thing")
        self.assertIsNone(spec2["permissions"])

        # Unknown profile -> error.
        self.assertEqual("error", parse_job_command("every 10m task:[bogus] x")["action"])


class CancelJobTests(unittest.TestCase):
    """A running job is cancellable: cancel_job sets abort + cancels the handle."""

    def test_cancel_running_job(self):
        asyncio.run(self._cancel_running_job())

    async def _cancel_running_job(self):
        import scheduler

        abort = asyncio.Event()
        started = asyncio.Event()

        async def long_running():
            started.set()
            await asyncio.sleep(60)

        task = asyncio.create_task(long_running())
        scheduler._running_tasks["job1"] = task
        scheduler._abort_events["job1"] = abort
        try:
            await started.wait()
            self.assertTrue(scheduler.cancel_job("job1"))
            self.assertTrue(abort.is_set())
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(task.cancelled())
        finally:
            scheduler._running_tasks.pop("job1", None)
            scheduler._abort_events.pop("job1", None)

    def test_cancel_unknown_job_returns_false(self):
        import scheduler

        self.assertFalse(scheduler.cancel_job("nope"))


class AuditTrailTests(unittest.TestCase):
    """A fired job records a structured audit entry (tool calls, status, output)."""

    def test_build_audit_record_extracts_tool_history(self):
        import scheduler
        from agents.runtime_state import RuntimeState

        class FakeOutcome:
            status = "done"

        rs = RuntimeState()
        rs.record_tool_result("read_file", {"path": "README.md"}, "contents", True)
        rs.record_error("(tool 'run_command' not permitted ...; skipped)", "run_command", {"cmd": "x"})
        outcome = FakeOutcome()
        outcome.runtime_state = rs

        audit = scheduler.build_audit_record(outcome, "⏰ title\nbody")
        self.assertEqual("done", audit["status"])
        self.assertEqual("⏰ title\nbody", audit["output"])
        tools = [c["tool"] for c in audit["tool_calls"]]
        self.assertIn("read_file", tools)
        self.assertTrue(any("not permitted" in e.get("error", "") for e in audit["errors"]))

    def test_record_audit_persists_on_job(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmp.close()
        os.unlink(tmp.name)
        with mock.patch("jobs.JOBS_FILE", tmp.name):
            from jobs import create_job, record_audit, get_job

            job = create_job(
                session_id="s", title="t", payload="p", kind="task",
                schedule={"type": "interval", "interval_seconds": 60}, now=1000,
            )
            record_audit(job["id"], {"status": "delivered", "output": "ok", "tool_calls": []}, now=2000)
            stored = get_job(job["id"])
            self.assertEqual("delivered", stored["last_audit"]["status"])
            self.assertEqual(2000, stored["last_audit"]["ts"])
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)


if __name__ == "__main__":
    unittest.main()
