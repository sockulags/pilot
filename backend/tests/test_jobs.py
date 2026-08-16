import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _ts(y, mo, d, hh, mm):
    return datetime(y, mo, d, hh, mm).timestamp()


class ComputeNextRunTests(unittest.TestCase):
    """Pure schedule math — no store involved."""

    def test_interval(self):
        from jobs import compute_next_run

        sched = {"type": "interval", "interval_seconds": 600}
        self.assertEqual(1000 + 600, compute_next_run(sched, 1000))
        self.assertIsNone(compute_next_run({"type": "interval", "interval_seconds": 0}, 1000))

    def test_daily_later_today(self):
        from jobs import compute_next_run

        # after = 2026-06-15 08:00, daily 09:00 -> same day 09:00
        after = _ts(2026, 6, 15, 8, 0)
        nxt = compute_next_run({"type": "daily", "time": "09:00"}, after)
        self.assertEqual(_ts(2026, 6, 15, 9, 0), nxt)

    def test_daily_passed_rolls_to_tomorrow(self):
        from jobs import compute_next_run

        # after = 2026-06-15 10:00, daily 09:00 -> next day 09:00
        after = _ts(2026, 6, 15, 10, 0)
        nxt = compute_next_run({"type": "daily", "time": "09:00"}, after)
        self.assertEqual(_ts(2026, 6, 16, 9, 0), nxt)

    def test_weekly_rolls_to_allowed_weekday(self):
        from jobs import compute_next_run

        # 2026-06-15 is a Monday (weekday 0). Job runs Fri (4) at 09:00.
        after = _ts(2026, 6, 15, 12, 0)  # Monday noon
        nxt = compute_next_run({"type": "weekly", "time": "09:00", "weekdays": [4]}, after)
        self.assertEqual(_ts(2026, 6, 19, 9, 0), nxt)  # Friday
        self.assertEqual(4, datetime.fromtimestamp(nxt).weekday())

    def test_weekly_same_day_later_counts(self):
        from jobs import compute_next_run

        # Monday 08:00, job runs Mondays 09:00 -> same Monday 09:00.
        after = _ts(2026, 6, 15, 8, 0)
        nxt = compute_next_run({"type": "weekly", "time": "09:00", "weekdays": [0]}, after)
        self.assertEqual(_ts(2026, 6, 15, 9, 0), nxt)

    def test_weekly_no_weekdays_is_never(self):
        from jobs import compute_next_run

        self.assertIsNone(compute_next_run({"type": "weekly", "time": "09:00", "weekdays": []}, 1000))

    def test_once_future_and_past(self):
        from jobs import compute_next_run

        sched = {"type": "once", "date": "2026-06-20", "time": "09:00"}
        target = _ts(2026, 6, 20, 9, 0)
        self.assertEqual(target, compute_next_run(sched, _ts(2026, 6, 15, 0, 0)))
        # After the target, never again.
        self.assertIsNone(compute_next_run(sched, _ts(2026, 6, 21, 0, 0)))


