"""Secret generation utilities for MemoGarden Core.

This module provides a centralized interface for generating all types of secrets
used in the codebase: API keys, UUIDs, random tokens, etc.

All third-party imports for secret generation are confined to this module.
Other parts of the codebase should only import from this module:
    from system.utils import secret

This approach:
- Simplifies dependency management
- Makes it easy to change secret generation implementations
- Provides clear audit trail for all secret generation
- Reduces attack surface by confining third-party crypto imports
"""

import secrets
import uuid as _uuid
from typing import Literal

# ============================================================================
# UUID Generation
# ============================================================================


def generate_uuid() -> str:
    """
    Generate a random UUID v4 as a string.

    Use this for all entity IDs, user IDs, API key IDs, etc.

    Returns:
        UUID v4 as a string (e.g., "550e8400-e29b-41d4-a716-446655440000")

    Example:
    ```python
    user_id = generate_uuid()
    # Returns: "550e8400-e29b-41d4-a716-446655440000"
    ```

    Note:
        This is the ONLY place in the codebase that imports uuid4.
        All other modules should use this function instead.
    """
    return str(_uuid.uuid4())


# ============================================================================
# API Key Generation
# ============================================================================

# API key format: mg_sk_<type>_<random>
# Example: mg_sk_agent_abc123def456...
API_KEY_PREFIX = "mg_sk"
API_KEY_RANDOM_BYTES = 32  # Number of random bytes (hex-encoded = 64 chars)


def generate_api_key(type: Literal["agent"] = "agent") -> str:
    """
    Generate a new API key for authentication.

    The API key format is: mg_sk_<type>_<random>
    - Prefix: mg_sk_agent_ (12 characters)
    - Random: 64 hex characters (32 bytes)

    Args:
        type: API key type (currently only "agent" supported)

    Returns:
        API key string (e.g., "mg_sk_agent_abc123def456...")

    Example:
    ```python
    key = generate_api_key()
    # Returns: "mg_sk_agent_9a2b8c7d..." (76 characters total)

    key = generate_api_key(type="agent")
    # Returns: "mg_sk_agent_9a2b8c7d..."
    ```

    Note:
        This is the ONLY place in the codebase that generates API keys.
        All other modules should use this function instead.
    """
    random_part = secrets.token_hex(API_KEY_RANDOM_BYTES)
    return f"{API_KEY_PREFIX}_{type}_{random_part}"


def get_api_key_prefix(api_key: str) -> str:
    """
    Extract the prefix from an API key for display.

    The prefix includes the type identifier: "mg_sk_agent_"

    Args:
        api_key: Full API key

    Returns:
        API key prefix (first 12 characters)

    Example:
    ```python
    prefix = get_api_key_prefix("mg_sk_agent_abc123def456...")
    # Returns: "mg_sk_agent_"
    ```
    """
    return api_key[:12]


# ============================================================================
# Random Token Generation
# ============================================================================


def generate_token(num_bytes: int = 32) -> str:
    """
    Generate a cryptographically secure random token.

    Useful for password reset tokens, email verification tokens, etc.

    Args:
        num_bytes: Number of random bytes (default 32, hex-encoded = 64 chars)

    Returns:
        Hex-encoded random token

    Example:
    ```python
    # Default 64-character hex token
    token = generate_token()
    # Returns: "9a2b8c7d..."

    # Shorter 32-character hex token
    short_token = generate_token(num_bytes=16)
    # Returns: "9a2b8c7d..."
    ```
    """
    return secrets.token_hex(num_bytes)


def generate_password(length: int = 16) -> str:
    """
    Generate a cryptographically secure random password.

    Uses alphanumeric characters (letters and digits).

    Args:
        length: Password length in characters (default 16)

    Returns:
        Random password string

    Example:
    ```python
    password = generate_password()
    # Returns: "xK9mP2nQ8vL7wR3t"

    password = generate_password(length=12)
    # Returns: "aB3xY9mK2pQ"
    ```
    """
    # Ensure we have both letters and digits
    chars = []
    for _ in range(length):
        chars.append(secrets.choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"))

    return ''.join(chars)


# ============================================================================
# Constants for Reference
# ============================================================================

# Common secret lengths
UUID_LENGTH = 36  # "550e8400-e29b-41d4-a716-446655440000"
API_KEY_LENGTH = 76  # "mg_sk_agent_" (12) + 64 hex chars
DEFAULT_TOKEN_LENGTH = 64  # 32 bytes hex-encoded
