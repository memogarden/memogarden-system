"""Utility functions for MemoGarden System.

**DEPRECATED:** Import from utils instead. This module re-exports for compatibility.

This module now re-exports from the memogarden-utils package for backward
compatibility during the system boundary refactor. New code should import
directly from the utils package:

    from utils import datetime, uid, secret, recurrence
    timestamp = datetime.now()
    date_str = datetime.to_datestring(some_date)
    uuid = uid.generate()
    api_key = secret.generate_api_key()
    occurrences = recurrence.generate_occurrences(rrule, start, end)

For compatibility during the transition, existing imports continue to work:

    from system.utils import isodatetime, uid, secret, recurrence
"""

# Re-export from utils package for backward compatibility
# Phase 2: system.utils is now a compatibility shim
from utils import datetime as isodatetime
from utils import uid, secret, hash_chain, recurrence
from utils import time

__all__ = ["isodatetime", "secret", "time", "uid", "recurrence", "hash_chain"]
