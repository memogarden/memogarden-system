"""
MemoGarden System

Core business logic for MemoGarden platform.
"""

__version__ = "0.1.0"

# Core exports
from system.core import Core, get_core, init_db, init_system

# Soil exports
from system.soil import Soil, Fact, Evidence, SystemRelation, get_soil, generate_soil_uuid

# Type exports
from system.core.types import Timestamp, Date

# Exception exports
from system import exceptions

# Transaction coordinator exports (for consistency checks)
from system.transaction_coordinator import TransactionCoordinator, SystemStatus

__all__ = [
    # Core
    "Core",
    "get_core",
    "init_db",
    "init_system",
    # Soil
    "Soil",
    "Fact",
    "Evidence",
    "SystemRelation",
    "get_soil",
    "generate_soil_uuid",
    # Types
    "Timestamp",
    "Date",
    # Exceptions module (access as system.exceptions.ConflictError, etc.)
    "exceptions",
    # Transaction coordinator
    "TransactionCoordinator",
    "SystemStatus",
]
