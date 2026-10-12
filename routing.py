"""Decide which cohort and LMS class session a Zoho recording belongs to.

A technical mentor can run full-time and part-time cohorts at once, so the
host alone is not enough. Recordings are matched, in order of confidence, by:

1. the Zoho meeting key in the session's meeting link, at the session's time;
2. the session's instructor (Zoho host e-mail), at the session's time;
3. the instructor's cohort whose slot (full-time 09:00-14:00 on weekdays,
   part-time evenings, Africa/Nairobi) contains the recording, on that day.

Anything else is left for an admin to assign on the dashboard.
"""
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import config

FULL_TIME, PART_TIME = "full-time", "part-time"
_KEY_PARAMS = ("key", "meetingKey", "meetingkey", "meeting_key", "sessionId", "sessionid")


@dataclass(frozen=True)
class Session:
    session_id: int
    cohort_id: int
    cohort_name: str
    cohort_type: str
    title: str
    start: datetime  # timezone-aware UTC
    end: datetime
    meeting_key: str | None
    instructor_email: str


@dataclass(frozen=True)
class Match:
    cohort_id: int
    cohort_name: str
    session_id: int | None
    session_title: str
    reason: str


# ---- parsing ---------------------------------------------------------------

def meeting_key_from_link(link):
    """Zoho meeting key from a join/register link, e.g. ...?key=1234567890."""
    if not link:
        return None
    parsed = urlparse(link.strip())
    query = parse_qs(parsed.query)
    for name in _KEY_PARAMS:
        for value in query.get(name, []):
            digits = re.sub(r"\D", "", value)
            if digits:
                return digits
    found = re.findall(r"\d{6,}", parsed.path)
    return found[-1] if found else None


def recording_meeting_key(rec):
    for field in ("meetingKey", "meetingkey", "meeting_key", "key"):
        digits = re.sub(r"\D", "", str(rec.get(field) or ""))
        if digits:
            return digits
    return None


def recording_start(rec):
    ms = rec.get("startTimeInMs") or rec.get("startTimeMillis")
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def normalise_cohort_type(value):
    value = str(value or "").strip().lower().replace("_", "-").replace(" ", "-")
    return {"fulltime": FULL_TIME, "parttime": PART_TIME}.get(value, value or FULL_TIME)


def _session_start(row):
    date, start = row.get("session_date"), row.get("start_time") or row.get("session_start_time")
    if not (date and start):
        return None
    start = str(start)[:8]
    fmt = "%Y-%m-%d %H:%M:%S" if start.count(":") == 2 else "%Y-%m-%d %H:%M"
    try:
        return datetime.strptime(f"{str(date)[:10]} {start}", fmt).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _hours(spec):
    start, end = (time.fromisoformat(part.strip()) for part in spec.split("-", 1))
    return start, end


def slot_for(moment):
    """'full-time' / 'part-time' / None for an aware datetime, in the school's timezone."""
    local = moment.astimezone(ZoneInfo(config.TIMEZONE))
    t = local.time()
    start, end = _hours(config.FULL_TIME_HOURS)
    if local.weekday() < 5 and start <= t < end:
        return FULL_TIME
    start, end = _hours(config.PART_TIME_HOURS)
    if start <= t < end:
        return PART_TIME
    return None


def local_date(moment):
    return moment.astimezone(ZoneInfo(config.TIMEZONE)).date()


# ---- catalog -----------------------------------------------------------------

class Catalog:
    """Snapshot of active cohorts' recent sessions, fetched once per cycle."""

    def __init__(self, sessions, cohorts=None):
        self.sessions = list(sessions)
        self.cohorts = cohorts or {s.cohort_id: (s.cohort_name, s.cohort_type) for s in self.sessions}

    @classmethod
    def from_lms(cls, lms):
        sessions, cohorts = [], {}
        for cohort in lms.active_cohorts():
            cid, name = cohort["id"], cohort.get("name") or f"Cohort {cohort['id']}"
            ctype = normalise_cohort_type(cohort.get("type"))
            cohorts[cid] = (name, ctype)
            for row in lms.past_sessions(cid):
                if row.get("cancelled_at") or str(row.get("status", "")).lower() == "cancelled":
                    continue
                start = _session_start(row)
                if not start or not row.get("session_id"):
                    continue
                instructor = row.get("instructor_id")
                sessions.append(Session(
                    session_id=int(row["session_id"]), cohort_id=cid, cohort_name=name,
                    cohort_type=ctype, title=row.get("title") or "",
                    start=start, end=start + timedelta(minutes=int(row.get("duration_minutes") or 60)),
                    meeting_key=meeting_key_from_link(row.get("meeting_link")),
                    instructor_email=lms.user_email(instructor) if instructor else "",
                ))
        return cls(sessions, cohorts)

    def resolve(self, rec, host_keys):
        start = recording_start(rec)
        if not start:
            return None
        hosts = set(host_keys or [])
        tolerance = timedelta(minutes=config.MATCH_TOLERANCE_MINUTES)

        def during(s):
            return s.start - tolerance <= start <= s.end + tolerance

        def nearest(candidates, reason):
            if not candidates:
                return None
            s = min(candidates, key=lambda c: abs((c.start - start).total_seconds()))
            return Match(s.cohort_id, s.cohort_name, s.session_id, s.title, reason)

        key = recording_meeting_key(rec)
        if key:
            found = nearest([s for s in self.sessions if s.meeting_key == key and during(s)],
                            "meeting link")
            if found:
                return found

        found = nearest([s for s in self.sessions if s.instructor_email in hosts and during(s)],
                        "mentor and session time")
        if found:
            return found

        slot = slot_for(start)
        if slot:
            day = local_date(start)
            return nearest([s for s in self.sessions
                            if s.instructor_email in hosts and s.cohort_type == slot
                            and local_date(s.start) == day],
                           f"mentor's {slot} cohort")
        return None
