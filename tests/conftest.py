"""Pytest fixtures for memogarden-system tests."""

import pytest
import tempfile
from system.core import get_core, init_db as init_core_db


@pytest.fixture(autouse=True)
def clean_database_before_tests():
    """Clean database before each test module to ensure isolation.

    This ensures that data from previous test runs doesn't interfere.
    Runs automatically before all tests in the module.
    """
    init_core_db()  # Initialize database
    core = get_core()

    # Clean all entities from previous test runs
    with core:
        core._conn.execute("DELETE FROM entity")
        core._conn.commit()

    yield


@pytest.fixture
def db_core():
    """Create a test Core instance with temporary database.

    Fixture Naming Decision (Session 15):
    - Initially used 'core' as fixture name but encountered pytest conflicts
    - Renamed to 'db_core' to avoid naming collisions
    - Simple approach: direct get_core() call without complex setup
    - Test analysis Option 1: Simplified fixture (rename only, no tempfile)

    Rationale:
    - tempfile setup in original fixture was overkill for entity tests
    - Tests use default database path from get_db_path()
    - conftest.py fixture name 'db_core' indicates database-specific fixture

    Bug Fix (Session 15):
    - init_db() returns None, not a Core object
    - Must call get_core() to get the actual Core instance
    - Core MUST be used as context manager (enforced at runtime)

    Implementation:
    - Uses yield to provide Core as context manager
    - Database initializes on first test run
    - All tests share the same Core instance and database
    - Transaction is rolled back after test for isolation
    """
    core = get_core()
    # Manually manage context to control rollback for test isolation
    core.__enter__()
    try:
        yield core
    finally:
        # Rollback to isolate tests (don't persist test data)
        core._conn.rollback()
        core._conn.close()
        core._in_context = False


@pytest.fixture
def core_with_data():
    """Create a test Core instance with sample Scope data."""
    core = get_core()

    # Manually manage context to control rollback for test isolation
    core.__enter__()
    try:
        # Create a test scope for context
        scope_uuid = core.entity.create(
            entity_type='Scope',
            data='{"label": "Test Scope"}'
        )
        yield core
    finally:
        # Rollback to isolate tests
        core._conn.rollback()
        core._conn.close()
        core._in_context = False
