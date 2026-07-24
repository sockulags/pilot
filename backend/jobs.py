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
     permissions: <profile name>,  # capability bound for task-kind jobs
     enabled, next_run (epoch|None), created_ts, last_run|None, last_result|None,
     last_audit|None}  # structured record of the last fired run (tool calls etc.)

``next_run`` is the single source of truth for *when* — recomputed on fire and
reconciled on startup, so jobs survive a backend restart. Times for
daily/weekly/once are interpreted in the server's local timezone.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
import uuid
from datetime import datetime, timedelta

from config import JOBS_FILE
from job_permissions import DEFAULT_PROFILE_BY_KIND, is_valid_profile, normalize_profile

logger = logging.getLogger(__name__)

VALID_KINDS = {"reminder", "task"}
VALID_SCHEDULE_TYPES = {"interval", "daily", "weekly", "once"}
_RECURRING = {"interval", "daily", "weekly"}


# --- schedule math ----------------------------------------------------------

def _parse_hhmm(time_str) -> tuple[int, int]:
    """Parse 'HH:MM' into validated (hour, minute). Raises ValueError on anything
    out of range or of the wrong shape (e.g. 730, '24:00', '07:65')."""
    hh, mm = str(time_str or "00:00").split(":")
    hour, minute = int(hh), int(mm)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"time out of range: {time_str!r}")
    return hour, minute


