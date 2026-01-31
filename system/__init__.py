"""
MemoGarden System

Core business logic for MemoGarden platform.
"""

__version__ = "0.1.0"

# Core exports
from system.core import Core, get_core, init_db

# Soil exports
from system.soil import Soil, Item, Evidence, SystemRelation, get_soil, generate_soil_uuid

# Type exports
from system.core.types import Timestamp, Date

__all__ = [
    # Core
    "Core",
    "get_core",
    "init_db",
    # Soil
    "Soil",
    "Item",
    "Evidence",
    "SystemRelation",
    "get_soil",
    "generate_soil_uuid",
    # Types
    "Timestamp",
    "Date",
]
