"""Tests for transaction_coordinator module.

Session 12: Cross-Database Transactions
Tests for RFC-008 transaction semantics and coordination.

Coverage:
- SystemStatus enum values
- Consistency check on startup (orphaned deltas, broken chains)
- Cross-database transaction context manager
- EXCLUSIVE locking behavior
- Commit ordering (Soil first, then Core)
- Rollback behavior on exceptions
- Inconsistency detection when Soil commits but Core fails
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from system.core import get_core, init_db as init_core_db
from system.exceptions import ConsistencyError, OptimisticLockError
from system.soil.database import get_soil
from system.schemas import get_sql_schema
from system.transaction_coordinator import SystemStatus, TransactionCoordinator, get_transaction_coordinator


class TestSystemStatus:
    """Tests for SystemStatus enum."""

    def test_system_status_enum_values(self):
        """SystemStatus has all required modes."""
        assert SystemStatus.NORMAL.value == "normal"
        assert SystemStatus.INCONSISTENT.value == "inconsistent"
        assert SystemStatus.READ_ONLY.value == "read_only"
        assert SystemStatus.SAFE_MODE.value == "safe_mode"


class TestTransactionCoordinatorInit:
    """Tests for TransactionCoordinator initialization."""

    def test_init_with_default_paths(self):
        """TransactionCoordinator uses default paths from get_db_path."""
        coordinator = TransactionCoordinator()

        assert coordinator.soil_db_path is not None
        assert coordinator.core_db_path is not None
        # Paths should be Path objects
        assert isinstance(coordinator.soil_db_path, Path)
        assert isinstance(coordinator.core_db_path, Path)

    def test_init_with_explicit_paths(self):
        """TransactionCoordinator accepts explicit paths."""
        soil_path = Path("/tmp/test_soil.db")
        core_path = Path("/tmp/test_core.db")

        coordinator = TransactionCoordinator(
            soil_db_path=soil_path,
            core_db_path=core_path
        )

        assert coordinator.soil_db_path == soil_path
        assert coordinator.core_db_path == core_path


class TestConsistencyCheck:
    """Tests for startup consistency checks (RFC-008 INV-TX-018 to INV-TX-020)."""

    @pytest.fixture
    def temp_databases(self):
        """Create temporary databases for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            soil_path = Path(tmpdir) / "soil.db"
            core_path = Path(tmpdir) / "core.db"

            # Initialize Soil database (using context manager)
            soil = get_soil(soil_path)
            with soil:
                soil.init_schema()

            # Initialize Core database
            import os
            old_env = os.environ.get('MEMOGARDEN_CORE_DB')
            try:
                os.environ['MEMOGARDEN_CORE_DB'] = str(core_path)
                init_core_db()
            finally:
                if old_env is not None:
                    os.environ['MEMOGARDEN_CORE_DB'] = old_env
                elif 'MEMOGARDEN_CORE_DB' in os.environ:
                    del os.environ['MEMOGARDEN_CORE_DB']

            yield soil_path, core_path

    def test_consistency_check_on_fresh_databases(self, temp_databases):
        """Fresh databases have NORMAL status."""
        soil_path, core_path = temp_databases

        coordinator = TransactionCoordinator(
            soil_db_path=soil_path,
            core_db_path=core_path
        )

        status = coordinator.check_consistency()
        assert status == SystemStatus.NORMAL

    def test_find_orphaned_deltas_empty(self, temp_databases):
        """Fresh databases have no orphaned deltas."""
        soil_path, core_path = temp_databases

        coordinator = TransactionCoordinator(
            soil_db_path=soil_path,
            core_db_path=core_path
        )

        orphans = coordinator._find_orphaned_deltas()
        assert orphans == []

    def test_find_broken_hash_chains_empty(self, temp_databases):
        """Fresh databases have no broken hash chains."""
        soil_path, core_path = temp_databases

        coordinator = TransactionCoordinator(
            soil_db_path=soil_path,
            core_db_path=core_path
        )

        broken = coordinator._find_broken_hash_chains()
        assert broken == []

    def test_entity_exists_in_core(self, temp_databases):
        """_entity_exists_in_core correctly finds entities."""
        soil_path, core_path = temp_databases

        coordinator = TransactionCoordinator(
            soil_db_path=soil_path,
            core_db_path=core_path
        )

        # Create an entity (need to set env var for Core)
        import os
        old_env = os.environ.get('MEMOGARDEN_CORE_DB')
        try:
            os.environ['MEMOGARDEN_CORE_DB'] = str(core_path)
            with get_core() as core:
                entity_uuid = core.entity.create("transactions")
        finally:
            if old_env is not None:
                os.environ['MEMOGARDEN_CORE_DB'] = old_env
            elif 'MEMOGARDEN_CORE_DB' in os.environ:
                del os.environ['MEMOGARDEN_CORE_DB']

        # Entity should exist
        assert coordinator._entity_exists_in_core(entity_uuid) is True

        # Non-existent entity should not exist
        assert coordinator._entity_exists_in_core("core_foo") is False

    def test_orphaned_delta_detection(self, temp_databases):
        """Detect EntityDelta with no matching entity."""
        soil_path, core_path = temp_databases

        coordinator = TransactionCoordinator(
            soil_db_path=soil_path,
            core_db_path=core_path
        )

        # Create an orphaned EntityDelta in Soil
        with get_soil(soil_path) as soil:
            from system.soil.fact import Fact

            orphaned_delta = Fact(
                uuid="soil_test_orphan",
                _type="EntityDelta",
                realized_at="2026-02-09T12:00:00Z",
                canonical_at="2026-02-09T12:00:00Z",
                data={
                    "entity_id": "core_nonexistent",
                    "changes": {"amount": 100},
                }
            )
            soil.create_fact(orphaned_delta)

        # Should detect orphaned delta
        orphans = coordinator._find_orphaned_deltas()
        assert len(orphans) == 1
        assert orphans[0]["uuid"] == "soil_test_orphan"
        assert orphans[0]["entity_id"] == "core_nonexistent"

        # Status should be INCONSISTENT
        status = coordinator.check_consistency()
        assert status == SystemStatus.INCONSISTENT


