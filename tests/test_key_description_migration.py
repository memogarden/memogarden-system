"""api#105 system half — api_keys.description (schema 20261129).

name stays the short identifier; description carries the human context.
Additive column (existing rows NULL); fresh seeds carry it at 20261129
(root schemas be191b7); existing DBs converge via the chain walk at the
next core open.
"""

import os
import sqlite3
import tempfile

from system.core import EXPECTED_SCHEMA_VERSION, init_db


def _fresh():
    tmpdir = tempfile.mkdtemp()
    os.environ["MEMOGARDEN_DATA_DIR"] = tmpdir
    try:
        init_db()
        conn = sqlite3.connect(os.path.join(tmpdir, "core.db"))
        return conn, tmpdir
    except BaseException:
        os.environ.pop("MEMOGARDEN_DATA_DIR", None)
        raise


def test_fresh_seed_carries_description():
    conn, tmpdir = _fresh()
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(api_keys)")}
        version = conn.execute(
            "SELECT value FROM _schema_metadata WHERE key='version'"
        ).fetchone()[0]
    finally:
        conn.close()
        os.environ.pop("MEMOGARDEN_DATA_DIR", None)
    assert "description" in cols
    assert version == EXPECTED_SCHEMA_VERSION == "20261129"


def test_migration_converges_existing_db():
    conn, tmpdir = _fresh()
    try:
        conn.execute("UPDATE _schema_metadata SET value='20261128'")
        conn.execute("ALTER TABLE api_keys DROP COLUMN description")
        conn.commit()
        conn.close()

        init_db()  # boot path: chain walk applies the migration
        conn = sqlite3.connect(os.path.join(tmpdir, "core.db"))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(api_keys)")}
        version = conn.execute(
            "SELECT value FROM _schema_metadata WHERE key='version'"
        ).fetchone()[0]
    finally:
        conn.close()
        os.environ.pop("MEMOGARDEN_DATA_DIR", None)
    assert "description" in cols
    assert version == "20261129"
