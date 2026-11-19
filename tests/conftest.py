"""Pytest fixtures for memogarden-system tests."""

import os

import pytest
import tempfile
import sqlite3
from pathlib import Path
from system.core import get_core, init_db as init_core_db
from system.host.environment import get_db_path


@pytest.fixture(scope="session", autouse=True)
def pinned_db_env(tmp_path_factory):
    """Pin RFC-004 DB path resolution to a session scratch directory.

    Without a pin, the env chain (get_db_path) falls back to the current
    directory: the autouse clean_database_before_tests fixture then
    creates and bulk-DELETEs rows in ./core.db and ./soil.db in the repo
    root on every run, and a developer who exports MEMOGARDEN_DATA_DIR
    would point those DELETEs at their real data directory.

    Session-wide os.environ mutation (not monkeypatch): function-scoped
    tests override these vars with monkeypatch.setenv/delenv, which
    restore the pinned values afterwards.

    See memogarden-system#7 for how env-var path resolution went wrong
    in production code — these tests must not repeat it.
    """
    os.environ["MEMOGARDEN_DATA_DIR"] = str(tmp_path_factory.mktemp("mg-test-data"))
    os.environ.pop("MEMOGARDEN_SOIL_DB", None)
    os.environ.pop("MEMOGARDEN_CORE_DB", None)
    os.environ.pop("DATABASE_PATH", None)
    yield
    os.environ.pop("MEMOGARDEN_DATA_DIR", None)


@pytest.fixture(autouse=True)
def clean_database_before_tests(pinned_db_env):
    """Clean database before each test module to ensure isolation.

    This ensures that data from previous test runs doesn't interfere.
    Runs automatically before all tests in the module.
    """
    init_core_db()  # Initialize Core database

    # Initialize Soil database
    from system.soil import get_soil
    from system.schemas import get_sql_schema

    soil_db_path = get_db_path('soil')
    soil_db_path.parent.mkdir(parents=True, exist_ok=True)

    # Check if Soil database exists and is initialized
    soil_needs_init = False
    if not soil_db_path.exists():
        soil_needs_init = True
    else:
        # Check if schema table exists
        conn = sqlite3.connect(str(soil_db_path))
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='_schema_metadata'"
        )
        if not cursor.fetchone():
            soil_needs_init = True
        conn.close()

    if soil_needs_init:
        with get_soil() as soil:
            soil.init_schema()

    # Clean all items from previous test runs
    with get_soil() as soil:
        soil._get_connection().execute("DELETE FROM item")
        soil._get_connection().commit()

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
