"""Tests for RFC-004 deployment features (Session 14).

Tests for:
- resolve_context() function (RFC-004 Section 4.1)
- RuntimeContext dataclass (RFC-004 Section 4.1)
- Environment variable precedence (RFC-004 Section 5.3)
"""

import os
import pytest
from pathlib import Path

from system.host.environment import resolve_context, get_db_path
from utils.config import Settings, ResourceProfile


# ============================================================================
# RuntimeContext Tests (RFC-004 Section 4.1)
# ============================================================================

class TestRuntimeContext:
    """Tests for RuntimeContext dataclass."""

    def test_runtime_context_serve_defaults(self):
        """Test RuntimeContext for 'serve' verb has correct defaults."""
        ctx = resolve_context("serve")

        assert ctx.verb == "serve"
        assert ctx.data_dir == Path("/var/lib/memogarden")
        assert ctx.config_dir == Path("/etc/memogarden")
        assert ctx.log_dir == Path("/var/log/memogarden")
        assert ctx.signal_method == "systemd"

    def test_runtime_context_run_defaults(self):
        """Test RuntimeContext for 'run' verb has correct defaults."""
        ctx = resolve_context("run")

        assert ctx.verb == "run"
        assert ctx.data_dir == Path.home() / ".local/share/memogarden"
        assert ctx.config_dir == Path.home() / ".config/memogarden"
        assert ctx.log_dir == Path.home() / ".local/state/memogarden/logs"
        assert ctx.signal_method == "stdout"

    def test_runtime_context_deploy_defaults(self):
        """Test RuntimeContext for 'deploy' verb has correct defaults."""
        ctx = resolve_context("deploy")

        assert ctx.verb == "deploy"
        assert ctx.data_dir == Path("/data")
        assert ctx.config_dir == Path("/config")
        assert ctx.log_dir is None
        assert ctx.signal_method == "none"

    def test_runtime_context_get_db_path(self, monkeypatch):
        """Test RuntimeContext.get_db_path() delegates to the canonical resolver.

        Before memogarden-system#7 this method applied the verb default
        (data_dir/{db_name}.db) and ignored the env chain — greenfield
        init then landed on different files than the runtime reads.
        """
        for var in ("MEMOGARDEN_DATA_DIR", "MEMOGARDEN_SOIL_DB", "MEMOGARDEN_CORE_DB"):
            monkeypatch.delenv(var, raising=False)
        ctx = resolve_context("serve")

        soil_path = ctx.get_db_path("soil")
        core_path = ctx.get_db_path("core")

        assert soil_path == get_db_path("soil")
        assert core_path == get_db_path("core")

    def test_runtime_context_get_config_path(self):
        """Test RuntimeContext.get_config_path() returns correct path."""
        ctx = resolve_context("run")

        config_path = ctx.get_config_path()

        assert config_path == Path.home() / ".config/memogarden/config.toml"

    def test_runtime_context_get_log_path(self):
        """Test RuntimeContext.get_log_path() returns correct path."""
        ctx = resolve_context("serve")

        log_path = ctx.get_log_path("app.log")

        assert log_path == Path("/var/log/memogarden/app.log")

    def test_runtime_context_deploy_no_log_dir(self):
        """Test RuntimeContext for 'deploy' returns None for log paths."""
        ctx = resolve_context("deploy")

        log_path = ctx.get_log_path("app.log")

        assert log_path is None


class TestResolveContext:
    """Tests for resolve_context() function."""

    def test_resolve_context_serve(self):
        """Test resolve_context('serve') returns correct context."""
        ctx = resolve_context("serve")

        assert ctx.verb == "serve"
        assert ctx.signal_method == "systemd"

    def test_resolve_context_run(self):
        """Test resolve_context('run') returns correct context."""
        ctx = resolve_context("run")

        assert ctx.verb == "run"
        assert ctx.signal_method == "stdout"

    def test_resolve_context_deploy(self):
        """Test resolve_context('deploy') returns correct context."""
        ctx = resolve_context("deploy")

        assert ctx.verb == "deploy"
        assert ctx.signal_method == "none"
        assert ctx.log_dir is None

    def test_resolve_context_with_config_override(self, tmp_path):
        """Test resolve_context() respects config_override parameter."""
        custom_config = tmp_path / "custom" / "config.toml"
        ctx = resolve_context("run", config_override=custom_config)

        assert ctx.config_dir == custom_config.parent
        assert ctx.verb == "run"

    def test_resolve_context_invalid_verb(self):
        """Test resolve_context() raises ValueError for invalid verb."""
        with pytest.raises(ValueError, match="Invalid verb"):
            resolve_context("invalid")


