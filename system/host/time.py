"""Time and timestamp utilities."""

from datetime import datetime, timezone


def now_utc() -> datetime:
    """Get current UTC time.

    Returns:
        datetime object with UTC timezone
    """
    return datetime.now(timezone.utc)


def now_iso() -> str:
    """Get current UTC time as ISO 8601 string.

    Returns:
        ISO 8601 formatted timestamp string
    """
    return now_utc().isoformat()
