"""Environment variable access and path resolution (RFC-004).

This module provides utilities for resolving database paths based on
environment variables and default locations.

Path Resolution Order (RFC-004 INV-PKG-001):
1. Layer-specific environment variable (MEMOGARDEN_SOIL_DB, MEMOGARDEN_CORE_DB)
2. Shared data directory (MEMOGARDEN_DATA_DIR)
3. Current directory (./{layer}.db)

This ensures backward compatibility (explicit paths still work) while
providing flexible configuration for different deployment scenarios.

Context Resolution (RFC-004 Section 4.1):
- resolve_context(verb, config_override) - Map verb to resource locations
- RuntimeContext - Paths, signal method, and defaults per verb
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class RuntimeContext:
    """Runtime context for MemoGarden deployment (RFC-004 Section 4.1).

    Contexts are determined by command verb, not auto-detection.
    Each verb has specific paths, signal method, and defaults.

    Attributes:
        verb: Command verb (serve, run, deploy)
        data_dir: Data directory for databases
        config_dir: Configuration directory
        log_dir: Log directory (None for container/deploy)
        signal_method: How to signal readiness (systemd, stdout, none)
    """
    verb: str
    data_dir: Path
    config_dir: Path
    log_dir: Optional[Path]
    signal_method: str  # "systemd" | "stdout" | "none"

    def get_db_path(self, db_name: str) -> Path:
        """Return database file path for given context.

        Args:
            db_name: Database name ('soil' or 'core')

        Returns:
            Path to database file
        """
        return self.data_dir / f"{db_name}.db"

    def get_config_path(self) -> Path:
        """Return config file path for given context."""
        return self.config_dir / "config.toml"

    def get_log_path(self, filename: str) -> Optional[Path]:
        """Return log file path for given context (if log_dir exists)."""
        if self.log_dir:
            return self.log_dir / filename
        return None

    @classmethod
    def from_config(cls, config_path: Path, verb: str = "run") -> "RuntimeContext":
        """Create RuntimeContext from explicit config file path.

        Args:
            config_path: Path to config.toml file
            verb: Command verb (defaults to "run")

        Returns:
            RuntimeContext with paths derived from config location
        """
        config_dir = config_path.parent

        # Derive data_dir from config location
        if verb == "serve":
            # System daemon: /etc/memogarden/config.toml -> /var/lib/memogarden
            data_dir = Path("/var/lib/memogarden")
            log_dir = Path("/var/log/memogarden")
            signal_method = "systemd"
        elif verb == "run":
            # User process: ~/.config/memogarden/config.toml -> ~/.local/share/memogarden
            data_dir = Path.home() / ".local/share/memogarden"
            log_dir = Path.home() / ".local/state/memogarden/logs"
            signal_method = "stdout"
        else:  # deploy
            # Container: /config/config.toml -> /data
            data_dir = Path("/data")
            log_dir = None
            signal_method = "none"

        return cls(
            verb=verb,
            data_dir=data_dir,
            config_dir=config_dir,
            log_dir=log_dir,
            signal_method=signal_method
        )


def resolve_context(
    verb: str,
    config_override: Optional[Path] = None
) -> RuntimeContext:
    """Map verb to resource locations (RFC-004 Section 4.1).

    Args:
        verb: Command verb (serve, run, deploy)
        config_override: Optional explicit config path

    Returns:
        RuntimeContext with paths, signal method, and defaults

    Raises:
        ValueError: If verb is not one of: serve, run, deploy

    Examples:
        >>> # System daemon context
        >>> ctx = resolve_context("serve")
        >>> ctx.data_dir
        Path('/var/lib/memogarden')
        >>> ctx.signal_method
        'systemd'

        >>> # User process context
        >>> ctx = resolve_context("run")
        >>> ctx.data_dir
        Path('~/.local/share/memogarden').expanduser()

        >>> # Container context
        >>> ctx = resolve_context("deploy")
        >>> ctx.data_dir
        Path('/data')
        >>> ctx.log_dir
        None

        >>> # Explicit config override
        >>> ctx = resolve_context("run", config_override=Path("/custom/config.toml"))
        >>> ctx.config_dir
        Path('/custom')
    """
    if config_override:
        return RuntimeContext.from_config(config_override, verb)

    if verb == "serve":
        return RuntimeContext(
            verb="serve",
            data_dir=Path("/var/lib/memogarden"),
            config_dir=Path("/etc/memogarden"),
            log_dir=Path("/var/log/memogarden"),
            signal_method="systemd"
        )

    elif verb == "run":
        return RuntimeContext(
            verb="run",
            data_dir=Path.home() / ".local/share/memogarden",
            config_dir=Path.home() / ".config/memogarden",
            log_dir=Path.home() / ".local/state/memogarden/logs",
            signal_method="stdout"
        )

    elif verb == "deploy":
        return RuntimeContext(
            verb="deploy",
            data_dir=Path("/data"),
            config_dir=Path("/config"),
            log_dir=None,  # Container logs to stdout only
            signal_method="none"  # Orchestrator probes /health endpoint
        )

    else:
        raise ValueError(
            f"Invalid verb: {verb}. Must be one of: serve, run, deploy"
        )


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