class ValidScheduleTests(unittest.TestCase):
    """The WS add_job boundary guard — what it accepts and what it turns away.

    Never raises: every malformed shape must come back as False so the handler
    can answer "Ogiltigt schema" instead of the tick crashing later.
    """

    def test_interval_requires_a_positive_period(self):
        from jobs import valid_schedule

        self.assertTrue(valid_schedule({"type": "interval", "interval_seconds": 60}))
        self.assertFalse(valid_schedule({"type": "interval", "interval_seconds": 0}))
        self.assertFalse(valid_schedule({"type": "interval", "interval_seconds": -60}))
        self.assertFalse(valid_schedule({"type": "interval"}))

    def test_interval_non_numeric_period_is_rejected_not_raised(self):
        from jobs import valid_schedule

        # int("abc") raises ValueError, int([60]) raises TypeError — the catch-all
        # turns both into a plain False.
        self.assertFalse(valid_schedule({"type": "interval", "interval_seconds": "abc"}))
        self.assertFalse(valid_schedule({"type": "interval", "interval_seconds": [60]}))

    def test_once_requires_a_parseable_date(self):
        from jobs import valid_schedule

        self.assertTrue(valid_schedule({"type": "once", "date": "2026-09-01", "time": "09:00"}))
        self.assertFalse(valid_schedule({"type": "once"}))
        self.assertFalse(valid_schedule({"type": "once", "date": "not-a-date", "time": "09:00"}))
        self.assertFalse(valid_schedule({"type": "once", "date": "2026-13-40", "time": "09:00"}))

    def test_once_rejects_an_out_of_range_time(self):
        from jobs import valid_schedule

        self.assertFalse(valid_schedule({"type": "once", "date": "2026-09-01", "time": "24:00"}))

    def test_daily_time_must_be_in_range(self):
        from jobs import valid_schedule

        self.assertTrue(valid_schedule({"type": "daily", "time": "09:00"}))
        self.assertFalse(valid_schedule({"type": "daily", "time": "24:00"}))
        self.assertFalse(valid_schedule({"type": "daily", "time": "07:65"}))

    def test_daily_missing_or_falsy_time_defaults_to_midnight(self):
        from jobs import valid_schedule

        # _parse_hhmm does str(time_str or "00:00"), so a falsy time is a default,
        # not a rejection. Only a value that stringifies without a ":" is malformed:
        # 730 becomes "730", the unpack raises, and the guard returns False.
        self.assertTrue(valid_schedule({"type": "daily"}))
        self.assertTrue(valid_schedule({"type": "daily", "time": None}))
        self.assertFalse(valid_schedule({"type": "daily", "time": 730}))

    def test_daily_accepts_single_digit_fields(self):
        from jobs import valid_schedule

        # Looser than the typed-command grammar's _valid_hhmm regex, which insists
        # on two-digit minutes. "9:5" is unambiguously 09:05, so the WS guard lets
        # it through rather than rejecting a computable time.
        self.assertTrue(valid_schedule({"type": "daily", "time": "9:5"}))

    def test_weekly_needs_a_non_empty_list_of_weekdays(self):
        from jobs import valid_schedule

        self.assertTrue(valid_schedule({"type": "weekly", "time": "09:00", "weekdays": [0, 2]}))
        self.assertTrue(valid_schedule({"type": "weekly", "time": "09:00", "weekdays": (0, 2)}))
        self.assertFalse(valid_schedule({"type": "weekly", "time": "09:00"}))
        self.assertFalse(valid_schedule({"type": "weekly", "time": "09:00", "weekdays": []}))
        self.assertFalse(valid_schedule({"type": "weekly", "time": "09:00", "weekdays": "mon"}))

    def test_weekly_weekdays_must_be_ints_in_range(self):
        from jobs import valid_schedule

        self.assertFalse(valid_schedule({"type": "weekly", "time": "09:00", "weekdays": ["0"]}))
        self.assertFalse(valid_schedule({"type": "weekly", "time": "09:00", "weekdays": [7]}))
        self.assertFalse(valid_schedule({"type": "weekly", "time": "09:00", "weekdays": [-1]}))
        self.assertFalse(valid_schedule({"type": "weekly", "time": "09:00", "weekdays": [0, 9]}))

    def test_weekly_time_is_validated_too(self):
        from jobs import valid_schedule

        self.assertFalse(valid_schedule({"type": "weekly", "time": "24:00", "weekdays": [0]}))

    def test_non_dict_and_unknown_type_are_rejected(self):
        from jobs import valid_schedule

        self.assertFalse(valid_schedule(None))
        self.assertFalse(valid_schedule("not a dict"))
        self.assertFalse(valid_schedule(["interval"]))
        self.assertFalse(valid_schedule({"type": "bogus"}))
        self.assertFalse(valid_schedule({}))


class OnceTargetTests(unittest.TestCase):
    def test_resolves_a_well_formed_date_and_time(self):
        from jobs import _once_target

        self.assertEqual(_ts(2026, 9, 1, 9, 0), _once_target({"date": "2026-09-01", "time": "09:00"}))
        # Missing time defaults to midnight rather than failing.
        self.assertEqual(_ts(2026, 9, 1, 0, 0), _once_target({"date": "2026-09-01"}))

    def test_returns_none_instead_of_raising(self):
        from jobs import _once_target

        self.assertIsNone(_once_target({}))
        self.assertIsNone(_once_target({"date": ""}))
        self.assertIsNone(_once_target({"date": "not-a-date"}))
        self.assertIsNone(_once_target({"date": "2026-13-40"}))
        self.assertIsNone(_once_target({"date": "2026-09-01", "time": "24:00"}))


class ParseHhmmTests(unittest.TestCase):
    def test_parses_and_defaults(self):
        from jobs import _parse_hhmm

        self.assertEqual((9, 0), _parse_hhmm("09:00"))
        self.assertEqual((23, 59), _parse_hhmm("23:59"))
        self.assertEqual((0, 0), _parse_hhmm(None))

    def test_raises_on_out_of_range_or_malformed(self):
        from jobs import _parse_hhmm

        for bad in ("24:00", "07:65", "-1:00"):
            with self.assertRaises(ValueError):
                _parse_hhmm(bad)
        # A string with no ":" fails at the tuple unpack, still a ValueError.
        for bad in ("730", "nine"):
            with self.assertRaises(ValueError):
                _parse_hhmm(bad)


class JobStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        os.unlink(self.tmp.name)  # start empty
        self._patch = mock.patch("jobs.JOBS_FILE", self.tmp.name)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def test_create_list_get_delete(self):
        from jobs import create_job, list_jobs, get_job, delete_job

        job = create_job(
            session_id="s1", title="Påminnelse", payload="drick vatten",
            schedule={"type": "interval", "interval_seconds": 600}, now=1000,
        )
        self.assertEqual(1600, job["next_run"])
        self.assertTrue(job["enabled"])

        self.assertEqual(1, len(list_jobs()))
        self.assertEqual(1, len(list_jobs(session_id="s1")))
        self.assertEqual(0, len(list_jobs(session_id="other")))
        self.assertEqual(job["id"], get_job(job["id"])["id"])

        self.assertTrue(delete_job(job["id"]))
        self.assertEqual([], list_jobs())
        self.assertFalse(delete_job("nope"))

    def test_persistence_round_trip(self):
        from jobs import create_job, _load

        create_job(
            session_id="s1", title="t", payload="p",
            schedule={"type": "daily", "time": "09:00"}, now=_ts(2026, 6, 15, 8, 0),
        )
        # Re-read from disk (fresh _load) to prove it persisted.
        self.assertEqual(1, len(_load()["jobs"]))

    def test_due_jobs(self):
        from jobs import create_job, due_jobs

        create_job(
            session_id="s1", title="t", payload="p",
            schedule={"type": "interval", "interval_seconds": 600}, now=1000,
        )  # next_run = 1600
        self.assertEqual([], due_jobs(now=1599))
        self.assertEqual(1, len(due_jobs(now=1600)))
        self.assertEqual(1, len(due_jobs(now=9999)))

    def test_set_enabled_pauses_from_due(self):
        from jobs import create_job, set_enabled, due_jobs

        job = create_job(
            session_id="s1", title="t", payload="p",
            schedule={"type": "interval", "interval_seconds": 600}, now=1000,
        )
        set_enabled(job["id"], False)
        self.assertEqual([], due_jobs(now=9999))  # paused -> not due

    def test_resume_reanchors_lapsed_recurring(self):
        from jobs import create_job, set_enabled, get_job

        job = create_job(
            session_id="s1", title="t", payload="p",
            schedule={"type": "interval", "interval_seconds": 600}, now=1000,
        )  # next_run = 1600
        set_enabled(job["id"], False)
        # Resume far in the future: next_run should move forward, not stay at 1600.
        set_enabled(job["id"], True, now=10000)
        self.assertEqual(10000 + 600, get_job(job["id"])["next_run"])

    def test_mark_ran_recurring_rolls_forward(self):
        from jobs import create_job, mark_ran

        job = create_job(
            session_id="s1", title="t", payload="p",
            schedule={"type": "interval", "interval_seconds": 600}, now=1000,
        )
        rolled = mark_ran(job["id"], result="done", now=1600)
        self.assertEqual(1600, rolled["last_run"])
        self.assertEqual("done", rolled["last_result"])
        self.assertEqual(2200, rolled["next_run"])
        self.assertTrue(rolled["enabled"])

    def test_once_fires_once_then_disables(self):
        from jobs import create_job, due_jobs, mark_ran, get_job

        target = _ts(2026, 6, 20, 9, 0)
        job = create_job(
            session_id="s1", title="t", payload="p",
            schedule={"type": "once", "date": "2026-06-20", "time": "09:00"},
            now=_ts(2026, 6, 15, 0, 0),
        )
        self.assertEqual(target, job["next_run"])
        self.assertEqual(1, len(due_jobs(now=target)))

        after = mark_ran(job["id"], now=target)
        self.assertIsNone(after["next_run"])
        self.assertFalse(after["enabled"])
        self.assertEqual([], due_jobs(now=target + 10000))
        self.assertFalse(get_job(job["id"])["enabled"])

    def test_once_overdue_at_create_is_due(self):
        from jobs import create_job, due_jobs

        # Target in the past relative to now -> seeded next_run is the past target,
        # so it is immediately due (overdue catch-up).
        create_job(
            session_id="s1", title="t", payload="p",
            schedule={"type": "once", "date": "2020-01-01", "time": "09:00"},
            now=_ts(2026, 6, 15, 0, 0),
        )
        self.assertEqual(1, len(due_jobs(now=_ts(2026, 6, 15, 0, 0))))

    def test_reconcile_skips_missed_recurring(self):
        from jobs import create_job, reconcile_on_start, get_job

        job = create_job(
            session_id="s1", title="t", payload="p",
            schedule={"type": "interval", "interval_seconds": 600}, now=1000,
        )  # next_run = 1600
        # Backend was "down"; now it's much later. Recurring should jump forward.
        reconcile_on_start(now=100000)
        self.assertEqual(100000 + 600, get_job(job["id"])["next_run"])

    def test_reconcile_leaves_overdue_once(self):
        from jobs import create_job, reconcile_on_start, get_job

        job = create_job(
            session_id="s1", title="t", payload="p",
            schedule={"type": "once", "date": "2020-01-01", "time": "09:00"},
            now=_ts(2026, 6, 15, 0, 0),
        )
        seeded = job["next_run"]
        reconcile_on_start(now=_ts(2026, 6, 15, 0, 0))
        # once is left untouched so it still fires on the next tick.
        self.assertEqual(seeded, get_job(job["id"])["next_run"])


