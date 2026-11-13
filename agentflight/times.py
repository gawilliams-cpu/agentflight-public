"""Time helpers.

All times are US Eastern local time, stored as "YYYY-MM-DDTHH:MM" strings with
no time zone; every airport in the schedule is in Eastern time. Daylight saving
ends Sun 11/1 at 02:00 and is ignored on purpose: it would only affect rest
measured across Saturday night, and no crew flies on both Saturday and Sunday.
"""

from datetime import datetime

FORMAT = "%Y-%m-%dT%H:%M"


def parse(text: str) -> datetime:
    """Turn "2026-10-29T16:00" into a datetime. Raises ValueError if malformed."""
    return datetime.strptime(text, FORMAT)


def to_text(moment: datetime) -> str:
    """Turn a datetime back into "2026-10-29T16:00"."""
    return moment.strftime(FORMAT)


def minutes_between(start: datetime, end: datetime) -> int:
    return round((end - start).total_seconds() / 60)


def duration(minutes: int) -> str:
    """Format minutes the way people read them: 793 -> "13 h 13 min", 47 -> "47 min"."""
    hours, mins = divmod(abs(minutes), 60)
    if hours == 0:
        return f"{mins} min"
    return f"{hours} h {mins:02d} min"


def label(moment: datetime) -> str:
    """Short human label: "Thu 10/29 16:00"."""
    return f"{moment:%a} {moment.month}/{moment.day} {moment:%H:%M}"


def day_label(moment: datetime) -> str:
    """Short day label: "Thu 10/29"."""
    return f"{moment:%a} {moment.month}/{moment.day}"
