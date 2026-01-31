"""Host interface for MemoGarden System.

Provides abstractions for host platform operations (filesystem, environment, time).
This allows the system to work across different host environments.
"""

from .filesystem import resolve_path, ensure_dir
from .environment import get_env
from .time import now_utc, now_iso

__all__ = [
    "resolve_path",
    "ensure_dir",
    "get_env",
    "now_utc",
    "now_iso",
]
