"""When scheduled tasks run.

A repeat rule is a short, readable string, always in the person's own time zone:

    daily 09:00            every day at 09:00
    weekdays 08:30         Monday to Friday
    weekly mon,thu 17:00   on the listed days
    monthly 1 10:00        on that day of the month (skips months that don't have it)
    every 30m / every 2h   at a fixed interval (at least 5 minutes)
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_TIME = r"([01]?\d|2[0-3]):([0-5]\d)"
_RULES = {
    "daily": re.compile(rf"^daily {_TIME}$"),
    "weekdays": re.compile(rf"^weekdays {_TIME}$"),
    "weekly": re.compile(rf"^weekly ((?:mon|tue|wed|thu|fri|sat|sun)(?:,(?:mon|tue|wed|thu|fri|sat|sun))*) {_TIME}$"),
    "monthly": re.compile(rf"^monthly ([1-9]|[12]\d|3[01]) {_TIME}$"),
    "every": re.compile(r"^every (\d{1,4})(m|h)$"),
}


class ScheduleError(ValueError):
    pass


def zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def normalize_repeat(spec: str) -> str:
    s = " ".join(spec.strip().lower().split())
    for rx in _RULES.values():
        if rx.match(s):
            m = _RULES["every"].match(s)
            if m and int(m.group(1)) * (60 if m.group(2) == "h" else 1) < 5:
                raise ScheduleError("Repeat intervals must be at least 5 minutes.")
            return s
    raise ScheduleError(
        f"Unknown repeat rule {spec!r}. Use one of: 'daily 09:00', 'weekdays 08:30', "
        "'weekly mon,thu 17:00', 'monthly 1 10:00', 'every 30m', 'every 2h'.")


def parse_when(value: str, tz_name: str | None) -> datetime:
    """An ISO date-time; without an offset it is read in the person's time zone. Returns UTC."""
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScheduleError(f"Couldn't read the time {value!r}; use ISO format like 2026-10-12T09:00.") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=zone(tz_name))
    return dt.astimezone(timezone.utc)


def next_run(spec: str, tz_name: str | None, after: datetime) -> datetime:
    """The first time strictly after `after` (UTC) that the rule fires. Returns UTC."""
    tz = zone(tz_name)
    m = _RULES["every"].match(spec)
    if m:
        step = timedelta(minutes=int(m.group(1)) * (60 if m.group(2) == "h" else 1))
        return after + step
    local = after.astimezone(tz)
    for kind, rx in _RULES.items():
        m = rx.match(spec)
        if not m:
            continue
        hh, mm = int(m.groups()[-2]), int(m.groups()[-1])
        for add in range(0, 400):  # enough to find the next valid day for any rule
            day = (local + timedelta(days=add)).date()
            if kind == "weekdays" and day.weekday() > 4:
                continue
            if kind == "weekly" and DAYS[day.weekday()] not in m.group(1).split(","):
                continue
            if kind == "monthly" and day.day != int(m.group(1)):
                continue
            cand = datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)
            if cand > local:
                return cand.astimezone(timezone.utc)
    raise ScheduleError(f"Can't compute the next run for {spec!r}.")


def describe(spec: str | None, next_at: datetime | None, tz_name: str | None) -> str:
    when = next_at.astimezone(zone(tz_name)).strftime("%a %d %b %Y, %H:%M") if next_at else "never"
    return f"{spec} (next: {when})" if spec else f"once at {when}"
