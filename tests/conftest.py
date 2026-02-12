"""Pytest fixtures for memogarden-system tests."""

import pytest
import tempfile
from system.core import get_core, init_db as init_core_db


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
    """
    return init_core_db()


@pytest.fixture
def core_with_data():
    """Create a test Core instance with sample Scope data."""
    core = init_core_db()

    # Create a test scope for context
    scope_uuid = core.entity.create(
        entity_type='Scope',
        data={'label': 'Test Scope'}
    )

    return core
