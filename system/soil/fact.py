"""Fact dataclasses for MemoGarden Soil."""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

# Constants
SOIL_UUID_PREFIX = "soil_"
EPOCH_DATE = datetime(2020, 1, 1, tzinfo=timezone.utc)


def current_day() -> int:
    """Return days since epoch (2020-01-01)."""
    return (datetime.now(timezone.utc) - EPOCH_DATE).days


def generate_soil_uuid() -> str:
    """Generate a new Soil UUID with prefix."""
    import uuid as uuid_lib
    return f"{SOIL_UUID_PREFIX}{uuid_lib.uuid4()}"


@dataclass
class Evidence:
    """Provenance information for relations."""
    source: str  # 'soil_stated' | 'user_stated' | 'agent_inferred' | 'system_inferred'
    confidence: float | None = None  # 0.0-1.0, for inferred only
    basis: list[str] | None = None  # UUIDs of supporting facts/entities
    method: str | None = None  # For inferred: 'nlp_extraction' | 'pattern_match' | etc.

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class Fact:
    """Base Fact class (immutable fact in Soil)."""
    uuid: str
    _type: str  # 'Note' | 'Message' | 'Email' | 'ToolCall' | 'EntityDelta' | 'SystemEvent'
    realized_at: str  # ISO 8601
    canonical_at: str  # ISO 8601
    integrity_hash: str | None = None
    fidelity: Literal['full', 'summary', 'stub', 'tombstone'] = 'full'
    superseded_by: str | None = None
    superseded_at: str | None = None
    data: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    def compute_hash(self) -> str:
        """Compute SHA256 hash of data fields."""
        data_str = json.dumps(self.data, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(data_str.encode()).hexdigest()
