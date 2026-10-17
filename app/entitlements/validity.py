"""
app/entitlements/validity.py
The one expiry policy for plans and special access, as pure functions of a clock and a timezone.

Policy
  * Every expiry is ONE unambiguous instant, stored as naive UTC like every other timestamp in this schema
    (users.plan_expires_at, user_feature_overrides.expires_at) and shown in APP_TIMEZONE.
  * "N days" means N calendar days from today in APP_TIMEZONE: access lasts through the end of that day, 23:59:59 local time.
    The remainder of today is a bonus, so an admin who gives 15 days always gives at least 15 full days; saving the same
    number of days again on the same day produces the same expiry.
  * A blank duration is "no expiry". It is never read as zero days.
  * A plan or override is active while its expiry is later than the current instant; at the expiry instant itself it has
    lapsed. Authorization compares with the current time on every check (resolver.py), so nothing has to run for expiry
    to take effect.
"""

from datetime import datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import app.config as config
from app.utils.datetime_service import app_timezone

EXPIRING_SOON = timedelta(days=3)
_END_OF_DAY = time(23, 59, 59, 999999)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def end_of_day_utc(day, tz: Optional[ZoneInfo] = None) -> datetime:
    """The last instant of a calendar day in the application timezone, as naive UTC."""
    local = datetime.combine(day, _END_OF_DAY, tzinfo=tz or app_timezone())
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def expiry_from_days(days: int, now: datetime, tz: Optional[ZoneInfo] = None) -> datetime:
    if days < 1:
        raise ValueError("days must be at least 1.")
    today = _aware(now).astimezone(tz or app_timezone()).date()
    return end_of_day_utc(today + timedelta(days=days), tz)


def parse_expires_at(raw) -> datetime:
    """An ISO timestamp from an API caller, as naive UTC. A value without an offset is taken as UTC."""
    try:
        parsed = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("expires_at must be an ISO date and time, for example 2026-12-31T23:59:59Z.")
    return _aware(parsed).astimezone(timezone.utc).replace(tzinfo=None)


def plan_expiry_to_store(plan: Optional[str], requested: Optional[datetime], current_plan: Optional[str],
                         current_expires: Optional[datetime], now: datetime) -> Optional[datetime]:
    """The expiry to save when an administrator sets a plan. `requested` is None when no duration was given. Saving the same
    plan again without a duration keeps its running expiry instead of silently clearing it; in every other case the request
    stands, and None means no expiry."""
    if requested is None and plan and current_plan == plan and current_expires and current_expires > now:
        return current_expires
    return requested


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _clock(local: datetime) -> str:
    return local.strftime("%I:%M %p").lstrip("0")


def _remaining_text(seconds: int, expires_local: datetime, now_local: datetime) -> str:
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if expires_local.date() == now_local.date() and seconds >= 3600:
        return f"Expires today at {_clock(expires_local)}"
    if days:
        text = _plural(days, "day")
        if days < 7 and hours:
            text += f", {_plural(hours, 'hour')}"
        return f"{text} remaining"
    if hours:
        return f"{_plural(hours, 'hour')}{', ' + _plural(minutes, 'minute') if minutes else ''} remaining"
    if minutes:
        return f"{_plural(minutes, 'minute')} remaining"
    return "Less than a minute remaining"


def _remaining_short(seconds: int) -> str:
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}d {hours}h" if days < 7 and hours else f"{days}d"
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return f"{minutes}m" if minutes else "<1m"


def display(value: Optional[datetime], tz: Optional[ZoneInfo] = None) -> Optional[str]:
    """A stored instant as the exact local date and time, with the timezone, in the configured display format."""
    if value is None:
        return None
    local = _aware(value).astimezone(tz or app_timezone())
    return f"{local.strftime(config.DISPLAY_DATETIME_FORMAT)} {local.tzname() or ''}".strip()


def describe(expires_at: Optional[datetime], now: datetime, tz: Optional[ZoneInfo] = None) -> dict:
    """state: none | active | expiring_soon | expired, with the exact expiry and a readable remaining time."""
    zone = tz or app_timezone()
    if expires_at is None:
        return {"state": "none", "expires_at": None, "expires_at_utc": None, "expires_display": None,
                "remaining_seconds": None, "remaining_text": "No expiry", "remaining_short": None}
    seconds = int((expires_at - now).total_seconds())
    shown = display(expires_at, zone)
    base = {"expires_at": expires_at.isoformat(), "expires_at_utc": expires_at.isoformat() + "Z", "expires_display": shown}
    if seconds <= 0:
        return {**base, "state": "expired", "remaining_seconds": 0, "remaining_text": f"Expired {shown}", "remaining_short": "Expired"}
    expires_local = _aware(expires_at).astimezone(zone)
    now_local = _aware(now).astimezone(zone)
    soon = timedelta(seconds=seconds) <= EXPIRING_SOON
    return {**base, "state": "expiring_soon" if soon else "active", "remaining_seconds": seconds,
            "remaining_text": _remaining_text(seconds, expires_local, now_local), "remaining_short": _remaining_short(seconds)}