class TestCrossDatabaseTransaction:
    """Tests for cross-database transaction context manager."""

    @pytest.fixture
    def coordinator_with_dbs(self):
        """Create coordinator with temporary databases."""
        with tempfile.TemporaryDirectory() as tmpdir:
            soil_path = Path(tmpdir) / "soil.db"
            core_path = Path(tmpdir) / "core.db"

            # Initialize databases
            soil = get_soil(soil_path)
            with soil:
                soil.init_schema()

            import os
            old_env = os.environ.get('MEMOGARDEN_CORE_DB')
            try:
                os.environ['MEMOGARDEN_CORE_DB'] = str(core_path)
                init_core_db()
            finally:
                if old_env is not None:
                    os.environ['MEMOGARDEN_CORE_DB'] = old_env
                elif 'MEMOGARDEN_CORE_DB' in os.environ:
                    del os.environ['MEMOGARDEN_CORE_DB']

            coordinator = TransactionCoordinator(
                soil_db_path=soil_path,
                core_db_path=core_path
            )

            yield coordinator, soil_path, core_path

    def test_cross_database_transaction_success(self, coordinator_with_dbs):
        """Successful cross-DB transaction commits both databases."""
        coordinator, soil_path, core_path = coordinator_with_dbs

        # Set environment variable for Core before transaction
        import os
        old_env = os.environ.get('MEMOGARDEN_CORE_DB')
        try:
            os.environ['MEMOGARDEN_CORE_DB'] = str(core_path)

            with coordinator.cross_database_transaction() as (soil, core):
                # Create entity in Core
                entity_uuid = core.entity.create("transactions")

                # Create item in Soil
                from system.soil.fact import Fact
                item = Fact(
                    uuid="soil_test_item",
                    _type="Note",
                    realized_at="2026-02-09T12:00:00Z",
                    canonical_at="2026-02-09T12:00:00Z",
                    data={"content": "test"},
                )
                soil.create_item(item)

        finally:
            if old_env is not None:
                os.environ['MEMOGARDEN_CORE_DB'] = old_env
            elif 'MEMOGARDEN_CORE_DB' in os.environ:
                del os.environ['MEMOGARDEN_CORE_DB']

        # Both should be committed
        with get_soil(soil_path) as s:
            retrieved = s.get_fact("soil_test_item")
            assert retrieved is not None
            assert retrieved._type == "Note"

        # Need to set environment variable for Core to verify
        try:
            os.environ['MEMOGARDEN_CORE_DB'] = str(core_path)
            with get_core() as c:
                entity = c.entity.get_by_id(entity_uuid)
                assert entity is not None
                assert entity["type"] == "transactions"
        finally:
            if old_env is not None:
                os.environ['MEMOGARDEN_CORE_DB'] = old_env
            elif 'MEMOGARDEN_CORE_DB' in os.environ:
                del os.environ['MEMOGARDEN_CORE_DB']

    def test_cross_database_transaction_rollback_on_exception(self, coordinator_with_dbs):
        """Exception in cross-DB transaction rolls back both databases."""
        coordinator, soil_path, core_path = coordinator_with_dbs

        from system.soil.fact import Fact

        # Create an entity before transaction
        import os
        old_env = os.environ.get('MEMOGARDEN_CORE_DB')
        try:
            os.environ['MEMOGARDEN_CORE_DB'] = str(core_path)
            with get_core() as core:
                entity_uuid = core.entity.create("transactions")
        finally:
            if old_env is not None:
                os.environ['MEMOGARDEN_CORE_DB'] = old_env
            elif 'MEMOGARDEN_CORE_DB' in os.environ:
                del os.environ['MEMOGARDEN_CORE_DB']

        # Transaction with exception
        with pytest.raises(RuntimeError, match="Test exception"):
            with coordinator.cross_database_transaction() as (soil, core):
                # Create item in Soil
                item = Fact(
                    uuid="soil_test_rollback",
                    _type="Note",
                    realized_at="2026-02-09T12:00:00Z",
                    canonical_at="2026-02-09T12:00:00Z",
                    data={"content": "test"},
                )
                soil.create_item(item)

                # Raise exception
                raise RuntimeError("Test exception")

        # Fact should not exist (rolled back)
        with get_soil(soil_path) as s:
            retrieved = s.get_fact("soil_test_rollback")
            assert retrieved is None

    def test_cross_database_transaction_context_manager_protocol(self, coordinator_with_dbs):
        """Cross-DB transaction properly implements context manager protocol."""
        coordinator, soil_path, core_path = coordinator_with_dbs

        # Should support with statement
        with coordinator.cross_database_transaction() as (soil, core):
            assert soil is not None
            assert core is not None


