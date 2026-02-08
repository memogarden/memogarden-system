"""Context frame and view stream operations (RFC-003).

IMPORT CONVENTION:
- Core accesses these through core.context property
- Receives Core reference for coordinating with other operations

CONTEXT MECHANISM (RFC-003):
- ContextFrame: Per-user and per-scope working memory (LRU-N of visited objects)
- View Stream: Append-only record of actions forming a linked-list timeline
- Context Capture: Automatic snapshot of working context into delta metadata

LRU-N MECHANISM:
- N=7 initially (INV-12: LRU-N Limit)
- New visit evicts least-recently-used when at capacity
- Each context maintains independent LRU-N (user's context ≠ scope's context)

VIEW STREAM:
- Views are immutable records with UUID, actions, started/ended timestamps
- Views form temporal linked list via prev pointer (INV-9: Linked List Structure)
- Same View UUID appended to user and all active scopes (INV-2: Synchronized Append)

SUBSTANTIVE VS PRIMITIVE (INV-17, INV-18, INV-19):
- Substantive: Added to context when visited (Artifact, Note, Contact)
- Primitive: NOT added to context (Schema, SystemConfig)
- Classification is type-based, hardcoded for MVP

SCHEMA (v20260130):
- Uses context_frame table in Core DB (legacy, should migrate to entity table)
- Views stored as entities with _type='View' in entity table
- ViewMerge stored as entities with _type='ViewMerge' in entity table

KNOWN LIMITATIONS (M1: Deferred to Session 5 - Context Verbs and Capture):
- view_timeline is tracked in-memory only, not persisted to database
- ContextFrame.view_timeline is always empty when loaded from database
- This violates INV-14 (Cross-Session Persistence) - will be fixed in Session 5
- When implementing view_timeline persistence, add view_timeline column to context_frame table
  and update get_context_frame() to query Views from entity table where _type='View'
"""

import json
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ..exceptions import ResourceNotFound, ValidationError
from ..utils import isodatetime, uid

if TYPE_CHECKING:
    from . import Core

# Configuration (RFC-003)
DEFAULT_CONTEXT_SIZE = 7  # LRU-N limit (INV-12: Tunable N)
CONTEXT_SIZE_MIN = 3
CONTEXT_SIZE_MAX = 20

# View coalescence timeout (INV-23)
VIEW_COALESCENCE_TIMEOUT_SECONDS = 300  # 5 minutes of inactivity

# Substantive vs Primitive classification (INV-17, INV-18, INV-19)
SUBSTANTIVE_TYPES = {
    'Artifact',  # Documents, notes, creative works
    'Note',  # Text notes
    'Contact',  # People/organizations
}

PRIMITIVE_TYPES = {
    'Schema',  # Type definitions
    'SystemConfig',  # Configuration objects
    'ContextFrame',  # Context frames themselves
}


@dataclass
class ViewAction:
    """Individual operation record within a View.

    Per RFC-003, ViewActions track individual operations performed
    within a time window, forming the audit trail within the view-stream.
    """
    type: str  # Operation type (e.g., 'update_entity', 'create_entity')
    target: str  # UUID of target entity or item
    timestamp: str  # ISO 8601 timestamp
    visited: list[str] | None = None  # List of entity UUIDs visited during action

    def to_dict(self) -> dict:
        """Convert ViewAction to dictionary for JSON serialization.

        Handles None values to match JSON schema requirements.
        """
        return {
            'type': self.type,
            'target': self.target,
            'timestamp': self.timestamp,
            'visited': self.visited or []  # Convert None to empty array for schema compliance
        }


@dataclass
class View:
    """Immutable record of actions within a time window.

    Views form the view-stream: a chronological, append-only record of
    actions taken by a user/scope. Multiple views can coalesce into a
    single View within the coalescence timeout (INV-22, INV-23).
    """
    uuid: str  # View entity UUID
    actor: str  # UUID of operator or agent
    actions: list[ViewAction]  # Operations performed
    started_at: str  # ISO 8601 timestamp
    ended_at: str | None  # ISO 8601 timestamp (null if still active)
    prev: str | None  # Previous View UUID (forms linked list)
    context_frame_uuid: str  # ContextFrame this View belongs to

    def to_dict(self) -> dict:
        """Convert View to dictionary for JSON serialization."""
        return {
            'uuid': self.uuid,
            'actor': self.actor,
            'actions': [
                {
                    'type': a.type,
                    'target': a.target,
                    'timestamp': a.timestamp,
                    'visited': a.visited or []
                }
                for a in self.actions
            ],
            'started_at': self.started_at,
            'ended_at': self.ended_at,
            'prev': self.prev,
            'context_frame_uuid': self.context_frame_uuid
        }


