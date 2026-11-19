"""Greenfield initialization tests (memogarden-system#7).

Before #7, init_system resolved the soil/coordinator paths via
resolve_context (verb defaults only) while init_db()/get_core() resolved
the core path via the RFC-004 env chain. With MEMOGARDEN_DATA_DIR set, a
greenfield deploy initialized core at the env path but soil at the verb
default — the app then read an empty soil.db at the env path and every
Soil-touching endpoint 500'd ("no such table: item").

These tests pin the contract that was broken: greenfield init_system and
the runtime (get_core/get_soil) must land on the same database files.
"""

import sqlite3
from pathlib import Path

import pytest

from system.core import init_system
from system.host.environment import get_db_path, resolve_context
from system.soil.database import get_soil
from utils.config import default_settings


@pytest.fixture
def fresh_data_dir(tmp_path, monkeypatch):
    """Pin RFC-004 env resolution to a per-test empty directory.

    Every test that runs init_system MUST go through this fixture — an
    unpinned init_system with no env vars resolves soil to the verb
    default (~/.local/share/memogarden), i.e. the operator's live
    database. Layer-specific overrides are cleared so the shared data
    dir wins, and any programmatic core-path override is reset.
    """
    monkeypatch.setenv("MEMOGARDEN_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MEMOGARDEN_SOIL_DB", raising=False)
    monkeypatch.delenv("MEMOGARDEN_CORE_DB", raising=False)
    monkeypatch.setattr(default_settings, "database_path", None)
    return tmp_path


class TestGreenfieldInitSystem:
    """init_system greenfield path creates both DBs where the runtime reads them."""

    def test_greenfield_creates_both_dbs_in_data_dir(self, fresh_data_dir):
        """Fresh data dir: both DBs land in MEMOGARDEN_DATA_DIR, soil gets its schema."""
        info = init_system(verb="run")

        assert info["core_db_path"] == str(fresh_data_dir / "core.db")
        assert info["soil_db_path"] == str(fresh_data_dir / "soil.db")

        conn = sqlite3.connect(str(fresh_data_dir / "soil.db"))
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        conn.close()
        # The tables whose absence 500'd every Soil-touching endpoint.
        assert {"item", "system_relation", "_schema_metadata"} <= tables

        conn = sqlite3.connect(str(fresh_data_dir / "core.db"))
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        conn.close()
        assert "entity" in tables

    def test_greenfield_status_normal_on_fresh_databases(self, fresh_data_dir):
        """A truly fresh pair is normal — the scratch-instance SAFE_MODE was
        the coordinator checking the operator's live DB pair (split-brain),
        not a defect of fresh databases themselves (see also #6)."""
        info = init_system(verb="run")

        assert info["databases_existed"] is False
        assert info["status"].value == "normal"

    def test_greenfield_soil_readable_via_runtime_resolution(self, fresh_data_dir):
        """The endpoint failure mode: get_soil() (the api's runtime path)
        must find a schema-initialized database after greenfield init."""
        init_system(verb="run")

        with get_soil() as soil:  # no explicit path: RFC-004 env resolution
            assert soil.count_items() == 0
            assert soil.count_relations() == 0

    def test_greenfield_idempotent_second_run(self, fresh_data_dir):
        """Second init_system reports existing databases and stays normal."""
        init_system(verb="run")

        info = init_system(verb="run")

        assert info["databases_existed"] is True
        assert info["status"].value == "normal"


