"""Direct coverage for scheduler.py's job-execution path.

Complements test_job_permissions.py (which covers cancel_job and
build_audit_record at the unit level) by exercising the pieces that had no
direct test: run_scheduler's tick loop, _run_task's timeout / cancellation /
exception handling, _execute_task's coordinator hand-off, and the
_store_offline delivery fallback.

The coordinator is always stubbed — no real coordinator run, no tool calls —
and the wall-clock timeout is monkeypatched to a tiny value so the timeout test
runs in milliseconds instead of the real 300s.
"""

import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _task_job(**over):
    job = {
        "id": "job1",
        "kind": "task",
        "session_id": "s",
        "title": "Nightly report",
        "payload": "summarise the day",
        "permissions": "read-only",
    }
    job.update(over)
    return job


class RunTaskDeliveryTests(unittest.TestCase):
    """_run_task's happy path: deliver to a live session, else store offline."""

    def test_completes_and_delivers_to_live_session(self):
        asyncio.run(self._run(delivered=True))

    def test_completes_and_stores_offline_when_no_live_session(self):
        asyncio.run(self._run(delivered=False))

    async def _run(self, delivered):
        import scheduler

        job = _task_job()

        async def fake_execute(j, abort):
            return "⏰ Nightly report\nbody", {"tool_calls": []}

        stored = {
            "messages": [{"role": "user", "content": "hi"}],
            "turn": 3,
            "cwd": "/tmp/p",
            "claude_session_id": "c",
            "codex_session_id": None,
            "agent": "claude",
            "model_mode": "auto",
            "route_mode": "auto",
        }
        with mock.patch.object(scheduler, "_execute_task", fake_execute), \
                mock.patch.object(scheduler, "deliver_to_session", return_value=delivered) as deliver, \
                mock.patch.object(scheduler, "load_session", return_value=stored), \
                mock.patch.object(scheduler, "save_session") as save, \
                mock.patch.object(scheduler, "mark_ran") as mark_ran, \
                mock.patch.object(scheduler, "record_audit") as record_audit:
            await scheduler._run_task(job)

        deliver.assert_called_once()
        if delivered:
            mark_ran.assert_called_once_with("job1", result="delivered")
            save.assert_not_called()
        else:
            mark_ran.assert_called_once_with("job1", result="stored")
            # _store_offline mirrored the store and appended the message.
            save.assert_called_once()
            appended = save.call_args.args[1]
            self.assertEqual("assistant", appended[-1]["role"])
            self.assertIn("body", appended[-1]["content"])

        audit = record_audit.call_args.args[1]
        self.assertEqual("delivered" if delivered else "stored", audit["status"])
        self.assertFalse(audit["timed_out"])
        # finally-block cleanup ran.
        self.assertNotIn("job1", scheduler._abort_events)
        self.assertNotIn("job1", scheduler._running_tasks)


class RunTaskTimeoutTests(unittest.TestCase):
    """A job exceeding JOB_MAX_RUNTIME_SECONDS is aborted, recorded, cleaned up."""

    def test_timeout_records_error_and_cleans_up(self):
        asyncio.run(self._run())

    async def _run(self):
        import scheduler

        job = _task_job()
        captured = {}

        async def slow_execute(j, abort):
            captured["abort"] = abort
            await asyncio.sleep(5)  # far longer than the patched timeout below
            return "never", {}

        # Pre-seed the in-flight guards the way run_scheduler would, so we can
        # assert the finally block clears them.
        scheduler._running.add("job1")
        scheduler._running_tasks["job1"] = mock.Mock()
        try:
            with mock.patch.object(scheduler, "JOB_MAX_RUNTIME_SECONDS", 0.05), \
                    mock.patch.object(scheduler, "_execute_task", slow_execute), \
                    mock.patch.object(scheduler, "deliver_to_session") as deliver, \
                    mock.patch.object(scheduler, "mark_ran") as mark_ran, \
                    mock.patch.object(scheduler, "record_audit") as record_audit:
                await scheduler._run_task(job)

            deliver.assert_not_called()
            mark_ran.assert_called_once_with("job1", result="error: timeout")
            self.assertTrue(captured["abort"].is_set())  # abort event was set
            audit = record_audit.call_args.args[1]
            self.assertEqual("timeout", audit["status"])
            self.assertTrue(audit["timed_out"])
            # finally cleaned up all three registries.
            self.assertNotIn("job1", scheduler._running)
            self.assertNotIn("job1", scheduler._running_tasks)
            self.assertNotIn("job1", scheduler._abort_events)
        finally:
            scheduler._running.discard("job1")
            scheduler._running_tasks.pop("job1", None)
            scheduler._abort_events.pop("job1", None)


