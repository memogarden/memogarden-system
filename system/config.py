"""Configuration management for memogarden-system.

This module provides settings for the system package.
For the full application, use the API package's config which extends this.

Database Path Resolution (RFC-004):
- If database_path is None, path is resolved via get_db_path('core')
- If database_path is provided, it is used directly (backward compatible)
"""

from pathlib import Path
from typing import Optional


class Settings:
    """System settings with defaults.

    In production, these can be overridden by environment variables
    or by passing a custom Settings instance to get_core().

    Database Path Resolution (RFC-004):
    - database_path=None: Resolve via get_db_path('core') using env vars
    - database_path=<path>: Use explicit path (backward compatible)
    """

    def __init__(
        self,
        database_path: Optional[str] = None,
        default_currency: str = "SGD",
    ):
        """Initialize settings.

        Args:
            database_path: Path to Core database file. If None, resolved
                via get_db_path('core') using environment variables.
            default_currency: Default currency code (e.g., "SGD", "USD")
        """
        self.database_path = database_path  # None triggers env var resolution
        self.default_currency = default_currency


# Default settings instance
# In API package, this will be replaced by full app settings
default_settings = Settings()