@dataclass
class ContextFrame:
    """Per-user and per-scope working memory (LRU-N of visited objects).

    ContextFrames maintain a list of recently visited object UUIDs,
    ordered by recency (most recent first). New visits evict the
    least-recently-used when at capacity (LRU-N eviction).
    """
    uuid: str  # ContextFrame UUID
    owner: str  # UUID of owner (Operator, Agent, or Scope)
    owner_type: Literal['operator', 'agent', 'scope']  # Type of owner
    containers: list[str]  # LRU-N ordered list of visited object UUIDs
    view_timeline: list[str]  # Chronological list of View UUIDs
    created_at: str  # ISO 8601 timestamp
    parent_frame_uuid: str | None = None  # Parent if subordinate context
    is_subordinate: bool = False  # True if forked subordinate context

    def to_dict(self) -> dict:
        """Convert ContextFrame to dictionary for JSON serialization."""
        return {
            'uuid': self.uuid,
            'owner': self.owner,
            'owner_type': self.owner_type,
            'containers': self.containers,
            'view_timeline': self.view_timeline,
            'created_at': self.created_at,
            'parent_frame_uuid': self.parent_frame_uuid,
            'is_subordinate': self.is_subordinate
        }


class ContextOperations:
    """Context frame and view stream operations (RFC-003).

    Provides methods for managing working context (LRU-N of visited objects)
    and view-streams (append-only action records) for users and scopes.
    """

    def __init__(self, conn: sqlite3.Connection, core: "Core | None" = None):
        """Initialize context operations.

        Args:
            conn: SQLite connection with row_factory set to sqlite3.Row
            core: Core reference for coordinating with entity operations
        """
        self._conn = conn
        self._core = core

    # =========================================================================
    # CONTEXT FRAME OPERATIONS
    # =========================================================================

    def get_context_frame(
        self,
        owner: str,
        owner_type: Literal['operator', 'agent', 'scope'],
        create_if_missing: bool = True
    ) -> ContextFrame:
        """Get ContextFrame for an owner, creating if it doesn't exist.

        Args:
            owner: UUID of the owner (Operator, Agent, or Scope)
            owner_type: Type of the owner
            create_if_missing: Create new ContextFrame if not found (default: True)

        Returns:
            ContextFrame for the owner

        Raises:
            ResourceNotFound: If ContextFrame not found and create_if_missing=False
            ValidationError: If owner_type is invalid

        Invariants:
            - INV-20: One Primary Context Per Owner
        """
        # Validate owner_type (L2: Runtime validation)
        valid_types = {'operator', 'agent', 'scope'}
        if owner_type not in valid_types:
            raise ValidationError(
                f"owner_type must be one of {valid_types}, got '{owner_type}'",
                {"owner_type": owner_type}
            )

        # Query existing context frame with proper logic (C2 fix)
        # For operators: project_uuid is NULL
        # For agents/scopes: project_uuid stores owner_type
        if owner_type == 'operator':
            row = self._conn.execute(
                """SELECT * FROM context_frame
                   WHERE participant = ? AND project_uuid IS NULL""",
                (owner,)
            ).fetchone()
        else:
            row = self._conn.execute(
                """SELECT * FROM context_frame
                   WHERE participant = ? AND project_uuid = ?""",
                (owner, owner_type)
            ).fetchone()

        if row:
            # Parse JSON fields
            containers = json.loads(row['containers']) if row['containers'] else []
            return ContextFrame(
                uuid=row['uuid'],
                owner=row['participant'],
                owner_type=owner_type,
                containers=containers,
                view_timeline=[],  # TODO: Query from entity table where _type='View'
                created_at=row['created_at'],
                parent_frame_uuid=row['parent_frame_uuid'],
                is_subordinate=row['parent_frame_uuid'] is not None
            )

        if not create_if_missing:
            raise ResourceNotFound(
                f"ContextFrame for {owner_type} '{owner}' not found",
                {"owner": owner, "owner_type": owner_type}
            )

        # Create new context frame
        return self._create_context_frame(owner, owner_type)

    def _create_context_frame(
        self,
        owner: str,
        owner_type: Literal['operator', 'agent', 'scope'],
        parent_frame_uuid: str | None = None
    ) -> ContextFrame:
        """Create a new ContextFrame for an owner.

        Args:
            owner: UUID of the owner
            owner_type: Type of the owner
            parent_frame_uuid: Optional parent ContextFrame UUID (for subordinate contexts)

        Returns:
            Newly created ContextFrame

        Invariants:
            - INV-5: Fork Inheritance (subordinate gets copy of parent's containers)
        """
        # Generate UUID with core_ prefix (H1: Use uid helper for consistency)
        context_uuid = uid.add_core_prefix(uid.generate_uuid())
        now = isodatetime.now()

        # Inherit containers from parent if subordinate (INV-5)
        if parent_frame_uuid:
            parent = self.get_context_frame_by_uuid(parent_frame_uuid)
            containers = parent.containers.copy()
            is_subordinate = True
        else:
            containers = []
            is_subordinate = False

        # For operators, store NULL in project_uuid; for agents/scopes, store owner_type
        project_uuid_value = None if owner_type == 'operator' else owner_type

        # Insert into database
        self._conn.execute(
            """INSERT INTO context_frame (uuid, project_uuid, participant, containers, created_at, parent_frame_uuid)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (context_uuid, project_uuid_value, owner, json.dumps(containers), now, parent_frame_uuid)
        )

        return ContextFrame(
            uuid=context_uuid,
            owner=owner,
            owner_type=owner_type,
            containers=containers,
            view_timeline=[],
            created_at=now,
            parent_frame_uuid=parent_frame_uuid,
            is_subordinate=is_subordinate
        )

    def get_context_frame_by_uuid(self, context_uuid: str) -> ContextFrame:
        """Get ContextFrame by UUID.

        Args:
            context_uuid: UUID of the ContextFrame

        Returns:
            ContextFrame

        Raises:
            ResourceNotFound: If ContextFrame not found
        """
        # Don't strip prefix - database stores full UUID with prefix
        row = self._conn.execute(
            "SELECT * FROM context_frame WHERE uuid = ?",
            (context_uuid,)
        ).fetchone()

        if not row:
            raise ResourceNotFound(
                f"ContextFrame '{context_uuid}' not found",
                {"context_uuid": context_uuid}
            )

        containers = json.loads(row['containers']) if row['containers'] else []
        owner_type = row['project_uuid'] or 'operator'

        return ContextFrame(
            uuid=row['uuid'],
            owner=row['participant'],
            owner_type=owner_type,
            containers=containers,
            view_timeline=[],
            created_at=row['created_at'],
            parent_frame_uuid=row['parent_frame_uuid'],
            is_subordinate=row['parent_frame_uuid'] is not None
        )

    def update_containers(
        self,
        context_frame: ContextFrame,
        visited_uuid: str,
        context_size: int = DEFAULT_CONTEXT_SIZE
    ) -> ContextFrame:
        """Update containers with new visit, applying LRU-N eviction.

        Args:
            context_frame: The ContextFrame to update
            visited_uuid: UUID of the newly visited object
            context_size: Maximum number of containers (default: DEFAULT_CONTEXT_SIZE)

        Returns:
            Updated ContextFrame

        Invariants:
            - INV-12: LRU-N Limit (containers ≤ N)
            - INV-17: Substantive vs Primitive (filter by type)

        Raises:
            ValidationError: If context_size outside valid range
        """
        # Validate context size
        if not (CONTEXT_SIZE_MIN <= context_size <= CONTEXT_SIZE_MAX):
            raise ValidationError(
                f"context_size must be between {CONTEXT_SIZE_MIN} and {CONTEXT_SIZE_MAX}",
                {"context_size": context_size}
            )

        # Check if entity type is substantive (INV-17)
        if self._core:
            try:
                entity = self._core.entity.get_by_id(visited_uuid)
                entity_type = entity['type']
                if entity_type in PRIMITIVE_TYPES:
                    # Primitive type - don't add to context
                    return context_frame
            except ResourceNotFound:
                # Entity not found - still add to context (may be item UUID)
                pass

        # Update containers: move to front if exists, append if new
        containers = context_frame.containers.copy()
        if visited_uuid in containers:
            # Move to front (most recent)
            containers.remove(visited_uuid)
        containers.insert(0, visited_uuid)

        # Evict least-recently-used if over capacity
        if len(containers) > context_size:
            containers = containers[:context_size]

        # Update in database
        self._conn.execute(
            "UPDATE context_frame SET containers = ? WHERE uuid = ?",
            (json.dumps(containers), context_frame.uuid)
        )

        # Return updated ContextFrame
        context_frame.containers = containers
        return context_frame

    # =========================================================================
    # VIEW STREAM OPERATIONS
    # =========================================================================

    def create_view(
        self,
        context_frame_uuid: str,
        actor: str,
        actions: list[ViewAction],
        prev: str | None = None
    ) -> View:
        """Create a new View record.

        Args:
            context_frame_uuid: UUID of the ContextFrame this View belongs to
            actor: UUID of the operator or agent performing the actions
            actions: List of ViewAction objects
            prev: Optional UUID of previous View (forms linked list)

        Returns:
            Newly created View

        Invariants:
            - INV-1: Unique View UUID
            - INV-9: Linked List Structure (prev pointer)

        Raises:
            ValidationError: If actions list is empty
        """
        # Validate actions not empty
        if not actions:
            raise ValidationError(
                "View must have at least one action",
                {"context_frame_uuid": context_frame_uuid, "actor": actor}
            )

        # Validate context_frame_uuid exists (H3: Add validation)
        try:
            self.get_context_frame_by_uuid(context_frame_uuid)
        except ResourceNotFound:
            raise ValidationError(
                f"ContextFrame '{context_frame_uuid}' does not exist",
                {"context_frame_uuid": context_frame_uuid}
            )

        # Create View entity with proper serialization (H2: Use to_dict())
        view_uuid = self._core.entity.create(
            entity_type='View',
            data=json.dumps({
                'actor': actor,
                'actions': [a.to_dict() for a in actions],  # Fixed: use to_dict() instead of __dict__
                'started_at': actions[0].timestamp,
                'ended_at': None,
                'prev': prev,
                'context_frame_uuid': context_frame_uuid
            })
        )

        # Add core_ prefix to View UUID (H1: Use uid helper for consistency)
        view_uuid = uid.add_core_prefix(view_uuid)

        return View(
            uuid=view_uuid,
            actor=actor,
            actions=actions,
            started_at=actions[0].timestamp,
            ended_at=None,
            prev=prev,
            context_frame_uuid=context_frame_uuid
        )

    def append_view(
        self,
        context_frame: ContextFrame,
        view: View
    ) -> ContextFrame:
        """Append View to ContextFrame's view timeline.

        Args:
            context_frame: The ContextFrame to update
            view: The View to append

        Returns:
            Updated ContextFrame

        Invariants:
            - INV-2: Synchronized Append (atomic update to context_frame table)

        Note:
            For multi-context append (user + active scopes), caller must
            call this for each ContextFrame within a transaction.
        """
        # Get previous View UUID
        prev_view_uuid = context_frame.view_timeline[-1] if context_frame.view_timeline else None

        # Update View's prev pointer
        # TODO: Update View entity in entity table

        # Append to view timeline
        view_timeline = context_frame.view_timeline.copy()
        view_timeline.append(view.uuid)

        # Update in database
        # Note: view_timeline not stored in current schema, tracked in-memory only
        # TODO: Add view_timeline column to context_frame table (M1: Deferred to Session 5)

        # Return updated ContextFrame
        context_frame.view_timeline = view_timeline
        return context_frame

    def append_view_to_contexts(
        self,
        view: View,
        context_frames: list[ContextFrame]
    ) -> None:
        """Append View to multiple ContextFrames atomically (M3: Multi-context helper).

        Per INV-2: Same View UUID appended to all context frames or none.
        This method provides a convenient wrapper for the common case of
        appending a View to both a user's and scope's ContextFrames.

        Args:
            view: The View to append
            context_frames: List of ContextFrames to update

        Invariants:
            - INV-2: Synchronized Append (one View UUID to all context frames)

        Note:
            This should be called within an atomic Core transaction:

                with get_core(atomic=True) as core:
                    core.context.append_view_to_contexts(view, [user_ctx, scope_ctx])

            All updates commit together when the atomic transaction exits.
            If any operation fails, all changes are rolled back.
        """
        for ctx in context_frames:
            self.append_view(ctx, view)
        # All updates commit together when atomic transaction exits

    # =========================================================================
    # HELPER FUNCTIONS
    # =========================================================================

    def is_substantive_type(self, entity_type: str) -> bool:
        """Check if entity type is substantive (adds to context on visit).

        Args:
            entity_type: The entity type to check

        Returns:
            True if substantive, False if primitive

        Invariants:
            - INV-17: Substantive vs Primitive Objects
            - INV-18: Type-Based Classification
            - INV-19: Hardcoded Initial Classification
        """
        return entity_type in SUBSTANTIVE_TYPES

    def is_primitive_type(self, entity_type: str) -> bool:
        """Check if entity type is primitive (doesn't add to context).

        Args:
            entity_type: The entity type to check

        Returns:
            True if primitive, False if substantive
        """
        return entity_type in PRIMITIVE_TYPES