def valid_schedule(schedule) -> bool:
    """Whether a client-supplied schedule dict is well-formed enough to compute.

    The WS add_job surface accepts a raw client dict; this is the boundary guard
    so a malformed schedule is rejected up front instead of crashing
    compute_next_run mid-tick (review 2026-07-04)."""
    if not isinstance(schedule, dict):
        return False
    stype = schedule.get("type")
    if stype not in VALID_SCHEDULE_TYPES:
        return False
    try:
        if stype == "interval":
            return int(schedule.get("interval_seconds") or 0) > 0
        if stype == "once":
            return _once_target(schedule) is not None
        # daily / weekly
        _parse_hhmm(schedule.get("time", "00:00"))
        if stype == "weekly":
            weekdays = schedule.get("weekdays")
            if not isinstance(weekdays, (list, tuple)) or not weekdays:
                return False
            if not all(isinstance(d, int) and 0 <= d <= 6 for d in weekdays):
                return False
        return True
    except (ValueError, TypeError):
        return False


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
    target is already at/behind ``after_ts``. Never raises: a malformed schedule
    (wrong types, out-of-range time, string weekdays) yields None rather than
    crashing the scheduler tick (review 2026-07-04).
    """
    if not isinstance(schedule, dict):
        return None
    try:
        stype = schedule.get("type")
        if stype == "interval":
            secs = int(schedule.get("interval_seconds") or 0)
            return after_ts + secs if secs > 0 else None

        if stype == "once":
            target = _once_target(schedule)
            return target if target is not None and target > after_ts else None

        if stype in ("daily", "weekly"):
            hh, mm = _parse_hhmm(schedule.get("time", "00:00"))
            weekdays = schedule.get("weekdays") if stype == "weekly" else None
            if stype == "weekly":
                if not isinstance(weekdays, (list, tuple)) or not weekdays:
                    return None
                if not all(isinstance(d, int) for d in weekdays):
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
    except (ValueError, TypeError) as exc:
        logger.warning("invalid schedule %r: %s", schedule, exc)
        return None

    return None


# --- persistence ------------------------------------------------------------

def _load() -> dict:
    try:
        with open(JOBS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data.get("jobs"), list):
            for job in data["jobs"]:
                # Back-compat: jobs written before permission profiles existed
                # default to their kind's safe profile (reminder-only / read-only).
                if "permissions" not in job:
                    job["permissions"] = DEFAULT_PROFILE_BY_KIND.get(
                        job.get("kind"), "read-only"
                    )
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
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, JOBS_FILE)
        except Exception:
            # Don't leak the temp file when the write fails.
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
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
    permissions: str | None = None,
    now: float | None = None,
) -> dict:
    """Persist a new job and return it (with its computed first ``next_run``).

    ``permissions`` names the capability profile a task-kind job's background
    coordinator run is bound to (see job_permissions.JOB_PERMISSION_PROFILES).
    It defaults to ``reminder-only`` for reminders and the safe ``read-only`` for
    tasks; an invalid name falls back to that kind's default.
    """
    now = time.time() if now is None else now
    kind = kind if kind in VALID_KINDS else "reminder"
    permissions = normalize_profile(permissions, kind)
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
        "kind": kind,
        "payload": payload or "",
        "schedule": schedule,
        "permissions": permissions,
        "enabled": True,
        "next_run": next_run,
        "created_ts": now,
        "last_run": None,
        "last_result": None,
        "last_audit": None,
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


def record_audit(job_id: str, audit: dict, now: float | None = None) -> dict | None:
    """Persist the structured audit record of a fired task run on the job.

    Stored under ``last_audit`` in jobs.json (mirroring last_run/last_result).
    ``audit`` is the dict produced by build_audit_record() in scheduler.py:
    {status, output, tool_calls: [...], errors: [...], permissions, timed_out}.
    """
    now = time.time() if now is None else now
    data = _load()
    for job in data["jobs"]:
        if job["id"] != job_id:
            continue
        job["last_audit"] = {"ts": now, **audit}
        _save(data)
        return job
    return None


# --- display + command grammar (used by the WS layer) -----------------------

_WD_NAMES = ["mån", "tis", "ons", "tor", "fre", "lör", "sön"]
_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _human_duration(secs: int) -> str:
    if secs % 86400 == 0:
        return f"{secs // 86400} dygn"
    if secs % 3600 == 0:
        return f"{secs // 3600} h"
    if secs % 60 == 0:
        return f"{secs // 60} min"
    return f"{secs} s"


def describe_schedule(schedule: dict) -> str:
    """Human-readable (Swedish) one-liner for a schedule. Used in replies + UI."""
    stype = schedule.get("type")
    if stype == "interval":
        return f"var {_human_duration(int(schedule.get('interval_seconds') or 0))}"
    if stype == "daily":
        return f"dagligen kl {schedule.get('time')}"
    if stype == "weekly":
        days = ", ".join(_WD_NAMES[d] for d in schedule.get("weekdays", []) if 0 <= d < 7)
        return f"{days} kl {schedule.get('time')}"
    if stype == "once":
        return f"en gång {schedule.get('date')} kl {schedule.get('time')}"
    return "okänt schema"


def reminder_content(job: dict) -> str:
    """The assistant-message text a fired reminder delivers (live or offline)."""
    payload = (job.get("payload") or "").strip()
    return f"⏰ {payload or job.get('title') or 'Påminnelse'}"


def _parse_duration(token: str) -> int | None:
    m = re.fullmatch(r"(\d+)([smhd])", token.strip().lower())
    if not m:
        return None
    return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]


def _valid_hhmm(t: str) -> bool:
    return bool(re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", t.strip()))


def _valid_date(d: str) -> bool:
    try:
        datetime.strptime(d.strip(), "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _parse_weekdays(token: str) -> list[int] | None:
    """Parse 'mon,fri' or 'mon-fri' into sorted weekday ints (mon=0). None if any
    token is not a weekday name."""
    days: set[int] = set()
    for part in token.strip().lower().split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            if a not in _WEEKDAYS or b not in _WEEKDAYS:
                return None
            ia, ib = _WEEKDAYS[a], _WEEKDAYS[b]
            span = range(ia, ib + 1) if ia <= ib else [*range(ia, 7), *range(0, ib + 1)]
            days.update(span)
        elif part in _WEEKDAYS:
            days.add(_WEEKDAYS[part])
        else:
            return None
    return sorted(days) if days else None


def parse_job_command(arg: str) -> dict:
    """Parse the `/job` argument into an intent dict.

    Returns one of: {"action": "list"} | {"action": "pause"|"resume"|"delete",
    "id": str} | {"action": "create", "title", "payload", "schedule"} |
    {"action": "error", "message": str}.
    """
    arg = (arg or "").strip()
    if not arg or arg.lower() == "list":
        return {"action": "list"}

    parts = arg.split(None, 1)
    head = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    if head in ("pause", "resume", "delete"):
        if not rest:
            return {"action": "error", "message": f"Ange jobb-id: /job {head} <id>"}
        return {"action": head, "id": rest.split()[0]}

    def _create(payload: str, schedule: dict) -> dict:
        payload = payload.strip()
        # "task: <instruction>" runs the coordinator on a schedule; otherwise the
        # text is delivered verbatim as a reminder. An optional leading
        # "[<profile>]" after "task:" opts a task into a wider permission profile
        # (e.g. "task:[shell] ...") — see job_permissions.JOB_PERMISSION_PROFILES.
        kind = "reminder"
        permissions: str | None = None
        if payload.lower().startswith("task:"):
            kind = "task"
            payload = payload[len("task:"):].strip()
            m = re.match(r"\[([a-z\-]+)\]\s*", payload)
            if m:
                requested = m.group(1)
                if not is_valid_profile(requested):
                    return {"action": "error",
                            "message": f"Okänd behörighetsprofil {requested!r}."}
                permissions = requested
                payload = payload[m.end():].strip()
        if not payload:
            return {"action": "error", "message": "Ange en text för jobbet."}
        return {"action": "create", "kind": kind, "permissions": permissions,
                "title": payload[:60], "payload": payload, "schedule": schedule}

    if head == "every":
        d = rest.split(None, 1)
        secs = _parse_duration(d[0]) if d else None
        if not secs or len(d) < 2:
            return {"action": "error", "message": "Format: /job every <30s|10m|2h|1d> <text>"}
        return _create(d[1], {"type": "interval", "interval_seconds": secs})

    if head == "daily":
        d = rest.split(None, 1)
        if len(d) < 2 or not _valid_hhmm(d[0]):
            return {"action": "error", "message": "Format: /job daily <HH:MM> <text>"}
        return _create(d[1], {"type": "daily", "time": d[0]})

    if head == "once":
        d = rest.split(None, 2)
        if len(d) < 3 or not _valid_date(d[0]) or not _valid_hhmm(d[1]):
            return {"action": "error", "message": "Format: /job once <YYYY-MM-DD> <HH:MM> <text>"}
        return _create(d[2], {"type": "once", "date": d[0], "time": d[1]})

    weekdays = _parse_weekdays(head)
    if weekdays is not None:
        d = rest.split(None, 1)
        if len(d) < 2 or not _valid_hhmm(d[0]):
            return {"action": "error", "message": "Format: /job mon,fri <HH:MM> <text>"}
        return _create(d[1], {"type": "weekly", "time": d[0], "weekdays": weekdays})

    return {"action": "error", "message": f"Okänt jobbkommando {head!r}. Skriv /job för hjälp."}


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
