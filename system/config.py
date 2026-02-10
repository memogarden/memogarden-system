"""Configuration management for memogarden-system.

This module provides settings for the system package following RFC 004.
Configuration is loaded from TOML files with support for multiple deployment contexts.

Configuration Resolution Order (RFC 004):
1. Explicit config path (--config flag)
2. Context-based defaults (serve/run/deploy verbs)
3. Built-in defaults

For the full application, use the API package's config which extends this.
"""

import sys
from pathlib import Path
from typing import Optional, Any

# Python 3.11+ has tomllib in stdlib, otherwise use tomli
if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None


def load_toml_config(config_path: Path) -> dict[str, Any]:
    """Load configuration from TOML file.

    Args:
        config_path: Path to config.toml file

    Returns:
        Dictionary with configuration sections

    Raises:
        ImportError: If tomli is not available (Python < 3.11)
        FileNotFoundError: If config file doesn't exist
        ValueError: If config file is invalid TOML
    """
    if tomllib is None:
        raise ImportError(
            "tomli is required for Python < 3.11. "
            "Install it with: pip install tomli"
        )

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "rb") as f:
        try:
            return tomllib.load(f)
        except Exception as e:
            raise ValueError(f"Invalid TOML in {config_path}: {e}")


def get_config_path(verb: str = "run", config_override: Optional[Path] = None) -> Path:
    """Get configuration file path based on deployment context (RFC 004).

    Args:
        verb: Deployment verb (serve, run, deploy)
        config_override: Optional explicit config path

    Returns:
        Path to configuration file

    Examples:
        >>> get_config_path("serve")
        Path('/etc/memogarden/config.toml')

        >>> get_config_path("run")
        Path('~/.config/memogarden/config.toml').expanduser()
    """
    if config_override:
        return config_override

    if verb == "serve":
        return Path("/etc/memogarden/config.toml")
    elif verb == "run":
        return Path.home() / ".config/memogarden/config.toml"
    elif verb == "deploy":
        return Path("/config/config.toml")
    else:
        # Default to user config
        return Path.home() / ".config/memogarden/config.toml"


class ResourceProfile:
    """Resource profile settings (RFC 004).

    Profiles are operator-declared, not hardware-detected.
    """

    PROFILES = {
        "embedded": {
            "max_view_entries": 100,
            "max_search_results": 20,
            "fossilization_threshold": 0.80,
            "wal_checkpoint_interval": 300,
            "log_level": "warning",
        },
        "standard": {
            "max_view_entries": 1000,
            "max_search_results": 100,
            "fossilization_threshold": 0.90,
            "wal_checkpoint_interval": 60,
            "log_level": "info",
        },
    }

    @classmethod
    def get_profile(cls, name: str) -> dict[str, Any]:
        """Get resource profile settings.

        Args:
            name: Profile name (embedded or standard)

        Returns:
            Dictionary with profile settings

        Raises:
            ValueError: If profile name is unknown
        """
        if name not in cls.PROFILES:
            raise ValueError(
                f"Unknown resource profile: {name}. "
                f"Available: {list(cls.PROFILES.keys())}"
            )
        return cls.PROFILES[name].copy()


class Settings:
    """System settings with RFC 004 TOML configuration support.

    Database Path Resolution (RFC-004):
    - If database_path is None, path is resolved via get_db_path('core')
    - If database_path is provided, it is used directly (backward compatible)

    Configuration Loading:
    1. Load from TOML config file (if exists)
    2. Apply resource profile defaults
    3. Apply runtime overrides (optional)
    4. Fall back to built-in defaults
    """

    def __init__(
        self,
        database_path: Optional[str] = None,
        default_currency: str = "SGD",
        config_path: Optional[Path] = None,
        verb: str = "run",
    ):
        """Initialize settings.

        Args:
            database_path: Path to Core database file. If None, resolved
                via get_db_path('core') using environment variables.
            default_currency: Default currency code (e.g., "SGD", "USD")
            config_path: Optional explicit path to config.toml
            verb: Deployment verb (serve, run, deploy) for config resolution
        """
        self.database_path = database_path
        self.default_currency = default_currency

        # Load TOML config if available
        self._config: dict[str, Any] = {}
        self._verb = verb

        if config_path is None:
            config_path = get_config_path(verb)

        if config_path.exists():
            try:
                self._config = load_toml_config(config_path)
                self._apply_config()
            except (ImportError, ValueError) as e:
                # Log warning but continue with defaults
                import warnings
                warnings.warn(f"Failed to load config from {config_path}: {e}")

    def _apply_config(self):
        """Apply TOML configuration to settings.

        Applies settings in order:
        1. Resource profile defaults
        2. Runtime overrides
        """
        # Get resource profile
        runtime_config = self._config.get("runtime", {})
        resource_profile = runtime_config.get("resource_profile", "standard")
        profile_settings = ResourceProfile.get_profile(resource_profile)

        # Apply profile settings (can be overridden by explicit values)
        for key, value in profile_settings.items():
            setattr(self, key, value)

        # Apply runtime overrides
        for key, value in runtime_config.items():
            if key != "resource_profile":
                setattr(self, key, value)

        # Apply network settings
        network_config = self._config.get("network", {})
        self.bind_address = network_config.get("bind_address", "127.0.0.1")
        self.bind_port = network_config.get("bind_port", 8080)

        # Apply security settings
        security_config = self._config.get("security", {})
        self.encryption = security_config.get("encryption", "disabled")

        # Apply path overrides (optional)
        paths_config = self._config.get("paths", {})
        if paths_config.get("data_dir"):
            self.data_dir = Path(paths_config["data_dir"])
        if paths_config.get("config_dir"):
            self.config_dir = Path(paths_config["config_dir"])
        if paths_config.get("log_dir"):
            self.log_dir = Path(paths_config["log_dir"])

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value.

        Args:
            key: Configuration key
            default: Default value if key not found

        Returns:
            Configuration value or default
        """
        return getattr(self, key, default)


# Default settings instance (legacy compatibility)
# In API package, this will be replaced by full app settings
default_settings = Settings()
