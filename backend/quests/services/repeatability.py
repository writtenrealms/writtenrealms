"""Shared, query-free quest repeat boundaries for runtime and journal projections."""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def next_daily_reset(completed_at, *, reset_at: str, timezone_name: str):
    """Return the first wall-clock reset strictly after a completion.

    Fold 0 selects the earlier occurrence of an ambiguous local time. A missing
    time advances by the DST gap (02:30 becomes 03:30 for a one-hour gap). UTC
    comparison is essential: local datetime comparisons ignore a DST fold.
    """
    zone = ZoneInfo(timezone_name)
    reset_time = time.fromisoformat(reset_at)
    completed_utc = completed_at.astimezone(timezone.utc)
    local_date = completed_utc.astimezone(zone).date()
    for day_offset in range(3):
        candidate = datetime.combine(
            local_date + timedelta(days=day_offset), reset_time, tzinfo=zone,
        ).astimezone(timezone.utc)
        if candidate > completed_utc:
            return candidate
    raise ValueError("Could not determine the next daily quest reset")


def repeatability_ready_at(template, *, resolved_at, cooldown_anchor=None):
    if resolved_at is None:
        return None
    if template.repeatability_mode == "daily":
        return next_daily_reset(
            resolved_at,
            reset_at=template.repeatability_reset_at,
            timezone_name=template.repeatability_timezone,
        )
    if template.repeatability_mode == "cooldown":
        return (cooldown_anchor or resolved_at) + timedelta(
            seconds=int(template.repeatability_cooldown_seconds or 0),
        )
    return None
