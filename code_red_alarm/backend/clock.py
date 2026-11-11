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


def next_ring(alarm: dict, now: datetime.datetime):
    """When an enabled alarm rings next (local time), or None. Used to wake the PC from sleep in time."""
    if not alarm.get("enabled"):
        return None
    try:
        hh, mm = (int(x) for x in str(alarm["time_hhmm"]).split(":"))
    except (KeyError, ValueError):
        return None
    weekdays = alarm.get("weekdays") or []
    for i in range(8):
        day = now.date() + datetime.timedelta(days=i)
        at = datetime.datetime.combine(day, datetime.time(hh, mm))
        if at <= now or alarm.get("last_triggered_date") == day.isoformat():
            continue
        if alarm.get("target_date"):
            if alarm["target_date"] == day.isoformat():
                return at
            continue
        if not weekdays or WEEKDAYS[day.weekday()] in weekdays:
            return at
    return None


def due_day(alarm: dict, prev: datetime.datetime, now: datetime.datetime):
    """Return the ISO date the alarm is ringing for, or None.

    prev is the previous scheduler tick. An alarm is due when its time falls in (prev, now], or when
    now is inside its minute (so an alarm set for the current minute still rings), and it is at most
    CATCH_UP late. Yesterday is checked too, so 23:55 still rings after waking at 00:10.
    """
    try:
        hh, mm = (int(x) for x in str(alarm["time_hhmm"]).split(":"))
    except (KeyError, ValueError):
        return None
    weekdays = alarm.get("weekdays") or []
    for day in (now.date(), now.date() - datetime.timedelta(days=1)):
        at = datetime.datetime.combine(day, datetime.time(hh, mm))
        in_minute = at <= now < at + datetime.timedelta(minutes=1)
        passed = prev < at <= now
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
