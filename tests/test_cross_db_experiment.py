"""Experiment to test if existing context managers provide cross-DB atomicity.

This test explores whether the pattern:
    with get_core() as core, get_soil() as soil:
        # operations on both

provides RFC-008 cross-database transaction guarantees.

RFC-008 Requirements:
1. EXCLUSIVE locks on both databases
2. Commit ordering: Soil first, then Core
3. Failure detection: If Soil commits but Core fails, mark INCONSISTENT
4. Single transaction coordinator (not two independent transactions)
"""


class TestCrossDatabaseAtomicity:
    """Test if existing context managers provide cross-DB atomicity."""

    def test_current_pattern_analysis(self):
        """Analyze the current pattern and its limitations.

        Current pattern:
            with get_core() as core, get_soil() as soil:
                # operations on both

        This creates TWO INDEPENDENT transactions, not one coordinated transaction.
        """
        print("\n=== Current Pattern Analysis ===\n")

        print("Current Implementation:")
        print("  with get_core() as core, get_soil() as soil:")
        print("      # Two separate context managers")
        print("      # Each gets its own connection")
        print("      # Each commits independently on __exit__")
        print()

        print("What happens on success:")
        print("  1. Core.__enter__() → BEGIN (implicit)")
        print("  2. Soil.__enter__() → BEGIN (implicit)")
        print("  3. Operations execute")
        print("  4. Soil.__exit__() → COMMIT or ROLLBACK")
        print("  5. Core.__exit__() → COMMIT or ROLLBACK")
        print("  Order: Soil commits BEFORE Core (context manager exit order)")
        print()

        print("What happens on failure:")
        print("  Scenario 1: Exception before any commit")
        print("    - Both rollback → Clean ✓")
        print()
        print("  Scenario 2: Soil commits, then Core fails")
        print("    - Soil: COMMITTED (changes persist)")
        print("    - Core: ROLLED BACK (changes lost)")
        print("    - Result: INCONSISTENT STATE")
        print("    - Detection: NONE (no tracking)")
        print("    - Recovery: NONE (manual intervention required)")
        print()
        print("  Scenario 3: Core commits, then Soil fails")
        print("    - Core: COMMITTED (state changed)")
        print("    - Soil: ROLLED BACK (audit trail lost)")
        print("    - Result: BROKEN AUDIT TRAIL (worse than inconsistency)")
        print("    - Detection: NONE")
        print("    - Recovery: NONE")
        print()

    def test_what_rfc008_requires(self):
        """Document what RFC-008 requires that current pattern doesn't provide.

        RFC-008 Requirements (from plan/rfc_008_transaction_semantics_v1_2.md):
        """
        print("\n=== RFC-008 Requirements vs Current Implementation ===\n")

        print("1. EXCLUSIVE Lock Coordination:")
        print("   RFC-008: BEGIN EXCLUSIVE on Soil, then BEGIN EXCLUSIVE on Core")
        print("   Current: Each DB gets its own BEGIN (not EXCLUSIVE, not coordinated)")
        print("   Gap: ❌ No coordination, potential for lock ordering issues\n")

        print("2. Commit Ordering:")
        print("   RFC-008: Soil commits FIRST (source of truth), then Core")
        print("   Current: Order depends on context manager exit order (undefined)")
        print("   Gap: ❌ No guaranteed commit ordering\n")

        print("3. Failure Detection:")
        print("   RFC-008: If Soil commits but Core fails, mark system INCONSISTENT")
        print("   Current: No system status tracking, no failure detection")
        print("   Gap: ❌ Inconsistency can go undetected\n")

        print("4. Startup Consistency Checks:")
        print("   RFC-008: Check for orphaned EntityDeltas, broken hash chains")
        print("   Current: No startup checks")
        print("   Gap: ❌ Inconsistencies not detected on startup\n")

        print("5. Single Transaction Coordinator:")
        print("   RFC-008: One transaction() context manager managing both DBs")
        print("   Current: Two independent context managers")
        print("   Gap: ❌ No unified transaction semantics\n")

        print("6. update_entity() with Cross-DB Coordination:")
        print("   RFC-008: Requires explicit transaction, creates EntityDelta in Soil")
        print("   Current: No update_entity() operation, no EntityDelta support")
        print("   Gap: ❌ Missing cross-DB operation entirely\n")

        print("7. Optimistic Locking:")
        print("   RFC-008: Update requires matching based_on_hash")
        print("   Current: No hash chains, no optimistic locking")
        print("   Gap: ❌ No concurrent update detection\n")

    def test_proposed_rfc008_pattern(self):
        """Show what RFC-008 pattern would look like.

        This is a placeholder for what Session 12 should implement.
        """
        print("\n=== Proposed RFC-008 Pattern ===\n")

        print("from system.memogarden import MemoGarden  # New coordinator class")
        print()
        print("# RFC-008 pattern (Session 12):")
        print("with MemoGarden().transaction() as mg:")
        print("    entity = mg.get_entity('core_abc')")
        print("    mg.update_entity('core_abc', {'content': '...'}, based_on_hash=entity.hash)")
        print("    # Single transaction context, locks both DBs, commits Soil then Core")
        print()
        print("# If Soil commits but Core fails:")
        print("# - System marked INCONSISTENT")
        print("# - Orphaned EntityDelta detected on startup")
        print("# - 'memogarden repair' can recover")
        print()
        print("# Current pattern:")
        print("with get_core() as core, get_soil() as soil:")
        print("    # Two independent transactions")
        print("    # No coordination, no failure detection")
        print("    # No guaranteed commit ordering")
        print()

    def test_critical_failure_mode(self):
        """Demonstrate the critical failure mode that RFC-008 addresses.

        The most dangerous failure mode:
        1. Soil commits EntityDelta (audit trail says: entity changed)
        2. Core fails to commit (state doesn't change)
        3. Result: Orphaned EntityDelta, broken hash chain
        """
        print("\n=== Critical Failure Mode ===\n")

        print("Example Timeline:")
        print("  T0: begin_transaction() locks both DBs")
        print("  T1: Write EntityDelta to Soil (staged)")
        print("  T2: Update Entity in Core (staged)")
        print("  T3: COMMIT Soil ✓ (EntityDelta now persists)")
        print("  T4: <POWER LOSS>")
        print("  T5: Core commit never happens")
        print()
        print("Result:")
        print("  - Soil: EntityDelta exists, points to entity with hash H2")
        print("  - Core: Entity still at hash H1")
        print("  - Hash chain: BROKEN (EntityDelta.new_hash != Entity.hash)")
        print("  - Audit trail: INCONSISTENT with actual state")
        print()
        print("Detection (RFC-008):")
        print("  - Startup check finds orphaned EntityDelta")
        print("  - System status: INCONSISTENT")
        print("  - Repair: 'memogarden repair rebuild-core'")
        print()
        print("Detection (Current):")
        print("  - NONE")
        print("  - System appears healthy")
        print("  - Inconsistency silent until manual investigation")
        print()


