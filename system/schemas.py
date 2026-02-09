"""Schema access utilities for MemoGarden System.

This module provides runtime access to SQL schemas and JSON type schemas
for both Soil and Core layers.

RFC-004 v2 Invariants:
- INV-PKG-004: Try importlib.resources first (bundled package)
- INV-PKG-005: Fall back to file reading (development mode)
- INV-PKG-006: Raise FileNotFoundError if schema not found in either location

USAGE:
    >>> from system.schemas import get_sql_schema, get_type_schema, list_type_schemas
    >>>
    >>> # Get SQL schema content
    >>> core_sql = get_sql_schema('core')
    >>> soil_sql = get_sql_schema('soil')
    >>>
    >>> # Get JSON schema for a specific type
    >>> email_schema = get_type_schema('items', 'Email')
    >>> transaction_schema = get_type_schema('entities', 'Transaction')
    >>>
    >>> # List available schemas
    >>> item_types = list_type_schemas('items')
    >>> entity_types = list_type_schemas('entities')
"""

from __future__ import annotations

import json
from pathlib import Path

# Try importlib.resources for bundled package support (Python 3.13)
try:
    from importlib.resources import files as resource_files
    HAS_RESOURCE_FILES = True
except ImportError:
    HAS_RESOURCE_FILES = False


# ============================================================================
# CONSTANTS
# ============================================================================

VALID_LAYERS = {"soil", "core"}
VALID_CATEGORIES = {"items", "entities"}

# Schema file paths (relative to package root)
SQL_SCHEMA_PATHS = {
    "soil": "system/schemas/sql/soil.sql",
    "core": "system/schemas/sql/core.sql",
}

TYPE_SCHEMA_DIR = {
    "items": "system/schemas/types/items",
    "entities": "system/schemas/types/entities",
}


# ============================================================================
# SQL SCHEMA ACCESS
# ============================================================================

def get_sql_schema(layer: str) -> str:
    """Get SQL schema content for Soil or Core layer.

    Args:
        layer: Database layer - 'soil' or 'core'

    Returns:
        SQL schema content as string

    Raises:
        ValueError: If layer is not 'soil' or 'core'
        FileNotFoundError: If schema file not found in bundled or file locations

    Examples:
        >>> core_sql = get_sql_schema('core')
        >>> soil_sql = get_sql_schema('soil')
    """
    if layer not in VALID_LAYERS:
        raise ValueError(
            f"Invalid layer: {layer!r}. Must be one of: {sorted(VALID_LAYERS)}"
        )

    schema_path = SQL_SCHEMA_PATHS[layer]

    # Try importlib.resources first (bundled package)
    if HAS_RESOURCE_FILES:
        try:
            schema_file = resource_files("system") / "schemas" / "sql" / f"{layer}.sql"
            if schema_file.is_file():
                return schema_file.read_text(encoding="utf-8")
        except (FileNotFoundError, AttributeError):
            # Fall through to file reading
            pass

    # Fall back to file reading (development mode)
    # Try system package location first, then root location
    file_locations = [
        Path(__file__).parent / "schemas" / "sql" / f"{layer}.sql",
        Path(__file__).parent.parent.parent / "schemas" / "sql" / f"{layer}.sql",
    ]

    for file_path in file_locations:
        if file_path.exists():
            return file_path.read_text(encoding="utf-8")

    # Not found anywhere
    raise FileNotFoundError(
        f"SQL schema file not found for layer '{layer}'. "
        f"Searched locations: {[str(p) for p in file_locations]}"
    )


# ============================================================================
# TYPE SCHEMA ACCESS
# ============================================================================

