"""File system operations."""

from pathlib import Path


def resolve_path(path: str | Path) -> Path:
    """Resolve a path to absolute path.

    Args:
        path: Path to resolve (string or Path object)

    Returns:
        Absolute Path object
    """
    return Path(path).resolve()


def ensure_dir(path: str | Path) -> Path:
    """Ensure directory exists, create if not.

    Args:
        path: Path to directory

    Returns:
        Path object for the directory
    """
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p
