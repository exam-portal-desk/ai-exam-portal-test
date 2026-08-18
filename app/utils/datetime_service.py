"""
app/utils/datetime_service.py
Central date/time service — all application timestamps are generated and
stored in UTC; user-facing display always goes through here so the
server's physical timezone never determines what a user sees.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import app.config as config

_APP_TZ = ZoneInfo(config.APP_TIMEZONE)


def now_utc() -> datetime:
    """Current time in UTC — use for anything written to the database."""
    return datetime.now(timezone.utc)


def now_utc_iso() -> str:
    return now_utc().isoformat()


def now_utc_naive() -> datetime:
    """Naive UTC datetime, for writing to `timestamp without time zone` columns
    (this schema's convention) without a stray server-local offset."""
    return now_utc().replace(tzinfo=None)


def now_app_tz() -> datetime:
    """Current time in APP_TIMEZONE — for display-only "today" logic."""
    return datetime.now(_APP_TZ)


def today_app_date() -> str:
    """Today's date as YYYY-MM-DD in APP_TIMEZONE — for date-keyed usage rows."""
    return now_app_tz().strftime("%Y-%m-%d")


def to_app_tz(value):
    """Parse a stored timestamp (ISO string or datetime) and convert to APP_TIMEZONE.
    Naive values are assumed to be UTC (how this app stores timestamps)."""
    if not value:
        return None
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_APP_TZ)


def format_display(value, fmt: str = None) -> str:
    """Format a stored timestamp for display in APP_TIMEZONE."""
    dt = to_app_tz(value)
    return dt.strftime(fmt or config.DISPLAY_DATETIME_FORMAT) if dt else ""


def format_display_date(value) -> str:
    dt = to_app_tz(value)
    return dt.strftime(config.DISPLAY_DATE_FORMAT) if dt else ""


def daily_reset_message() -> str:
    """Human-readable string for when a daily-limit counter resets
    (local midnight in APP_TIMEZONE), e.g. "Resets in 3h 12m at 12:00 AM IST"."""
    now_local = now_app_tz()
    midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    delta = midnight - now_local

    hours = int(delta.total_seconds() // 3600)
    minutes = int((delta.total_seconds() % 3600) // 60)
    tz_label = now_local.strftime("%Z") or config.APP_TIMEZONE

    if hours >= 20:
        when = "tomorrow"
    elif hours == 0:
        when = f"in {minutes} min"
    else:
        when = f"in {hours}h {minutes}m"

    return f"Resets {when} at 12:00 AM {tz_label}"
