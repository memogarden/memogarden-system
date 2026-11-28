"""Migration 20261124 -> 20261128 — provider_runs tagging columns.

provider_runs gains triggered_by_key (the authenticating key's name) and
external_app_id (a verbatim app-supplied id from the X-External-App-Id
header). Additive columns; existing rows stay NULL — history is not
rewritten. Fresh seeds carry them directly at 20261128 (root schemas
e64d2ae).
"""

import sqlite3
import tempfile
from pathlib import Path

from system.core import EXPECTED_SCHEMA_VERSION, init_db
from system.schemas import get_sql_schema


def test_fresh_core_seed_carries_the_columns():
    with tempfile.TemporaryDirectory() as tmpdir:
        import os

        os.environ["MEMOGARDEN_DATA_DIR"] = tmpdir
        # init_db resolves MEMOGARDEN_DATA_DIR via get_db_path
        try:
            init_db()
            conn = sqlite3.connect(os.path.join(tmpdir, "core.db"))
            cols = {row[1] for row in conn.execute("PRAGMA table_info(provider_runs)")}
            version = conn.execute(
                "SELECT value FROM _schema_metadata WHERE key='version'"
            ).fetchone()[0]
        finally:
            os.environ.pop("MEMOGARDEN_DATA_DIR", None)
    assert {"triggered_by_key", "external_app_id"} <= cols
    assert version == EXPECTED_SCHEMA_VERSION


def test_migration_converges_existing_db():
    """An existing 20261124 database gains the columns at the next open
    (the deploy path: _run_migrations walks the chain at init)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        import os

        os.environ["MEMOGARDEN_DATA_DIR"] = tmpdir
        try:
            init_db()
            conn = sqlite3.connect(os.path.join(tmpdir, "core.db"))
            conn.execute("UPDATE _schema_metadata SET value='20261124'")
            # strip everything the chain walk re-adds: the 20261128 tagging
            # columns and the 20261129 description — a real 20261124 DB
            # never carried any of them
            conn.execute("ALTER TABLE provider_runs DROP COLUMN triggered_by_key")
            conn.execute("ALTER TABLE provider_runs DROP COLUMN external_app_id")
            conn.execute("ALTER TABLE api_keys DROP COLUMN description")
            conn.commit()
            conn.close()

            init_db()  # the boot path: walks the chain, applies the migration
            conn = sqlite3.connect(os.path.join(tmpdir, "core.db"))
            cols = {row[1] for row in conn.execute("PRAGMA table_info(provider_runs)")}
            version = conn.execute(
                "SELECT value FROM _schema_metadata WHERE key='version'"
            ).fetchone()[0]
        finally:
            os.environ.pop("MEMOGARDEN_DATA_DIR", None)
    assert {"triggered_by_key", "external_app_id"} <= cols
    assert version == EXPECTED_SCHEMA_VERSION