class TestContextResolutionPerVerb:
    """Parametrized tests for each verb (RFC-004 Section 9.3)."""

    @pytest.mark.parametrize("verb,expected_signal", [
        ("serve", "systemd"),
        ("run", "stdout"),
        ("deploy", "none"),
    ])
    def test_context_resolution_per_verb(self, verb, expected_signal):
        """Test context resolution per verb (RFC-004 Section 9.3)."""
        ctx = resolve_context(verb)
        assert ctx.signal_method == expected_signal


# ============================================================================
# Environment Variable Precedence Tests (RFC-004 Section 5.3)
# ============================================================================

class TestEnvironmentVariablePrecedence:
    """Tests for environment variable precedence (env var > TOML > default)."""

    def test_resource_profile_from_env_var(self, monkeypatch):
        """Test MEMOGARDEN_RESOURCE_PROFILE environment variable."""
        monkeypatch.setenv("MEMOGARDEN_RESOURCE_PROFILE", "embedded")

        settings = Settings()

        assert settings.max_view_entries == 100
        assert settings.max_search_results == 20

    def test_bind_address_from_env_var(self, monkeypatch):
        """Test MEMOGARDEN_BIND_ADDRESS environment variable."""
        monkeypatch.setenv("MEMOGARDEN_BIND_ADDRESS", "0.0.0.0")

        settings = Settings()

        assert settings.bind_address == "0.0.0.0"

    def test_bind_port_from_env_var(self, monkeypatch):
        """Test MEMOGARDEN_BIND_PORT environment variable."""
        monkeypatch.setenv("MEMOGARDEN_BIND_PORT", "9000")

        settings = Settings()

        assert settings.bind_port == 9000

    def test_log_level_from_env_var(self, monkeypatch):
        """Test MEMOGARDEN_LOG_LEVEL environment variable."""
        monkeypatch.setenv("MEMOGARDEN_LOG_LEVEL", "debug")

        settings = Settings()

        assert settings.log_level == "debug"

    def test_encryption_from_env_var(self, monkeypatch):
        """Test MEMOGARDEN_ENCRYPTION environment variable."""
        monkeypatch.setenv("MEMOGARDEN_ENCRYPTION", "required")

        settings = Settings()

        assert settings.encryption == "required"

    def test_data_dir_from_env_var(self, monkeypatch):
        """Test MEMOGARDEN_DATA_DIR environment variable."""
        monkeypatch.setenv("MEMOGARDEN_DATA_DIR", "/custom/data")

        settings = Settings()

        assert settings.data_dir == Path("/custom/data")

    def test_env_var_overrides_toml(self, monkeypatch, tmp_path):
        """Test environment variables override TOML config values."""
        # Create a test config file
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"

        config_file.write_text("""
[runtime]
max_view_entries = 500

[network]
bind_address = "192.168.1.1"
bind_port = 3000
""")

        # Set env vars that should override TOML
        monkeypatch.setenv("MEMOGARDEN_BIND_ADDRESS", "0.0.0.0")
        monkeypatch.setenv("MEMOGARDEN_BIND_PORT", "9000")

        settings = Settings(config_path=config_file)

        # Env vars should override TOML
        assert settings.bind_address == "0.0.0.0"
        assert settings.bind_port == 9000
        # TOML value should be used when env var not set
        assert settings.max_view_entries == 500

    def test_toml_used_when_no_env_var(self, tmp_path):
        """Test TOML config is used when environment variable not set."""
        # Create a test config file
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"

        config_file.write_text("""
[runtime]
resource_profile = "embedded"
max_view_entries = 200

[network]
bind_address = "10.0.0.1"
bind_port = 7000
""")

        settings = Settings(config_path=config_file)

        # TOML values should be used
        assert settings.max_view_entries == 200
        assert settings.bind_address == "10.0.0.1"
        assert settings.bind_port == 7000

    def test_defaults_used_when_no_env_or_toml(self, monkeypatch):
        """Test built-in defaults are used when no env var or TOML."""
        # Clear any existing env vars
        for key in list(os.environ.keys()):
            if key.startswith("MEMOGARDEN_"):
                monkeypatch.delenv(key, raising=False)

        settings = Settings()

        # Default profile is "standard"
        assert settings.max_view_entries == 1000
        assert settings.max_search_results == 100
        assert settings.bind_address == "127.0.0.1"
        assert settings.bind_port == 8080


