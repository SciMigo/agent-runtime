"""Tests for configuration management."""

import os
from pathlib import Path
from unittest.mock import patch


class TestSettings:
    """Tests for Settings class."""

    def test_default_settings(self):
        """Test default settings values."""
        with patch.dict(os.environ, {}, clear=True):
            # Import fresh settings
            from agent_runtime.config import Settings

            settings = Settings()

            assert settings.host == "127.0.0.1"
            assert settings.port == 9477
            assert settings.debug is False
            assert settings.require_pairing is True
            assert settings.pairing_timeout == 300
            assert settings.default_kernel_timeout == 60
            assert settings.max_kernels_per_lab == 1
            assert settings.kernel_idle_timeout == 3600

    def test_env_override(self):
        """Test environment variable overrides."""
        env = {
            "AGENT_RUNTIME_HOST": "0.0.0.0",
            "AGENT_RUNTIME_PORT": "8080",
            "AGENT_RUNTIME_DEBUG": "true",
            "AGENT_RUNTIME_REQUIRE_PAIRING": "false",
        }

        with patch.dict(os.environ, env, clear=True):
            from agent_runtime.config import Settings

            settings = Settings()

            assert settings.host == "0.0.0.0"
            assert settings.port == 8080
            assert settings.debug is True
            assert settings.require_pairing is False

    def test_envs_dir_property(self):
        """Test envs_dir property."""
        from agent_runtime.config import Settings

        settings = Settings(runtime_dir=Path("/tmp/test-runtime"))

        assert settings.envs_dir == Path("/tmp/test-runtime/envs")

    def test_config_file_property(self):
        """Test config_file property."""
        from agent_runtime.config import Settings

        settings = Settings(runtime_dir=Path("/tmp/test-runtime"))

        assert settings.config_file == Path("/tmp/test-runtime/config.toml")

    def test_paired_origins_file_property(self):
        """Test paired_origins_file property."""
        from agent_runtime.config import Settings

        settings = Settings(runtime_dir=Path("/tmp/test-runtime"))

        assert settings.paired_origins_file == Path("/tmp/test-runtime/paired_origins.json")

    def test_ensure_dirs(self, tmp_path):
        """Test ensure_dirs creates directories."""
        from agent_runtime.config import Settings

        runtime_dir = tmp_path / "new-runtime"
        settings = Settings(runtime_dir=runtime_dir)

        assert not runtime_dir.exists()

        settings.ensure_dirs()

        assert runtime_dir.exists()
        assert settings.envs_dir.exists()


class TestGetRuntimeDir:
    """Tests for get_runtime_dir function."""

    def test_default_home_dir(self):
        """Test default runtime directory is in home."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("os.name", "posix"):
                from agent_runtime.config import get_runtime_dir

                runtime_dir = get_runtime_dir()

                assert ".agent-runtime" in str(runtime_dir)

    def test_xdg_data_home(self):
        """Test XDG_DATA_HOME is respected on Linux."""
        with patch.dict(os.environ, {"XDG_DATA_HOME": "/custom/data"}, clear=True):
            with patch("os.name", "posix"):
                from agent_runtime.config import get_runtime_dir

                runtime_dir = get_runtime_dir()

                assert runtime_dir == Path("/custom/data/agent-runtime")
