"""Environment variable access and path resolution (RFC-004).

This module provides utilities for resolving database paths based on
environment variables and default locations.

Path Resolution Order (RFC-004 INV-PKG-001):
1. Layer-specific environment variable (MEMOGARDEN_SOIL_DB, MEMOGARDEN_CORE_DB)
2. Shared data directory (MEMOGARDEN_DATA_DIR)
3. Current directory (./{layer}.db)

This ensures backward compatibility (explicit paths still work) while
providing flexible configuration for different deployment scenarios.
"""

import os
from pathlib import Path


def get_env(key: str, default: str | None = None) -> str | None:
    """Get environment variable.

    Args:
        key: Environment variable name
        default: Default value if not found

    Returns:
        Environment variable value or default
    """
    return os.environ.get(key, default)


def get_db_path(layer: str) -> Path:
    """Resolve database path for a layer (RFC-004).

    Resolution order (INV-PKG-001):
    1. Layer-specific environment variable (MEMOGARDEN_{LAYER}_DB)
    2. Shared data directory (MEMOGARDEN_DATA_DIR/{layer}.db)
    3. Current directory (./{layer}.db)

    Args:
        layer: Database layer ('soil' or 'core')

    Returns:
        Path to database file

    Raises:
        ValueError: If layer is not 'soil' or 'core'

    Examples:
        >>> # Layer-specific override
        >>> os.environ['MEMOGARDEN_SOIL_DB'] = '/custom/soil.db'
        >>> get_db_path('soil')
        Path('/custom/soil.db')

        >>> # Shared data directory
        >>> os.environ['MEMOGARDEN_DATA_DIR'] = '/data'
        >>> get_db_path('core')
        Path('/data/core.db')

        >>> # Default (current directory)
        >>> get_db_path('soil')
        Path('./soil.db')
    """
    if layer not in ("soil", "core"):
        raise ValueError(f"Invalid layer: {layer}. Must be 'soil' or 'core'")

    # 1. Layer-specific override (highest priority)
    env_var = f"MEMOGARDEN_{layer.upper()}_DB"
    layer_path = get_env(env_var)
    if layer_path:
        return Path(layer_path)

    # 2. Shared data directory
    data_dir = get_env("MEMOGARDEN_DATA_DIR")
    if data_dir:
        return Path(data_dir) / f"{layer}.db"

    # 3. Default: current directory (backward compatible, INV-PKG-002)
    return Path(f"./{layer}.db")
