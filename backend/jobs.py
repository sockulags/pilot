"""Scheduled jobs — recurring reminders and (later) background tasks.

A flat JSON store of job definitions plus the schedule math that decides when each
one next fires. The scheduler loop (started in main.py's lifespan) ticks every
JOBS_TICK_SECONDS, asks ``due_jobs(now)`` what is owed, delivers it, and calls
``mark_ran`` to roll the schedule forward.

Persistence mirrors store.py / memory.py: one JSON file, atomic write (temp file
in the same dir, then os.replace). The file lives under backend/data/ which is
gitignored.

Shape on disk: {"jobs": [job, ...]} where a job is::

    {id, session_id, title, kind: "reminder"|"task", payload,
     schedule: {type: "interval"|"daily"|"weekly"|"once",
                interval_seconds?, time? "HH:MM", weekdays? [0..6 mon=0],
                date? "YYYY-MM-DD"},
     enabled, next_run (epoch|None), created_ts, last_run|None, last_result|None}

``next_run`` is the single source of truth for *when* — recomputed on fire and
reconciled on startup, so jobs survive a backend restart. Times for
daily/weekly/once are interpreted in the server's local timezone.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import uuid
from datetime import datetime, timedelta

from config import JOBS_FILE

logger = logging.getLogger(__name__)

VALID_KINDS = {"reminder", "task"}
VALID_SCHEDULE_TYPES = {"interval", "daily", "weekly", "once"}
_RECURRING = {"interval", "daily", "weekly"}


# --- schedule math ----------------------------------------------------------

def _parse_hhmm(time_str: str) -> tuple[int, int]:
    hh, mm = (time_str or "00:00").split(":")
    return int(hh), int(mm)


def _once_target(schedule: dict) -> float | None:
    """Absolute epoch a `once` job points at, or None if malformed."""
    date_str = schedule.get("date")
    if not date_str:
        return None
    try:
        y, mo, d = (int(p) for p in date_str.split("-"))
        hh, mm = _parse_hhmm(schedule.get("time", "00:00"))
        return datetime(y, mo, d, hh, mm).timestamp()
    except (ValueError, TypeError):
        return None


def compute_next_run(schedule: dict, after_ts: float) -> float | None:
    """Next epoch strictly after ``after_ts`` the schedule fires, or None.

    None means "never again" — an interval with no period, or a `once` whose
    target is already at/behind ``after_ts``.
    """
    stype = schedule.get("type")
    if stype == "interval":
        secs = int(schedule.get("interval_seconds") or 0)
        return after_ts + secs if secs > 0 else None

    if stype == "once":
        target = _once_target(schedule)
        return target if target is not None and target > after_ts else None

    if stype in ("daily", "weekly"):
        try:
            hh, mm = _parse_hhmm(schedule.get("time", "00:00"))
        except ValueError:
            return None
        weekdays = schedule.get("weekdays") if stype == "weekly" else None
        if stype == "weekly" and not weekdays:
            return None
        base = datetime.fromtimestamp(after_ts)
        # Scan today..+7 days for the first HH:MM slot strictly after after_ts
        # that also matches an allowed weekday (daily = every day).
        for delta in range(0, 8):
            cand = (base + timedelta(days=delta)).replace(
                hour=hh, minute=mm, second=0, microsecond=0
            )
            if cand.timestamp() <= after_ts:
                continue
            if weekdays is not None and cand.weekday() not in weekdays:
                continue
            return cand.timestamp()
        return None

    return None


# --- persistence ------------------------------------------------------------

def _load() -> dict:
    try:
        with open(JOBS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data.get("jobs"), list):
            return data
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("could not load jobs store: %s", exc)
    return {"jobs": []}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(JOBS_FILE), exist_ok=True)
    try:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(JOBS_FILE), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, JOBS_FILE)
    except Exception as exc:
        logger.warning("could not save jobs store: %s", exc)


# --- CRUD -------------------------------------------------------------------

def create_job(
    *,
    session_id: str | None,
    title: str,
    payload: str,
    schedule: dict,
    kind: str = "reminder",
    now: float | None = None,
) -> dict:
    """Persist a new job and return it (with its computed first ``next_run``)."""
    now = time.time() if now is None else now
    if schedule.get("type") == "once":
        # A `once` target in the past still fires once (overdue catch-up), so we
        # seed next_run with the raw target rather than compute_next_run (which
        # would reject a past time). It self-disables after firing.
        next_run = _once_target(schedule)
    else:
        next_run = compute_next_run(schedule, now)

    job = {
        "id": uuid.uuid4().hex[:12],
        "session_id": session_id,
        "title": (title or "").strip() or "Jobb",
        "kind": kind if kind in VALID_KINDS else "reminder",
        "payload": payload or "",
        "schedule": schedule,
        "enabled": True,
        "next_run": next_run,
        "created_ts": now,
        "last_run": None,
        "last_result": None,
    }
    data = _load()
    data["jobs"].append(job)
    _save(data)
    return job


def list_jobs(session_id: str | None = None) -> list[dict]:
    """Jobs (newest first); scoped to ``session_id`` when given."""
    jobs = _load()["jobs"]
    if session_id is not None:
        jobs = [j for j in jobs if j.get("session_id") == session_id]
    return sorted(jobs, key=lambda j: j.get("created_ts", 0), reverse=True)


def get_job(job_id: str) -> dict | None:
    for job in _load()["jobs"]:
        if job["id"] == job_id:
            return job
    return None


def update_job(job_id: str, **fields) -> dict | None:
    data = _load()
    for job in data["jobs"]:
        if job["id"] == job_id:
            job.update(fields)
            _save(data)
            return job
    return None


def delete_job(job_id: str) -> bool:
    data = _load()
    before = len(data["jobs"])
    data["jobs"] = [j for j in data["jobs"] if j["id"] != job_id]
    if len(data["jobs"]) != before:
        _save(data)
        return True
    return False


def set_enabled(job_id: str, enabled: bool, now: float | None = None) -> dict | None:
    """Pause/resume a job. Resuming a recurring job whose slot lapsed while paused
    re-anchors ``next_run`` forward so it doesn't immediately storm."""
    now = time.time() if now is None else now
    data = _load()
    for job in data["jobs"]:
        if job["id"] != job_id:
            continue
        job["enabled"] = enabled
        if enabled and job["schedule"].get("type") in _RECURRING:
            nxt = job.get("next_run")
            if nxt is None or nxt <= now:
                job["next_run"] = compute_next_run(job["schedule"], now)
        _save(data)
        return job
    return None


