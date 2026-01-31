"""MemoGarden Soil - Immutable Facts Database.

Soil stores immutable Items (facts) and System Relations (structural connections).
Per PRD v0.7.0 - Personal Information System.

ARCHITECTURE:
- Soil owns its connection (no Flask dependency)
- Items are immutable; modifications create new Items with supersession links
- System relations are permanent structural facts
- UUID prefix: "soil_"

ID GENERATION:
All UUIDs are auto-generated UUIDv4 with "soil_" prefix.
"""

from .item import Item, Evidence, generate_soil_uuid, SOIL_UUID_PREFIX, current_day
from .relation import SystemRelation
from .database import Soil, get_soil, create_email_item

__all__ = [
    "Item",
    "Evidence",
    "SystemRelation",
    "Soil",
    "generate_soil_uuid",
    "SOIL_UUID_PREFIX",
    "current_day",
    "get_soil",
    "create_email_item",
]
