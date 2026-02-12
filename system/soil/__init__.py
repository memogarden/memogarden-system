"""MemoGarden Soil - Immutable Facts Database.

Soil stores immutable Facts and System Relations (structural connections).
Per PRD v0.7.0 - Personal Information System.

ARCHITECTURE:
- Soil owns its connection (no Flask dependency)
- Facts are immutable; modifications create new Facts with supersession links
- System relations are permanent structural facts
- UUID prefix: "soil_"

ID GENERATION:
All UUIDs are auto-generated UUIDv4 with "soil_" prefix.
"""

from .fact import Fact, Evidence, generate_soil_uuid, SOIL_UUID_PREFIX, current_day
from .relation import SystemRelation
from .database import Soil, get_soil, create_email_item

__all__ = [
    "Fact",
    "Evidence",
    "SystemRelation",
    "Soil",
    "generate_soil_uuid",
    "SOIL_UUID_PREFIX",
    "current_day",
    "get_soil",
    "create_email_item",
]
