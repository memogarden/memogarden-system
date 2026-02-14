"""**DEPRECATED:** Configuration management for memogarden-system.

Import from utils.config instead. This module re-exports for compatibility.

This module now re-exports from the memogarden-utils package for backward
compatibility during the system boundary refactor. New code should import
directly from the utils.config package:

    from utils.config import Settings, ResourceProfile, get_config_path

For compatibility during the transition, existing imports continue to work:

    from system.config import Settings, ResourceProfile
"""

# Re-export from utils.config for backward compatibility
# Phase 3: system.config is now a compatibility shim
from utils.config.base import Settings, get_config_path, load_toml_config
from utils.config.profiles import ResourceProfile

__all__ = ["Settings", "get_config_path", "load_toml_config", "ResourceProfile"]

# Default settings instance (legacy compatibility)
# In API package, this will be replaced by full app settings
default_settings = Settings()
