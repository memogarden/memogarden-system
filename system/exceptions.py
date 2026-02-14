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


class LockConflictError(MemoGardenError):
    """Exception raised when an optimistic locking conflict occurs."""

    pass


class OptimisticLockError(LockConflictError):
    """Exception raised when entity update fails due to hash mismatch (RFC-008).

    Raised when an entity update operation provides a based_on_hash that
    doesn't match the entity's current hash, indicating the entity was
    modified by another transaction.

    Attributes:
        entity_uuid: UUID of the entity that failed the hash check
        expected_hash: The hash that was expected (based_on_hash provided)
        actual_hash: The actual current hash of the entity
    """

    entity_uuid: str
    expected_hash: str
    actual_hash: str

    def __init__(self, message: str, entity_uuid: str, expected_hash: str, actual_hash: str):
        """Initialize optimistic locking error.

        Args:
            message: Human-readable error message
            entity_uuid: UUID of the entity that failed the hash check
            expected_hash: The hash that was expected (based_on_hash provided)
            actual_hash: The actual current hash of the entity
        """
        super().__init__(
            message,
            details={
                "entity_uuid": entity_uuid,
                "expected_hash": expected_hash,
                "actual_hash": actual_hash,
            }
        )
        self.entity_uuid = entity_uuid
        self.expected_hash = expected_hash
        self.actual_hash = actual_hash


class PermissionDenied(MemoGardenError):
    """Exception raised when access is denied due to insufficient permissions."""

    pass


class ConsistencyError(MemoGardenError):
    """Exception raised when cross-database inconsistency is detected (RFC-008).

    Raised when Soil commits but Core fails, or when startup checks
    detect orphaned EntityDeltas or broken hash chains.

    Attributes:
        soil_committed: Whether Soil database was committed
        core_error: Error from Core database (if applicable)
        orphans: List of orphaned EntityDeltas (if applicable)
        broken_chains: List of broken hash chains (if applicable)
    """

    soil_committed: bool
    core_error: str | None
    orphans: list[dict]
    broken_chains: list[dict]

    def __init__(self, message: str, details: dict | None = None):
        """Initialize consistency error.

        Args:
            message: Human-readable error message
            details: Optional dictionary with additional error context
        """
        super().__init__(message, details)
        self.soil_committed = details.get("soil_committed", False) if details else False
        self.core_error = details.get("core_error") if details else None
        self.orphans = details.get("orphans", []) if details else []
        self.broken_chains = details.get("broken_chains", []) if details else []


class ConflictError(MemoGardenError):
    """Exception raised when an optimistic locking conflict occurs on artifact edits.

    Raised when attempting to commit artifact delta operations with a
    based_on_hash that doesn't match the artifact's current hash,
    indicating the artifact was modified by another transaction.

    This is part of the Project Studio artifact delta operations (Session 17).

    Attributes:
        artifact_uuid: UUID of the artifact that failed the hash check
        expected_hash: The hash that was expected (based_on_hash provided)
        actual_hash: The actual current hash of the artifact
    """

    artifact_uuid: str
    expected_hash: str
    actual_hash: str

    def __init__(
        self,
        message: str,
        artifact_uuid: str,
        expected_hash: str,
        actual_hash: str,
    ):
        """Initialize conflict error.

        Args:
            message: Human-readable error message
            artifact_uuid: UUID of the artifact that failed the hash check
            expected_hash: The hash that was expected (based_on_hash provided)
            actual_hash: The actual current hash of the artifact
        """
        super().__init__(
            message,
            details={
                "artifact_uuid": artifact_uuid,
                "expected_hash": expected_hash,
                "actual_hash": actual_hash,
            },
        )
        self.artifact_uuid = artifact_uuid
        self.expected_hash = expected_hash
        self.actual_hash = actual_hash