def get_type_schema(category: str, type_name: str) -> dict:
    """Get JSON schema for a specific Item or Entity type.

    Args:
        category: Schema category - 'items' or 'entities'
        type_name: Type name (e.g., 'Email', 'Note', 'Transaction', 'Label')

    Returns:
        JSON schema as Python dict

    Raises:
        ValueError: If category or type_name is invalid
        FileNotFoundError: If schema file not found

    Examples:
        >>> email_schema = get_type_schema('items', 'Email')
        >>> transaction_schema = get_type_schema('entities', 'Transaction')
    """
    if category not in VALID_CATEGORIES:
        raise ValueError(
            f"Invalid category: {category!r}. Must be one of: {sorted(VALID_CATEGORIES)}"
        )

    # Schema filename uses lowercase type name
    schema_filename = f"{type_name.lower()}.schema.json"

    # Try importlib.resources first (bundled package)
    if HAS_RESOURCE_FILES:
        try:
            schema_file = (
                resource_files("system")
                / "schemas"
                / "types"
                / category
                / schema_filename
            )
            if schema_file.is_file():
                content = schema_file.read_text(encoding="utf-8")
                return json.loads(content)
        except (FileNotFoundError, AttributeError, json.JSONDecodeError):
            # Fall through to file reading
            pass

    # Fall back to file reading (development mode)
    file_locations = [
        Path(__file__).parent / "schemas" / "types" / category / schema_filename,
        Path(__file__).parent.parent.parent / "schemas" / "types" / category / schema_filename,
    ]

    for file_path in file_locations:
        if file_path.exists():
            content = file_path.read_text(encoding="utf-8")
            try:
                return json.loads(content)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON in schema file {file_path}: {e}") from e

    # Not found anywhere
    raise FileNotFoundError(
        f"Type schema file not found: category={category!r}, type_name={type_name!r}. "
        f"Searched locations: {[str(p) for p in file_locations]}"
    )


def list_type_schemas(category: str) -> list[str]:
    """List available type schemas in a category.

    Args:
        category: Schema category - 'items' or 'entities'

    Returns:
        List of type names (e.g., ['Email', 'Note', 'Message', 'Action', 'ActionResult'])

    Raises:
        ValueError: If category is invalid
        FileNotFoundError: If schema directory not found

    Examples:
        >>> item_types = list_type_schemas('items')
        >>> entity_types = list_type_schemas('entities')
    """
    if category not in VALID_CATEGORIES:
        raise ValueError(
            f"Invalid category: {category!r}. Must be one of: {sorted(VALID_CATEGORIES)}"
        )

    type_names: set[str] = set()

    # Try importlib.resources first (bundled package)
    if HAS_RESOURCE_FILES:
        try:
            schema_dir = resource_files("system") / "schemas" / "types" / category
            if schema_dir.is_dir():
                for item in schema_dir.iterdir():
                    if item.is_file() and item.name.endswith(".schema.json"):
                        # Read schema to get the correct title from the JSON
                        try:
                            content = item.read_text(encoding="utf-8")
                            schema = json.loads(content)
                            if 'title' in schema:
                                type_names.add(schema['title'])
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            # Fallback to filename-based extraction
                            type_name = item.name.removesuffix(".schema.json")
                            type_names.add(type_name.title())
        except (FileNotFoundError, AttributeError):
            # Fall through to file reading
            pass

    # Fall back to file reading (development mode)
    file_locations = [
        Path(__file__).parent / "schemas" / "types" / category,
        Path(__file__).parent.parent / "schemas" / "types" / category,
    ]

    for dir_path in file_locations:
        if dir_path.exists() and dir_path.is_dir():
            for schema_file in dir_path.glob("*.schema.json"):
                # Try to read schema to get the correct title
                try:
                    content = schema_file.read_text(encoding="utf-8")
                    schema = json.loads(content)
                    if 'title' in schema:
                        type_names.add(schema['title'])
                        continue
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass

                # Fallback to filename-based extraction
                type_name = schema_file.stem.removesuffix(".schema")
                type_names.add(type_name.title())

    if not type_names:
        raise FileNotFoundError(
            f"No type schemas found for category={category!r}. "
            f"Searched locations: {[str(p) for p in file_locations]}"
        )

    return sorted(type_names)