class TestContextEnvAgreement:
    """RuntimeContext.get_db_path must agree with get_db_path (INV-PKG-001)."""

    def test_ctx_and_module_db_path_agree_with_data_dir(self, fresh_data_dir):
        """With MEMOGARDEN_DATA_DIR set, both resolvers return the same paths."""
        ctx = resolve_context("run")

        assert ctx.get_db_path("soil") == fresh_data_dir / "soil.db"
        assert ctx.get_db_path("soil") == get_db_path("soil")
        assert ctx.get_db_path("core") == fresh_data_dir / "core.db"
        assert ctx.get_db_path("core") == get_db_path("core")

    def test_layer_env_beats_data_dir_for_both_resolvers(self, fresh_data_dir, monkeypatch):
        """MEMOGARDEN_{LAYER}_DB outranks MEMOGARDEN_DATA_DIR in both resolvers."""
        custom_soil = fresh_data_dir / "custom-soil.db"
        monkeypatch.setenv("MEMOGARDEN_SOIL_DB", str(custom_soil))

        ctx = resolve_context("run")

        assert ctx.get_db_path("soil") == custom_soil
        assert ctx.get_db_path("soil") == get_db_path("soil")
        # core still follows the shared data dir
        assert ctx.get_db_path("core") == fresh_data_dir / "core.db"

    def test_ctx_delegates_without_env(self, tmp_path, monkeypatch):
        """Without env overrides both resolvers use the cwd default
        (INV-PKG-002) — RuntimeContext.get_db_path is pure delegation."""
        monkeypatch.delenv("MEMOGARDEN_DATA_DIR", raising=False)
        monkeypatch.delenv("MEMOGARDEN_SOIL_DB", raising=False)
        monkeypatch.delenv("MEMOGARDEN_CORE_DB", raising=False)
        monkeypatch.chdir(tmp_path)  # cwd default must not touch the repo root

        ctx = resolve_context("run")

        assert ctx.get_db_path("soil") == Path("./soil.db")
        assert ctx.get_db_path("soil") == get_db_path("soil")
        assert ctx.get_db_path("core") == get_db_path("core")

    def test_get_db_path_cwd_default_without_env(self, fresh_data_dir, monkeypatch):
        """get_db_path keeps its INV-PKG-002 cwd default (backward compatible)."""
        monkeypatch.delenv("MEMOGARDEN_DATA_DIR", raising=False)
        monkeypatch.delenv("MEMOGARDEN_SOIL_DB", raising=False)
        monkeypatch.delenv("MEMOGARDEN_CORE_DB", raising=False)

        assert get_db_path("soil") == Path("./soil.db")

    def test_greenfield_without_env_lands_where_runtime_reads(self, tmp_path, monkeypatch):
        """No env vars at all: init must use the runtime's cwd default.

        The same defect class as the data-dir split, one flavor over:
        before the delegation fix, init_system resolved soil to the verb
        default (~/.local/share/memogarden) while get_soil()/init_db()
        used ./{layer}.db — stray initialized DB pairs accumulated in
        repo roots and request-time soil reads hit empty databases.
        """
        monkeypatch.delenv("MEMOGARDEN_DATA_DIR", raising=False)
        monkeypatch.delenv("MEMOGARDEN_SOIL_DB", raising=False)
        monkeypatch.delenv("MEMOGARDEN_CORE_DB", raising=False)
        monkeypatch.setattr(default_settings, "database_path", None)
        monkeypatch.chdir(tmp_path)

        info = init_system(verb="run")

        # cwd-resolved (Path normalizes "./soil.db" to "soil.db")
        assert Path(info["soil_db_path"]).resolve() == (tmp_path / "soil.db").resolve()
        assert Path(info["core_db_path"]).resolve() == (tmp_path / "core.db").resolve()
        conn = sqlite3.connect(str(tmp_path / "soil.db"))
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        conn.close()
        assert {"item", "system_relation", "_schema_metadata"} <= tables

        with get_soil() as soil:  # runtime resolution == init resolution
            assert soil.count_items() == 0


class TestCoreSettingsOverride:
    """init_system's core path must honor settings.database_path like get_core does."""

    def test_init_system_core_path_honors_settings_override(self, fresh_data_dir, monkeypatch):
        """settings.database_path pins core for init AND runtime alike."""
        settings_core = fresh_data_dir / "settings-core.db"
        monkeypatch.setattr(default_settings, "database_path", str(settings_core))

        info = init_system(verb="run")

        assert info["core_db_path"] == str(settings_core)
        assert Path(settings_core).exists()

        conn = sqlite3.connect(str(settings_core))
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        conn.close()
        assert "entity" in tables

        # And the runtime resolves core to the same file init created.
        from system.core import get_core

        with get_core() as core:
            assert core.has_admin_user() is False