# ============================================================================
# CONCLUSION
# ============================================================================

"""
CONCLUSION:
===========

The existing context managers (`with get_core() as core, get_soil() as soil`)
DO NOT provide RFC-008 cross-database transaction guarantees.

Key gaps:
1. No EXCLUSIVE lock coordination
2. No guaranteed commit ordering (Soil first, then Core)
3. No failure detection (Soil commits, Core fails → INCONSISTENT)
4. No startup consistency checks
5. No unified transaction coordinator
6. No update_entity() with EntityDelta support
7. No optimistic locking with hash chains

Session 12 IS NEEDED to implement RFC-008 transaction semantics.

What Session 12 should implement:
- MemoGarden class with transaction() context manager
- begin_transaction(): EXCLUSIVE locks on both DBs
- commit_transaction(): Soil first, then Core
- rollback_transaction(): Best-effort rollback on both
- System status tracking (NORMAL/INCONSISTENT/READ_ONLY/SAFE_MODE)
- Startup consistency checks (orphaned deltas, broken hash chains)
- update_entity() operation with cross-DB coordination
- Tests for all failure modes

Estimated time: 2-3 hours

Priority: HIGH (Cross-database operations are central to MemoGarden architecture)

Dependencies:
- Session 6 (audit facts) - EntityDelta schema
- Session 4 (context) - Context frame operations
- Both already complete
"""