class RunTaskErrorTests(unittest.TestCase):
    """A coordinator that raises is recorded as an error with its message."""

    def test_coordinator_exception_recorded_as_error(self):
        asyncio.run(self._run())

    async def _run(self):
        import scheduler

        job = _task_job()

        async def boom(*a, **k):
            raise ValueError("coordinator exploded")

        async def no_memories(_):
            return []

        with mock.patch("agents.coordinator.run_coordinator", boom), \
                mock.patch("memory.search_memories", no_memories), \
                mock.patch("memory.format_for_prompt", return_value=""), \
                mock.patch.object(scheduler, "load_session", return_value={"messages": []}), \
                mock.patch.object(scheduler, "deliver_to_session") as deliver, \
                mock.patch.object(scheduler, "mark_ran") as mark_ran, \
                mock.patch.object(scheduler, "record_audit") as record_audit:
            await scheduler._run_task(job)

        deliver.assert_not_called()
        mark_ran.assert_called_once_with("job1", result="error: coordinator exploded")
        audit = record_audit.call_args.args[1]
        self.assertEqual("error", audit["status"])
        self.assertIn("coordinator exploded", audit["output"])
        self.assertIn("coordinator exploded", audit["errors"])


class ExecuteTaskTests(unittest.TestCase):
    """_execute_task hands the instruction to the coordinator and composes a reply."""

    def test_runs_coordinator_and_composes_reply(self):
        asyncio.run(self._run())

    async def _run(self):
        import scheduler
        from agents.loop import LoopOutcome
        from agents.runtime_state import RuntimeState

        job = _task_job(title="Weather")
        outcome = LoopOutcome("done", action_log="checked forecast", detail="raw",
                              runtime_state=RuntimeState())

        async def fake_coordinator(task_text, *a, **k):
            self.assertEqual("summarise the day", task_text)
            return outcome

        async def fake_compose(conversation, grounding, model, memories):
            yield "Sunny "
            yield "and warm"

        async def no_memories(_):
            return []

        with mock.patch("agents.coordinator.run_coordinator", fake_coordinator), \
                mock.patch("agents.orchestrator.compose_reply", fake_compose), \
                mock.patch("memory.search_memories", no_memories), \
                mock.patch("memory.format_for_prompt", return_value=""), \
                mock.patch.object(scheduler, "load_session", return_value={"messages": []}):
            content, audit = await scheduler._execute_task(job, asyncio.Event())

        self.assertEqual("⏰ Weather\nSunny and warm", content)
        self.assertEqual("done", audit["status"])
        self.assertEqual(content, audit["output"])

    def test_needs_input_short_circuits_without_compose(self):
        asyncio.run(self._run_needs_input())

    async def _run_needs_input(self):
        import scheduler
        from agents.loop import LoopOutcome
        from agents.runtime_state import RuntimeState

        job = _task_job(title="Deploy")
        outcome = LoopOutcome("needs_input", detail="which environment?",
                              runtime_state=RuntimeState())

        async def fake_coordinator(*a, **k):
            return outcome

        async def fake_compose(*a, **k):
            raise AssertionError("compose_reply must not run on needs_input")
            yield  # pragma: no cover

        async def no_memories(_):
            return []

        with mock.patch("agents.coordinator.run_coordinator", fake_coordinator), \
                mock.patch("agents.orchestrator.compose_reply", fake_compose), \
                mock.patch("memory.search_memories", no_memories), \
                mock.patch("memory.format_for_prompt", return_value=""), \
                mock.patch.object(scheduler, "load_session", return_value={"messages": []}):
            content, audit = await scheduler._execute_task(job, asyncio.Event())

        self.assertEqual("⏰ Deploy: which environment?", content)