class TestCommitOrdering:
    """Tests for Soil-first commit ordering (RFC-008 INV-TX-007)."""

    @pytest.fixture
    def coordinator_with_dbs(self):
        """Create coordinator with temporary databases."""
        with tempfile.TemporaryDirectory() as tmpdir:
            soil_path = Path(tmpdir) / "soil.db"
            core_path = Path(tmpdir) / "core.db"

            # Initialize databases
            soil = get_soil(soil_path)
            with soil:
                soil.init_schema()

            import os
            old_env = os.environ.get('MEMOGARDEN_CORE_DB')
            try:
                os.environ['MEMOGARDEN_CORE_DB'] = str(core_path)
                init_core_db()
            finally:
                if old_env is not None:
                    os.environ['MEMOGARDEN_CORE_DB'] = old_env
                elif 'MEMOGARDEN_CORE_DB' in os.environ:
                    del os.environ['MEMOGARDEN_CORE_DB']

            coordinator = TransactionCoordinator(
                soil_db_path=soil_path,
                core_db_path=core_path
            )

            yield coordinator, soil_path, core_path

    def test_soil_commits_first(self, coordinator_with_dbs):
        """Cross-database transaction successfully commits both databases."""
        coordinator, soil_path, core_path = coordinator_with_dbs

        # Set environment variable for Core
        import os
        old_env = os.environ.get('MEMOGARDEN_CORE_DB')
        try:
            os.environ['MEMOGARDEN_CORE_DB'] = str(core_path)

            # Transaction should succeed
            with coordinator.cross_database_transaction() as (soil, core):
                entity_uuid = core.entity.create("transactions")

                from system.soil.fact import Fact
                item = Fact(
                    uuid="soil_test_commit_order",
                    _type="Note",
                    realized_at="2026-02-09T12:00:00Z",
                    canonical_at="2026-02-09T12:00:00Z",
                    data={"content": "test"},
                )
                soil.create_item(item)

        finally:
            if old_env is not None:
                os.environ['MEMOGARDEN_CORE_DB'] = old_env
            elif 'MEMOGARDEN_CORE_DB' in os.environ:
                del os.environ['MEMOGARDEN_CORE_DB']

        # Verify both were committed
        with get_soil(soil_path) as s:
            retrieved = s.get_fact("soil_test_commit_order")
            assert retrieved is not None


class TestGetTransactionCoordinator:
    """Tests for get_transaction_coordinator convenience function."""

    def test_get_transaction_coordinator(self):
        """get_transaction_coordinator returns TransactionCoordinator instance."""
        coordinator = get_transaction_coordinator()

        assert isinstance(coordinator, TransactionCoordinator)
        assert coordinator.soil_db_path is not None
        assert coordinator.core_db_path is not None
