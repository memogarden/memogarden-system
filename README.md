# MemoGarden System

MemoGarden System contains the core business logic for the MemoGarden platform.

## Layers

- **Core**: Mutable belief layer (entities, transactions, user relations)
- **Soil**: Immutable fact layer (items, system relations)
- **Host**: Host platform interface (filesystem, environment, time)
- **Utils**: Shared utilities (uid, isodatetime)

## Installation

```bash
poetry install
```

## Usage

```python
from system.core import Core
from system.soil import Soil

# Open databases
core = Core("data/core.db")
soil = Soil("data/soil.db")
```

## Schema

SQL schemas are bundled in `system/schemas/sql/`:
- `soil.sql` - Soil database schema
- `core.sql` - Core database schema

JSON schemas for type validation are in `system/schemas/types/`:
- `items/` - Item type schemas (Note, Email, Message, etc.)
- `entities/` - Entity type schemas (Transaction, User, etc.)

## Testing

### ⚠️ Standard Test Execution (MUST follow)

**IMPORTANT:** Always run tests from the `memogarden-system` directory to ensure pytest uses the correct configuration from `pyproject.toml`.

```bash
# From project root, change to system directory first
cd memogarden-system

# Then run pytest
poetry run pytest
```

**Standard Commands:**

| Task | Command |
|------|---------|
| Run all tests | `poetry run pytest` |
| Run with verbose output | `poetry run pytest -xvs` |
| Run specific test file | `poetry run pytest tests/test_core.py` |
| Run specific test | `poetry run pytest tests/test_core.py::test_entity_create -xvs` |
| Run with coverage | `poetry run pytest --cov=system --cov-report=html` |
| Stop on first failure | `poetry run pytest -x` |

**Why this matters:** Running from the root directory or without `poetry run` can cause import errors and ensure the correct test environment is used.

### Testing Philosophy

MemoGarden System follows these testing principles:
- **No Mocking**: Tests use real database operations to catch integration issues
- **In-Memory Databases**: Each test uses `:memory:` SQLite for perfect isolation
- **Behavior-Focused**: Tests verify external behavior, not implementation details

See [MemoGarden Testing Philosophy](../memogarden-api/tests/README.md) for detailed patterns and best practices.

