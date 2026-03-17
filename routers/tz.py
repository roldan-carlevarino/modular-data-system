"""Centralised timezone helpers. All backend code should use these
instead of date.today() / datetime.now() so that the server (UTC)
returns times in the user's local timezone (Europe/Amsterdam – CET/CEST)."""

from datetime import date, datetime, timezone, timedelta

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except ImportError:
    from backports.zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Europe/Amsterdam")


def local_now() -> datetime:
    """Current datetime in CET/CEST (Europe/Amsterdam)."""
    return datetime.now(LOCAL_TZ)


def local_today() -> date:
    """Current date in CET/CEST."""
    return local_now().date()
