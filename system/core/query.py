"""Query builders and utilities.

QUERY BUILDER SCOPE:
Abstract query patterns that are repeated MORE THAN TWICE.
Don't build a full ORM - just helpers for common patterns.
"""

from typing import Any


def build_where_clause(
    conditions: dict[str, Any],
    param_map: dict[str, str] | None = None
) -> tuple[str, list[Any]]:
    """Build dynamic WHERE clause from condition dictionary.

    Args:
        conditions: Dictionary of condition names to values.
                    Values that are None are excluded from the clause.
        param_map: Optional mapping from condition names to SQL fragments.
                   If a condition name is in param_map, use its SQL fragment.
                   Otherwise, default to "{key} = ?" format.

    Returns:
        Tuple of (where_clause, params) where:
        - where_clause: SQL WHERE clause (without "WHERE" keyword)
        - params: List of parameter values for placeholders

    Examples:
        >>> build_where_clause({"name": "John", "age": 30})
        ('name = ? AND age = ?', ['John', 30])

        >>> build_where_clause({"name": "John"}, {"name": "t.name LIKE ?"})
        ('t.name LIKE ?', ['John'])

        >>> build_where_clause({"name": None})
        ('1=1', [])
    """
    where_parts = []
    params = []

    for key, value in conditions.items():
        if value is None:
            continue

        if param_map and key in param_map:
            where_parts.append(param_map[key])
            params.append(value)
        else:
            where_parts.append(f"{key} = ?")
            params.append(value)

    where_clause = " AND ".join(where_parts) if where_parts else "1=1"
    return where_clause, params


def build_update_clause(
    data: dict[str, Any],
    exclude: set[str] | None = None
) -> tuple[str, list[Any]]:
    """Build dynamic UPDATE clause from data dictionary.

    Args:
        data: Dictionary of field names to values.
              Values that are None are excluded from the clause.
        exclude: Set of field names to exclude from the clause.

    Returns:
        Tuple of (update_clause, params) where:
        - update_clause: SQL UPDATE clause (e.g., "field1 = ?, field2 = ?")
        - params: List of parameter values for placeholders

    Examples:
        >>> build_update_clause({"name": "John", "age": 30})
        ('name = ?, age = ?', ['John', 30])

        >>> build_update_clause({"name": "John", "age": None})
        ('name = ?', ['John'])

        >>> build_update_clause({"name": "John", "id": 1}, exclude={"id"})
        ('name = ?', ['John'])
    """
    exclude = exclude or set()
    update_parts = []
    params = []

    for key, value in data.items():
        if value is None or key in exclude:
            continue
        update_parts.append(f"{key} = ?")
        params.append(value)

    update_clause = ", ".join(update_parts)
    return update_clause, params
