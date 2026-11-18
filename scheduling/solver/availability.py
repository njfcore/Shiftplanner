from dataclasses import dataclass
from datetime import datetime, time, timedelta


@dataclass(frozen=True)
class Window:
    """A weekly window of an employee.

    weekday follows date.weekday() (Monday=0). available=True means the
    employee can work only inside it; False means they cannot work in it.
    """

    weekday: int
    start_time: time
    end_time: time
    available: bool = True


def _interval(day, start_time, end_time):
    start = datetime.combine(day, start_time)
    end = datetime.combine(day, end_time)
    if end <= start:  # crosses midnight
        end += timedelta(days=1)
    return start, end


def can_work(windows, day, shift):
    """Whether an employee with these windows may work `shift` starting on `day`.

    Rules:
    1. "Unavailable" windows anchored on the previous, same or next day
       must not overlap the shift (they can spill over midnight).
    2. If the employee has "available" windows anchored on the shift's
       start day, the whole shift must fit inside them (touching windows
       are merged). With no such windows, the day is unrestricted.
    """
    shift_start, shift_end = _interval(day, shift.start_time, shift.end_time)

    for offset in (-1, 0, 1):
        anchor = day + timedelta(days=offset)
        for window in windows:
            if window.available or window.weekday != anchor.weekday():
                continue
            w_start, w_end = _interval(anchor, window.start_time, window.end_time)
            if w_start < shift_end and shift_start < w_end:
                return False

    allowed = sorted(
        _interval(day, w.start_time, w.end_time)
        for w in windows
        if w.available and w.weekday == day.weekday()
    )
    if not allowed:
        return True

    merged = []
    for start, end in allowed:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return any(start <= shift_start and shift_end <= end for start, end in merged)