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
