"""Environment variable access."""

import os


def get_env(key: str, default: str | None = None) -> str | None:
    """Get environment variable.

    Args:
        key: Environment variable name
        default: Default value if not found

    Returns:
        Environment variable value or default
    """
    return os.environ.get(key, default)
