"""
Recurrence utilities for MemoGarden Core.

This module confines all python-dateutil imports and provides a clean
interface for working with iCal RFC 5545 recurrence rules (RRULE).

All datetime and recurrence logic should be centralized in this module
to maintain consistency and limit dependency footprint.
"""

from datetime import datetime, date
from typing import List, Optional

from dateutil.rrule import rrulestr, rrule
from dateutil.relativedelta import relativedelta

from system.utils.isodatetime import (
    to_datetime as parse_isodatetime,
    to_timestamp as format_isodatetime,
    now as now_utc,
)


def validate_rrule(rrule_str: str) -> bool:
    """
    Validate an iCal RRULE string.

    Args:
        rrule_str: RRULE string (e.g., "FREQ=MONTHLY;BYDAY=2FR")

    Returns:
        True if RRULE is valid, False otherwise

    Example:
        >>> validate_rrule("FREQ=MONTHLY;BYDAY=2FR")
        True
        >>> validate_rrule("INVALID")
        False
    """
    try:
        rrulestr(rrule_str)
        return True
    except Exception:
        return False


def generate_occurrences(
    rrule_str: str,
    start: datetime,
    end: Optional[datetime] = None,
    count: Optional[int] = None,
) -> List[datetime]:
    """
    Generate occurrences from an RRULE string.

    Args:
        rrule_str: iCal RRULE string
        start: Start datetime for occurrence generation
        end: Optional end datetime (exclusive)
        count: Optional maximum number of occurrences to generate

    Returns:
        List of datetime objects representing occurrences

    Example:
        >>> start = datetime(2025, 1, 1)
        >>> generate_occurrences("FREQ=MONTHLY;BYDAY=2FR", start, count=3)
        [datetime(2025, 1, 10, 0, 0), datetime(2025, 2, 14, 0, 0), ...]
    """
    rule = rrulestr(rrule_str, dtstart=start)

    if count:
        return list(rule[:count])
    elif end:
        return list(rule.between(start, end))
    else:
        # Default to 100 occurrences if no limit specified
        return list(rule[:100])


def get_next_occurrence(
    rrule_str: str,
    after: datetime,
) -> Optional[datetime]:
    """
    Get the next occurrence after a given datetime.

    Args:
        rrule_str: iCal RRULE string
        after: Get next occurrence after this datetime

    Returns:
        Next occurrence datetime, or None if no more occurrences
    """
    rule = rrulestr(rrule_str, dtstart=after)
    try:
        return rule.after(after, inc=False)
    except Exception:
        return None


def rrule_to_description(rrule_str: str) -> str:
    """
    Convert an RRULE string to a human-readable description.

    Args:
        rrule_str: iCal RRULE string

    Returns:
        Human-readable description (e.g., "Every month on the 2nd Friday")

    Example:
        >>> rrule_to_description("FREQ=MONTHLY;BYDAY=2FR")
        'Every month on the 2nd Friday'
    """
    # This is a placeholder - full implementation would parse
    # the RRULE and generate natural language descriptions
    # For now, return the RRULE string itself
    return f"Recurrence rule: {rrule_str}"


def is_valid_recurrence_window(
    valid_from: str,
    valid_until: Optional[str],
) -> bool:
    """
    Validate that a recurrence window is valid.

    Args:
        valid_from: ISO 8601 datetime string (inclusive)
        valid_until: Optional ISO 8601 datetime string (exclusive)

    Returns:
        True if window is valid, False otherwise

    Raises:
        ValueError: If dates cannot be parsed
    """
    try:
        from_date = parse_isodatetime(valid_from)
        if valid_until:
            until_date = parse_isodatetime(valid_until)
            return from_date < until_date
        return True
    except Exception as e:
        raise ValueError(f"Invalid recurrence window: {e}")


# Additional helper functions can be added here as needed for:
# - RRULE validation for Budget app UI
# - Occurrence filtering by date range
# - Next N occurrences for preview
# - RRULE string construction from UI selections
