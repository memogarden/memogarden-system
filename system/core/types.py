"""Domain types for MemoGarden.

These types define the fundamental data representations used throughout
the system, ensuring consistency between API, database, and business logic.
"""

from datetime import date, datetime

from system.utils import isodatetime


class Timestamp(str):
    """ISO 8601 UTC timestamp string.

    Represents a point in time with timezone awareness (UTC only).
    Format: '2025-12-23T10:30:00Z'

    This is a string subtype for JSON serialization compatibility while
    providing type-safe conversion methods.
    """

    @classmethod
    def from_datetime(cls, dt: datetime) -> "Timestamp":
        """Convert datetime to Timestamp.

        Args:
            dt: datetime object (naive datetimes treated as UTC)

        Returns:
            Timestamp string in ISO 8601 format with 'Z' suffix
        """
        return cls(isodatetime.to_timestamp(dt))

    @classmethod
    def now(cls) -> "Timestamp":
        """Get current UTC timestamp.

        Returns:
            Timestamp representing current time in UTC
        """
        return cls(isodatetime.now())

    def to_datetime(self) -> datetime:
        """Convert Timestamp to datetime.

        Returns:
            datetime object with UTC timezone info
        """
        return isodatetime.to_datetime(self)


class Date(str):
    """ISO 8601 date string.

    Represents a calendar date without time or timezone.
    Format: '2025-12-23'

    This is a string subtype for JSON serialization compatibility while
    providing type-safe conversion methods.
    """

    @classmethod
    def from_date(cls, d: date) -> "Date":
        """Convert date to Date string.

        Args:
            d: date object

        Returns:
            Date string in ISO 8601 format (YYYY-MM-DD)
        """
        return cls(isodatetime.to_datestring(d))

    @classmethod
    def today(cls) -> "Date":
        """Get today's date as Date string.

        Returns:
            Date representing today (local date)
        """
        return cls.from_date(date.today())

    def to_date(self) -> date:
        """Convert Date string to date object.

        Returns:
            date object
        """
        return date.fromisoformat(self)