# --- scheduler helpers ------------------------------------------------------

def due_jobs(now: float | None = None) -> list[dict]:
    """Enabled jobs whose next_run is at/behind ``now``."""
    now = time.time() if now is None else now
    return [
        j for j in _load()["jobs"]
        if j.get("enabled")
        and j.get("next_run") is not None
        and j["next_run"] <= now
    ]


def mark_ran(job_id: str, result: str | None = None, now: float | None = None) -> dict | None:
    """Record a fire and roll the schedule forward. A schedule with no future
    occurrence (`once`, or an exhausted/invalid recurring) is disabled."""
    now = time.time() if now is None else now
    data = _load()
    for job in data["jobs"]:
        if job["id"] != job_id:
            continue
        job["last_run"] = now
        job["last_result"] = result
        nxt = compute_next_run(job["schedule"], now)
        job["next_run"] = nxt
        if nxt is None:
            job["enabled"] = False
        _save(data)
        return job
    return None


def reconcile_on_start(now: float | None = None) -> None:
    """Re-anchor recurring jobs that fell behind while the backend was down.

    Skips missed recurring fires (avoids a thundering herd on restart) by moving
    their next_run forward to the next slot after ``now``. Overdue `once` jobs are
    left as-is so they still fire exactly once on the next tick.
    """
    now = time.time() if now is None else now
    data = _load()
    changed = False
    for job in data["jobs"]:
        if not job.get("enabled"):
            continue
        if job["schedule"].get("type") not in _RECURRING:
            continue
        nxt = job.get("next_run")
        if nxt is None or nxt <= now:
            job["next_run"] = compute_next_run(job["schedule"], now)
            changed = True
    if changed:
        _save(data)
