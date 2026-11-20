"""
Wall-clock helpers for the alarm engine.

local_now() asks Windows for the local time directly (GetLocalTime). Python's datetime.now() goes
through the C runtime, which honours the TZ environment variable; the C runtime can't read IANA
names such as "America/Los_Angeles", so a process started with one of those silently runs on UTC.
An alarm clock can't depend on how its process was launched.

due_day() decides whether an alarm should ring now. It matches the alarm's minute as before, and
also catches an alarm whose time passed while the scheduler wasn't ticking (sleep, a stalled
loop, a slow disk), as long as it's at most CATCH_UP late.
"""
import datetime
import os

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
CATCH_UP = datetime.timedelta(minutes=30)

if os.name == "nt":
    import ctypes

    class _SYSTEMTIME(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ushort) for n in
                    ("wYear", "wMonth", "wDayOfWeek", "wDay", "wHour", "wMinute", "wSecond", "wMilliseconds")]

    def local_now() -> datetime.datetime:
        st = _SYSTEMTIME()
        ctypes.windll.kernel32.GetLocalTime(ctypes.byref(st))
        return datetime.datetime(st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond,
                                 st.wMilliseconds * 1000)
else:
    def local_now() -> datetime.datetime:
        return datetime.datetime.now()


def snoozed_until(alarm: dict):
    """The local time a snoozed alarm rings again, or None."""
    try:
        return datetime.datetime.fromisoformat(alarm["snoozed_until"]) if alarm.get("snoozed_until") else None
    except (TypeError, ValueError):
        return None


def next_ring(alarm: dict, now: datetime.datetime):
    """When an alarm rings next (local time), or None. Used to wake the PC from sleep in time.

    A snooze counts even after a one-time alarm turned itself off, so a sleeping PC still wakes for it."""
    snooze = snoozed_until(alarm)
    regular = _next_regular(alarm, now)
    if snooze and snooze > now:
        return min(snooze, regular) if regular else snooze
    return regular


def _next_regular(alarm: dict, now: datetime.datetime):
    if not alarm.get("enabled"):
        return None
    try:
        hh, mm = (int(x) for x in str(alarm["time_hhmm"]).split(":"))
        alarm_time = datetime.time(hh, mm)
    except (KeyError, ValueError):
        return None
    weekdays = alarm.get("weekdays") or []
    target = alarm.get("target_date")
    if target:
        try:
            day = datetime.date.fromisoformat(target)
        except (TypeError, ValueError):
            return None
        at = datetime.datetime.combine(day, alarm_time)
        if weekdays and WEEKDAYS[day.weekday()] not in weekdays:
            return None
        if at > now and alarm.get("last_triggered_date") != day.isoformat():
            return at
        return None
    for i in range(8):
        try:
            day = now.date() + datetime.timedelta(days=i)
        except OverflowError:
            return None
        at = datetime.datetime.combine(day, alarm_time)
        if at <= now or alarm.get("last_triggered_date") == day.isoformat():
            continue
        if not weekdays or WEEKDAYS[day.weekday()] in weekdays:
            return at
    return None


def due_day(alarm: dict, prev: datetime.datetime | None, now: datetime.datetime):
    """Return the ISO date the alarm is ringing for, or None.

    prev is the previous scheduler tick. An alarm is due when its time falls in (prev, now], or when
    now is inside its minute (so an alarm set for the current minute still rings), and it is at most
    CATCH_UP late. None means startup: recover any unhandled occurrence within CATCH_UP,
    including its exact boundary. Yesterday is checked too, so 23:55 still rings after waking at 00:10.
    """
    try:
        hh, mm = (int(x) for x in str(alarm["time_hhmm"]).split(":"))
    except (KeyError, ValueError):
        return None
    weekdays = alarm.get("weekdays") or []
    for day in (now.date(), now.date() - datetime.timedelta(days=1)):
        at = datetime.datetime.combine(day, datetime.time(hh, mm))
        in_minute = at <= now < at + datetime.timedelta(minutes=1)
        passed = at <= now and (prev is None or prev < at)
        if not (in_minute or passed) or now - at > CATCH_UP:
            continue
        iso = day.isoformat()
        if alarm.get("last_triggered_date") == iso:
            continue
        if alarm.get("target_date") and alarm["target_date"] != iso:
            continue
        if weekdays and WEEKDAYS[day.weekday()] not in weekdays:
            continue
        return iso
    return None


def missed_occurrence(alarm: dict, since: datetime.datetime, until: datetime.datetime):
    """The latest occurrence of an enabled alarm in (since, until] that never rang, as (iso_day, at), or None.

    The scheduler calls this for the stretch it could not cover (PC off or asleep longer than CATCH_UP), so the
    history and the page can say an alarm was missed instead of skipping it silently. At most 8 days are checked."""
    if not alarm.get("enabled") or until <= since:
        return None
    try:
        hh, mm = (int(x) for x in str(alarm["time_hhmm"]).split(":"))
    except (KeyError, ValueError):
        return None
    weekdays = alarm.get("weekdays") or []
    target = alarm.get("target_date")
    start = max(since.date(), until.date() - datetime.timedelta(days=7))
    day = until.date()
    while day >= start:
        at = datetime.datetime.combine(day, datetime.time(hh, mm))
        iso = day.isoformat()
        if (since < at <= until and alarm.get("last_triggered_date") != iso and (not target or target == iso)
                and (not weekdays or WEEKDAYS[day.weekday()] in weekdays)):
            return iso, at
        day -= datetime.timedelta(days=1)
    return None


def roll_forward(hhmm: str, now: datetime.datetime) -> str:
    """The next date a one-time alarm at hhmm can ring: today if that minute is still ahead, else tomorrow."""
    hh, mm = (int(x) for x in hhmm.split(":"))
    at = datetime.datetime.combine(now.date(), datetime.time(hh, mm))
    return (now.date() if at > now else now.date() + datetime.timedelta(days=1)).isoformat()