class CommandGrammarTests(unittest.TestCase):
    def test_list_and_empty(self):
        from jobs import parse_job_command

        self.assertEqual("list", parse_job_command("")["action"])
        self.assertEqual("list", parse_job_command("list")["action"])

    def test_management(self):
        from jobs import parse_job_command

        self.assertEqual({"action": "pause", "id": "abc"}, parse_job_command("pause abc"))
        self.assertEqual({"action": "resume", "id": "abc"}, parse_job_command("resume abc"))
        self.assertEqual({"action": "delete", "id": "abc"}, parse_job_command("delete abc"))
        self.assertEqual("error", parse_job_command("pause")["action"])

    def test_every(self):
        from jobs import parse_job_command

        spec = parse_job_command("every 10m drick vatten")
        self.assertEqual("create", spec["action"])
        self.assertEqual({"type": "interval", "interval_seconds": 600}, spec["schedule"])
        self.assertEqual("drick vatten", spec["payload"])
        self.assertEqual("error", parse_job_command("every 10x foo")["action"])
        self.assertEqual("error", parse_job_command("every 10m")["action"])

    def test_daily(self):
        from jobs import parse_job_command

        spec = parse_job_command("daily 09:00 ta en paus")
        self.assertEqual({"type": "daily", "time": "09:00"}, spec["schedule"])
        self.assertEqual("error", parse_job_command("daily 99:00 x")["action"])

    def test_weekly_list_and_range(self):
        from jobs import parse_job_command

        spec = parse_job_command("mon,fri 08:00 standup")
        self.assertEqual("weekly", spec["schedule"]["type"])
        self.assertEqual([0, 4], spec["schedule"]["weekdays"])
        self.assertEqual([0, 1, 2, 3, 4], parse_job_command("mon-fri 08:00 x")["schedule"]["weekdays"])
        self.assertEqual("error", parse_job_command("xyz 08:00 x")["action"])

    def test_once(self):
        from jobs import parse_job_command

        spec = parse_job_command("once 2026-06-20 09:00 ring tandläkaren")
        self.assertEqual({"type": "once", "date": "2026-06-20", "time": "09:00"}, spec["schedule"])
        self.assertEqual("ring tandläkaren", spec["payload"])
        self.assertEqual("error", parse_job_command("once 2026-13-40 09:00 x")["action"])


class DescribeTests(unittest.TestCase):
    def test_describe_each_type(self):
        from jobs import describe_schedule

        self.assertEqual("var 10 min", describe_schedule({"type": "interval", "interval_seconds": 600}))
        self.assertEqual("var 2 h", describe_schedule({"type": "interval", "interval_seconds": 7200}))
        self.assertEqual("dagligen kl 09:00", describe_schedule({"type": "daily", "time": "09:00"}))
        self.assertEqual("mån, fre kl 08:00", describe_schedule({"type": "weekly", "time": "08:00", "weekdays": [0, 4]}))
        self.assertEqual("en gång 2026-06-20 kl 09:00", describe_schedule({"type": "once", "date": "2026-06-20", "time": "09:00"}))

    def test_reminder_content(self):
        from jobs import reminder_content

        self.assertEqual("⏰ drick vatten", reminder_content({"payload": "drick vatten"}))
        self.assertEqual("⏰ Möte", reminder_content({"payload": "", "title": "Möte"}))


if __name__ == "__main__":
    unittest.main()
