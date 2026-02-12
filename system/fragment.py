"""Fragment system for Message Items and semantic references.

Session 16: ConversationLog Entity and Fragment System
Implements fragment ID generation, reference parsing, and fragment resolution for Project Studio.
"""

import hashlib
import re
from typing import List, NamedTuple, Optional


class NotImplementedError(Exception):
    """Fragment or artifact line resolution not yet implemented."""
    pass


class ReferenceType:
    """Types of semantic references in Messages.

    Per PRD v0.11.0: Object = Fact | Entity. A UUID reference can point to either
    layer's object, so we use 'object' rather than 'item' or 'entity'."""

    FRAGMENT = "fragment"
    ARTIFACT_LINE = "artifact_line"
    ARTIFACT_LINE_AT_COMMIT = "artifact_line_at_commit"
    OBJECT = "object"  # UUID reference to any Fact or Entity (was 'item')
    LOG = "log"


class Reference(NamedTuple):
    """Parsed semantic reference from Message content."""

    type: str  # ReferenceType constant (e.g., ReferenceType.FRAGMENT)
    span: tuple[int, int]  # (start, end) character offsets
    target: str  # Resolved target string (fragment_id, artifact_uuid, etc.)


class Fragment(NamedTuple):
    """Fragment within a Message Item."""

    id: str  # Fragment ID (e.g., "^abc")
    content: str  # Text content of fragment
    position: int  # Character position in Message content


def generate_fragment_id(content: str) -> str:
    """Generate exactly 3 character base36 hash from content (prefixed with ^).

    From Project Studio spec (§6.2):
    "Fragment IDs are the first 3 characters of the base36 encoding
    of the SHA-256 hash of the fragment content."

    Args:
        content: Text content of fragment

    Returns:
        Fragment ID with format "^abc" (exactly 4 characters: ^ + 3 chars)

    Examples:
        "hello world" → "^a1b"
        "Approve: Use RFC-009" → "^2e8"

    Implementation:
        - Hash content using SHA-256
        - Convert first 2 bytes to base36 integer
        - Zero-pad to exactly 3 characters
        - Prefix with "^" character
    """
    # Hash the content
    hash_bytes = hashlib.sha256(content.encode()).digest()[:2]

    # Convert to base36 integer
    hash_int = int.from_bytes(hash_bytes)

    # Convert to base36 string (0-9, a-z)
    characters = "0123456789abcdefghijklmnopqrstuvwxyz"
    frag_id = ""
    n = hash_int
    if n == 0:
        frag_id = "000"
    else:
        while n > 0:
            frag_id = characters[n % 36] + frag_id
            n = n // 36

    # Zero-pad to exactly 3 characters, take only first 3
    frag_id = frag_id[:3].zfill(3).lower()

    return f"^{frag_id}"


def parse_references(content: str) -> List[Reference]:
    """Parse fragment and artifact references from Message content.

    From Project Studio spec (§6.3):
    - Fragment refs: ^abc
    - Artifact line refs: <label>:<line>[@<commit>]
    - Item refs: @<uuid>
    - Log refs: [<text>](uuid)

    Returns:
        List of parsed Reference objects with type, span, and target

    Implementation:
        - Regex patterns for each reference type
        - Return all non-overlapping matches in character offset order
    """
    references: List[Reference] = []

    # Pattern for fragment references: ^<lowercase-alphanum> (exactly 3 chars)
    frag_pattern = re.compile(r'\^([0-9a-z]{3})')

    # Pattern for artifact line refs: <label>:<line>[@<commit>]
    # Group 1: <label> (artifact identifier)
    # Group 2: :\d+ (line number)
    # Group 3: @hex (optional commit hash, 4+ characters)
    artifact_line_pattern = re.compile(r'([\w_]+):(\d+)(?:@([0-9a-f]{4,})\b)?')

    # Pattern for object refs: @<uuid> (Fact or Entity)
    object_pattern = re.compile(r'@((?:soil|core)_[\w-]+)')

    # Pattern for log refs: [<text>](uuid)
    log_pattern = re.compile(r'\[([^\]]+)\]\(((?:soil|core)_[\w-]+)\)')

    # Find all matches
    for match in frag_pattern.finditer(content):
        ref_type = ReferenceType.FRAGMENT
        references.append(Reference(ref_type, (match.start(), match.end()), match.group()))

    for match in artifact_line_pattern.finditer(content):
        # Check if this includes a commit hash (group 3)
        if match.group(3):
            ref_type_str = ReferenceType.ARTIFACT_LINE_AT_COMMIT
        else:
            ref_type_str = ReferenceType.ARTIFACT_LINE
        references.append(Reference(ref_type_str, (match.start(), match.end()), match.group()))

    for match in object_pattern.finditer(content):
        ref_type = ReferenceType.OBJECT
        # Extract just the UUID without @ prefix (group 1)
        references.append(Reference(ref_type, (match.start(), match.end()), match.group(1)))

    for match in log_pattern.finditer(content):
        ref_type = ReferenceType.LOG
        # Extract just the UUID without link syntax (group 2)
        references.append(Reference(ref_type, (match.start(), match.end()), match.group(2)))

    return references


def resolve_fragment(
    conn,
    scope_uuid: str,
    fragment_id: str,
    conversationlog_uuid: Optional[str] = None
) -> Optional[Fragment]:
    """Find fragment by ID within a scope's conversation threads.

    From Project Studio spec (§6.3):
    "Fragment IDs only apply within artifact (Message content) - they're
    'relative URL anchors' for absolute references to artifact content."

    Therefore, this function:
        1. Queries all ConversationLogs for given scope
        2. For each ConversationLog, gets items from data.items array
        3. Searches each Message item's fragments array
        4. Returns matching Fragment if found
        5. Returns None if not found

    Args:
        scope_uuid: Scope UUID to search within
        fragment_id: Fragment ID to find (e.g., "^abc")
        conversationlog_uuid: Optional specific ConversationLog to limit search (default: all logs)
        conn: Database connection (for queries)

    Returns:
        Fragment with id, content, position if found, None otherwise

    Implementation Note:
        - This is an expensive operation (scans all messages)
        - Future optimization: Add fragment index table for O(1) lookups
    """
    # TODO: Implement cross-database fragment resolution
    # For MVP, fragment resolution is not yet implemented
    raise NotImplementedError("Fragment resolution requires Session 17 ArtifactDelta operations")


def resolve_artifact_line(
    conn,
    artifact_uuid: str,
    line_number: int,
    commit_hash: str | None = None
) -> dict | None:
    """Resolve artifact line reference to actual content.

    From Project Studio spec (§6.3):
    "Artifact line refs: <label>:<line>[@<commit>]"

    This function:
        1. Gets Artifact entity
        2. Finds current or specified commit state
        3. Extracts requested line
        4. Returns content with line number

    Args:
        artifact_uuid: Artifact UUID
        line_number: 1-based line number to retrieve
        commit_hash: Optional commit hash (for @<commit> syntax)
        conn: Database connection

    Returns:
        {"content": str, "line": int} if found, None otherwise

    Implementation:
        - Parse delta ops to find line at commit
        - Apply ops from beginning to current content
        - Extract requested line
    """
    # TODO: Implement when ArtifactDelta operations exist (Session 17)
    # For now, raise error to indicate not yet implemented

    raise NotImplementedError("Artifact line resolution requires Session 17")