# ============================================================================
# Resource Profile Tests (RFC-004 Section 5.2)
# ============================================================================

class TestResourceProfile:
    """Tests for ResourceProfile class."""

    def test_get_profile_embedded(self):
        """Test embedded resource profile settings."""
        profile = ResourceProfile.get_profile("embedded")

        assert profile["max_view_entries"] == 100
        assert profile["max_search_results"] == 20
        assert profile["fossilization_threshold"] == 0.80
        assert profile["wal_checkpoint_interval"] == 300
        assert profile["log_level"] == "warning"

    def test_get_profile_standard(self):
        """Test standard resource profile settings."""
        profile = ResourceProfile.get_profile("standard")

        assert profile["max_view_entries"] == 1000
        assert profile["max_search_results"] == 100
        assert profile["fossilization_threshold"] == 0.90
        assert profile["wal_checkpoint_interval"] == 60
        assert profile["log_level"] == "info"

    def test_get_profile_invalid(self):
        """Test get_profile() raises ValueError for unknown profile."""
        with pytest.raises(ValueError, match="Unknown resource profile"):
            ResourceProfile.get_profile("invalid")


# ============================================================================
# Path Resolution Tests (RFC-004 Section 4.2)
# ============================================================================

class TestPathResolution:
    """Tests for path resolution using RuntimeContext.

    RuntimeContext.get_db_path delegates to the module-level get_db_path
    (memogarden-system#7): database paths follow the RFC-004 env chain
    with a cwd fallback, not the verb default — verbs govern config and
    log locations only.
    """

    def test_get_db_path_with_context_serve(self, monkeypatch):
        """Test get_db_path() with serve context resolves via the env chain."""
        for var in ("MEMOGARDEN_DATA_DIR", "MEMOGARDEN_SOIL_DB", "MEMOGARDEN_CORE_DB"):
            monkeypatch.delenv(var, raising=False)
        ctx = resolve_context("serve")
        soil_path = ctx.get_db_path("soil")
        core_path = ctx.get_db_path("core")

        assert soil_path == get_db_path("soil")
        assert core_path == get_db_path("core")
        # cwd fallback (INV-PKG-002) — not the /var/lib verb default
        assert soil_path == Path("./soil.db")

    def test_get_db_path_with_context_run(self, monkeypatch):
        """Test get_db_path() with run context resolves via the env chain."""
        for var in ("MEMOGARDEN_DATA_DIR", "MEMOGARDEN_SOIL_DB", "MEMOGARDEN_CORE_DB"):
            monkeypatch.delenv(var, raising=False)
        ctx = resolve_context("run")
        soil_path = ctx.get_db_path("soil")
        core_path = ctx.get_db_path("core")

        assert soil_path == get_db_path("soil")
        assert core_path == get_db_path("core")

    def test_get_db_path_with_context_deploy(self, monkeypatch):
        """Test get_db_path() with deploy context resolves via the env chain."""
        for var in ("MEMOGARDEN_DATA_DIR", "MEMOGARDEN_SOIL_DB", "MEMOGARDEN_CORE_DB"):
            monkeypatch.delenv(var, raising=False)
        ctx = resolve_context("deploy")
        soil_path = ctx.get_db_path("soil")
        core_path = ctx.get_db_path("core")

        assert soil_path == get_db_path("soil")
        assert core_path == get_db_path("core")

    def test_get_db_path_env_overrides_beat_any_verb(self, tmp_path, monkeypatch):
        """MEMOGARDEN_DATA_DIR beats serve/deploy verb defaults for db paths."""
        monkeypatch.setenv("MEMOGARDEN_DATA_DIR", str(tmp_path))
        monkeypatch.delenv("MEMOGARDEN_SOIL_DB", raising=False)
        monkeypatch.delenv("MEMOGARDEN_CORE_DB", raising=False)

        for verb in ("serve", "run", "deploy"):
            ctx = resolve_context(verb)
            assert ctx.get_db_path("soil") == tmp_path / "soil.db"
            assert ctx.get_db_path("core") == tmp_path / "core.db"
