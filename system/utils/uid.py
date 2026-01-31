"""UUID generation and prefix utilities.

This module centralizes all UUID generation and prefix handling.

Per PRD v6:
- Core entities use core_ prefix
- Future Soil items will use soil_ prefix
- Storage uses plain UUID (no prefix) for simplicity
- API layer adds/strips prefixes at boundary
"""

from uuid import uuid4

# Prefix constants (PRD v6 UUID namespaces)
UUID_PREFIX_CORE = "core_"
UUID_PREFIX_SOIL = "soil_"


def generate_uuid() -> str:
    """Generate a random UUID v4 as a string (plain, no prefix).

    The prefix is added at the API boundary, not in storage.
    Storage uses plain UUID for simplicity and compatibility.

    Returns:
        Plain UUID4 string (e.g., "a1b2c3d4-e5f6-7890-abcd-ef1234567890")
    """
    return str(uuid4())


def add_core_prefix(uuid: str) -> str:
    """Add core_ prefix to a UUID if not already present.

    Used when returning data from the API.

    Args:
        uuid: Plain or prefixed UUID string

    Returns:
        UUID with core_ prefix (e.g., "core_a1b2c3d4-e5f6-7890-abcd-ef1234567890")
    """
    if uuid.startswith(UUID_PREFIX_CORE) or uuid.startswith(UUID_PREFIX_SOIL):
        return uuid
    return f"{UUID_PREFIX_CORE}{uuid}"


def add_soil_prefix(uuid: str) -> str:
    """Add soil_ prefix to a UUID if not already present.

    Args:
        uuid: Plain or prefixed UUID string

    Returns:
        UUID with soil_ prefix
    """
    if uuid.startswith(UUID_PREFIX_SOIL) or uuid.startswith(UUID_PREFIX_CORE):
        return uuid
    return f"{UUID_PREFIX_SOIL}{uuid}"


def strip_prefix(uuid: str) -> str:
    """Remove core_ or soil_ prefix from a UUID.

    Used when processing incoming API requests.

    Args:
        uuid: Prefixed or plain UUID string

    Returns:
        Plain UUID string (e.g., "a1b2c3d4-e5f6-7890-abcd-ef1234567890")
    """
    if uuid.startswith(UUID_PREFIX_CORE):
        return uuid[len(UUID_PREFIX_CORE):]
    if uuid.startswith(UUID_PREFIX_SOIL):
        return uuid[len(UUID_PREFIX_SOIL):]
    return uuid


def has_core_prefix(uuid: str) -> bool:
    """Check if UUID has core_ prefix."""
    return uuid.startswith(UUID_PREFIX_CORE)


def has_soil_prefix(uuid: str) -> bool:
    """Check if UUID has soil_ prefix."""
    return uuid.startswith(UUID_PREFIX_SOIL)


def has_prefix(uuid: str) -> bool:
    """Check if UUID has any known prefix."""
    return has_core_prefix(uuid) or has_soil_prefix(uuid)
