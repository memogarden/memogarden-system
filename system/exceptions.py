"""Custom exceptions for MemoGarden Core.

This module provides exception classes used throughout the system.
"""


class MemoGardenError(Exception):
    """Base exception for all MemoGarden errors."""

    def __init__(self, message: str, details: dict | None = None):
        """Initialize exception with message and optional details.

        Args:
            message: Human-readable error message
            details: Optional dictionary with additional error context
        """
        super().__init__(message)
        self.message = message
        self.details = details


class ResourceNotFound(MemoGardenError):
    """Exception raised when a requested resource is not found."""

    pass


class ValidationError(MemoGardenError):
    """Exception raised when input validation fails."""

    pass


class AuthenticationError(MemoGardenError):
    """Exception raised when authentication fails."""

    pass
