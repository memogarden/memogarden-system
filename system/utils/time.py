"""Time utilities for MemoGarden System.

Provides date/time functions for time horizon computation and other
time-based operations.
"""

from datetime import date, timedelta

# Epoch for days-since calculations (RFC-002)
EPOCH = date(2020, 1, 1)


def current_day() -> int:
    """Get current day as days since epoch.

    Returns:
        Days since EPOCH (2020-01-01)

    Examples:
        >>> current_day()  # If today is 2026-02-07
        2229
    """
    return (date.today() - EPOCH).days


def day_to_date(day: int) -> date:
    """Convert day since epoch to date.

    Args:
        day: Days since EPOCH (2020-01-01)

    Returns:
        Corresponding date

    Examples:
        >>> day_to_date(2229)
        datetime.date(2026, 2, 7)
    """
    return EPOCH + timedelta(days=day)
