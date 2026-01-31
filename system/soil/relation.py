"""SystemRelation dataclass for MemoGarden Soil."""

from dataclasses import dataclass


@dataclass
class SystemRelation:
    """System relation (immutable structural fact)."""
    uuid: str
    kind: str  # 'triggers' | 'cites' | 'derives_from' | 'contains' | 'replies_to' | 'continues' | 'supersedes'
    source: str  # UUID of source
    source_type: str  # 'item' | 'entity'
    target: str  # UUID of target
    target_type: str  # 'item' | 'entity'
    created_at: int  # Days since epoch
    evidence: object | dict | None = None
    metadata: dict | None = None