class CancelInFlightTests(unittest.TestCase):
    """cancel_job cancels a genuinely in-flight _run_task end-to-end.

    Distinct from test_job_permissions.CancelJobTests, which cancels a bare
    asyncio.Task: here the cancelled coroutine IS _run_task, so we also assert
    its CancelledError branch records "cancelled" and its finally cleans up.
    """

    def test_cancel_running_run_task(self):
        asyncio.run(self._run())

    async def _run(self):
        import scheduler

        job = _task_job(id="jobC")
        started = asyncio.Event()

        async def hanging_execute(j, abort):
            started.set()
            await asyncio.sleep(60)
            return "never", {}

        with mock.patch.object(scheduler, "_execute_task", hanging_execute), \
                mock.patch.object(scheduler, "deliver_to_session") as deliver, \
                mock.patch.object(scheduler, "mark_ran") as mark_ran, \
                mock.patch.object(scheduler, "record_audit") as record_audit:
            # Mirror run_scheduler's book-keeping so cancel_job can find the task.
            task = asyncio.create_task(scheduler._run_task(job))
            scheduler._running.add("jobC")
            scheduler._running_tasks["jobC"] = task
            try:
                await started.wait()  # _run_task registered its abort event by now
                self.assertTrue(scheduler.cancel_job("jobC"))
                self.assertTrue(scheduler._abort_events.get("jobC") is None
                                or scheduler._abort_events["jobC"].is_set())
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertTrue(task.cancelled())
            finally:
                scheduler._running.discard("jobC")
                scheduler._running_tasks.pop("jobC", None)
                scheduler._abort_events.pop("jobC", None)

        deliver.assert_not_called()
        mark_ran.assert_called_once_with("jobC", result="cancelled")
        audit = record_audit.call_args.args[1]
        self.assertEqual("cancelled", audit["status"])
        # _run_task's finally still cleaned up despite the re-raised cancellation.
        self.assertNotIn("jobC", scheduler._running)
        self.assertNotIn("jobC", scheduler._running_tasks)
        self.assertNotIn("jobC", scheduler._abort_events)


class RunSchedulerTests(unittest.TestCase):
    """run_scheduler's tick loop: reminders inline, tasks spawned, guard + errors."""

    def test_tick_delivers_reminder_and_spawns_task(self):
        asyncio.run(self._run())

    async def _run(self):
        import scheduler

        reminder = {"id": "r", "kind": "reminder", "session_id": "s", "title": "t"}
        task = _task_job(id="t1")
        spawned = asyncio.Event()

        async def fake_run_task(job):
            spawned.set()

        # sleep raises CancelledError to break the otherwise-infinite loop after
        # exactly one tick.
        async def stop_sleep(_):
            raise asyncio.CancelledError

        with mock.patch.object(scheduler, "reconcile_on_start") as reconcile, \
                mock.patch.object(scheduler, "due_jobs", return_value=[reminder, task]), \
                mock.patch.object(scheduler, "reminder_content", return_value="ping"), \
                mock.patch.object(scheduler, "_deliver") as deliver, \
                mock.patch.object(scheduler, "mark_ran") as mark_ran, \
                mock.patch.object(scheduler, "_run_task", fake_run_task), \
                mock.patch("asyncio.sleep", stop_sleep):
            try:
                with self.assertRaises(asyncio.CancelledError):
                    await scheduler.run_scheduler()
                reconcile.assert_called_once()
                deliver.assert_called_once_with(reminder, "ping")
                mark_ran.assert_called_once_with("r", result="delivered")
                # The task-kind job was registered as in-flight and spawned.
                self.assertIn("t1", scheduler._running)
                await spawned.wait()
            finally:
                scheduler._running.discard("t1")
                scheduler._running_tasks.pop("t1", None)

    def test_already_running_task_is_skipped(self):
        asyncio.run(self._run_guard())

    async def _run_guard(self):
        import scheduler

        task = _task_job(id="busy")
        scheduler._running.add("busy")

        async def stop_sleep(_):
            raise asyncio.CancelledError

        try:
            with mock.patch.object(scheduler, "reconcile_on_start"), \
                    mock.patch.object(scheduler, "due_jobs", return_value=[task]), \
                    mock.patch.object(scheduler, "_run_task") as run_task, \
                    mock.patch("asyncio.sleep", stop_sleep):
                with self.assertRaises(asyncio.CancelledError):
                    await scheduler.run_scheduler()
                run_task.assert_not_called()  # in-flight guard skipped it
        finally:
            scheduler._running.discard("busy")

    def test_tick_swallows_due_jobs_error(self):
        asyncio.run(self._run_error())

    async def _run_error(self):
        import scheduler

        async def stop_sleep(_):
            raise asyncio.CancelledError

        with mock.patch.object(scheduler, "reconcile_on_start"), \
                mock.patch.object(scheduler, "due_jobs", side_effect=RuntimeError("store down")), \
                mock.patch("asyncio.sleep", stop_sleep):
            # The tick's except-Exception swallows the error; the loop proceeds to
            # sleep, which raises CancelledError and exits cleanly.
            with self.assertRaises(asyncio.CancelledError):
                await scheduler.run_scheduler()


if __name__ == "__main__":
    unittest.main()
