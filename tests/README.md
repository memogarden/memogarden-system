# MemoGarden System Tests

Testing approach and patterns for MemoGarden System unit and integration tests.

## Testing Philosophy

### No Mocking
Tests use real `system` code without mocking. This ensures tests catch real integration issues that mocked tests would miss.

### In-Memory Database
Each test gets a fresh `:memory:` SQLite database for perfect isolation:
- No shared state between tests
- No database locking issues
- Fast execution (no disk I/O)
- Automatic cleanup (no manual state reset needed)

### Behavior-Focused
Tests verify external behavior (API contracts, database operations) rather than implementation details. This makes tests more maintainable and resilient to refactoring.

## Test Organization

### Directory Structure

```
tests/
├── README.md           # This file
├── conftest.py         # Shared pytest fixtures
├── test_core.py        # Core layer tests
├── test_soil.py        # Soil layer tests
├── test_utils.py       # Utility function tests
└── test_integration.py # Cross-layer integration tests
```

### Test Categories

1. **Unit Tests**: Test individual methods and classes
   - Example: `test_entity_create_with_valid_data`
   - Focus: Single function/class behavior

2. **Integration Tests**: Test interactions between layers
   - Example: `test_core_and_soil_transaction_commit`
   - Focus: Cross-layer functionality

3. **Schema Tests**: Verify database schema integrity
   - Example: `test_core_schema_foreign_keys`
   - Focus: Database constraints and migrations

## Running Tests

### ⚠️ Standard Test Execution (MUST follow)

**IMPORTANT:** Always use the standardized `run_tests.sh` script for test execution. This ensures consistent behavior across environments and provides grep-able output for automation.

```bash
# From project root
./memogarden-system/run_tests.sh

# Or change to system directory first
cd memogarden-system && ./run_tests.sh
```

**Standard Commands:**

| Task | Command |
|------|---------|
| Run all tests | `./run_tests.sh` |
| Run with verbose output | `./run_tests.sh -xvs` |
| Run specific test file | `./run_tests.sh tests/test_core.py` |
| Run specific test | `./run_tests.sh tests/test_core.py::test_entity_create -xvs` |
| Run with coverage | `./run_tests.sh --cov=system --cov-report=html` |
| Stop on first failure | `./run_tests.sh -x` |
| Get summary only (for agents) | `./run_tests.sh --tb=no -q 2>&1 | tail -n 7` |

**Why use run_tests.sh:**
- Ensures correct Poetry environment is used
- Works from any directory (changes to project dir automatically)
- Provides grep-able output with test run ID and summary
- Last 7 lines always contain summary (use `tail -n 7` for quick status check)

**For quick status check (agents):**
```bash
# Get just the summary (7 lines) without full test output
./run_tests.sh --tb=no -q 2>&1 | tail -n 7
```

**Example output summary:**
```
╔═══════════════════════════════════════════════════════════╗
║  Test Summary                                               ║
╠═══════════════════════════════════════════════════════════╣
║  Status: PASSED                                            ║
║  Tests: 165 passed                                      ║
║  Duration: 8.14s                                        ║
║  Test Run ID: 20260213-064712                                  ║
╚═══════════════════════════════════════════════════════════╝
```

## Writing Tests

### Test Structure

```python
def test_feature_description(core):
    """
    Brief description of what's being tested.
    """
    # Setup: Create test data
    entity_uuid = core.entity.create(
        entity_type="Transaction",
        data='{"amount": 100}'
    )

    # Exercise: Test the feature
    entity = core.entity.get_by_id(entity_uuid)

    # Verify: Check expected behavior
    assert entity is not None
    assert entity["type"] == "Transaction"
    assert entity["uuid"] == entity_uuid
```

### Test Naming Convention

- Use descriptive names: `test_verb_expected_outcome`
- Examples:
  - `test_entity_create_returns_uuid`
  - `test_entity_get_by_id_returns_correct_data`
  - `test_entity_update_modifies_data_field`

### Fixtures

Common fixtures will be added to `conftest.py`:

| Fixture | Purpose | Scope |
|---------|---------|-------|
| `core` | Core instance with in-memory database | `function` (per test) |
| `soil` | Soil instance with in-memory database | `function` (per test) |
| `db_conn` | Direct database connection | `function` (per test) |

Example fixture:
```python
@pytest.fixture(scope="function")
def core():
    """Create a Core instance with in-memory database for testing."""
    from system.core import Core
    from system.schemas.sql import core_sql

    # Create in-memory database
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # Initialize schema
    conn.executescript(core_sql)
    conn.commit()

    # Create Core instance
    core_instance = Core(conn, atomic=False)

    yield core_instance

    # Cleanup
    conn.close()
```

### Assertions

- **UUIDs**: Verify UUID format and prefixes (`core_`, `soil_`)
- **Types**: Check entity/item types match expected values
- **Data integrity**: Verify JSON data is parsed correctly
- **Database state**: Check row counts, foreign keys, constraints
- **Invariants**: Test RFC invariants explicitly (e.g., one context per owner)

## Test Coverage Goals

### Current Coverage
- System tests: ⏳ Not yet implemented
- Target: >80% coverage for core business logic

### Coverage Areas
- Core API (entity, transaction, relation operations)
- Soil API (item, relation operations)
- Utils (uid, isodatetime, hash computation)
- Schema migrations and constraints

## Common Patterns

### Testing Database Operations

```python
def test_entity_create_persists_to_database(core):
    """Test that entity creation persists to database."""
    # Create entity
    uuid = core.entity.create("Transaction")

    # Verify in database
    cursor = core._conn.execute(
        "SELECT * FROM entity WHERE uuid = ?",
        (uuid,)
    )
    row = cursor.fetchone()

    assert row is not None
    assert row["type"] == "Transaction"
    assert row["uuid"] == uuid
```

### Testing Error Handling

```python
def test_entity_get_by_id_not_found(core):
    """Test that getting non-existent entity raises error."""
    from system.exceptions import ResourceNotFound

    with pytest.raises(ResourceNotFound):
        core.entity.get_by_id("nonexistent_uuid")
```

### Testing JSON Data Fields

```python
def test_entity_data_field_stores_json(core):
    """Test that entity.data field stores and retrieves JSON correctly."""
    import json

    data = {"amount": 100, "category": "groceries"}
    uuid = core.entity.create(
        entity_type="Transaction",
        data=json.dumps(data)
    )

    entity = core.entity.get_by_id(uuid)
    retrieved_data = json.loads(entity["data"])

    assert retrieved_data == data
```

## References

- [Testing Architecture](../../memogarden-core/docs/architecture.md#testing) - Core testing principles
- [MemoGarden API Tests](../memogarden-api/tests/README.md) - API test patterns
- [RFC-003 Context Mechanism](../../plan/rfc_003_context_mechanism_v4.md) - Context invariants
- [RFC-005 Semantic API](../../plan/rfc_005_semantic_api_v7.md) - API specification

---

**Last Updated:** 2026-02-08 (Session 5: Established test execution standards)
