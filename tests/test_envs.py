"""Tests for virtual environment management."""

from unittest.mock import MagicMock, patch

import pytest

from agent_runtime.envs import EnvManager


class TestEnvManager:
    """Tests for EnvManager class."""

    @pytest.fixture
    def manager(self, temp_runtime_dir):
        """Create a fresh env manager for each test."""
        with patch("agent_runtime.envs.settings") as mock_settings:
            mock_settings.envs_dir = temp_runtime_dir / "envs"
            mock_settings.envs_dir.mkdir(exist_ok=True)
            mock_settings.ensure_dirs = MagicMock()
            return EnvManager()

    def test_init_empty(self, manager):
        """Test manager initializes with no environments."""
        assert manager._envs == {}

    def test_load_existing_envs(self, temp_runtime_dir):
        """Test loading existing environments from disk."""
        envs_dir = temp_runtime_dir / "envs"
        envs_dir.mkdir(exist_ok=True)

        # Create a fake environment
        lab_dir = envs_dir / "lab-test123"
        lab_dir.mkdir()
        (lab_dir / ".venv").mkdir()

        with patch("agent_runtime.envs.settings") as mock_settings:
            mock_settings.envs_dir = envs_dir
            mock_settings.ensure_dirs = MagicMock()
            manager = EnvManager()

        assert "test123" in manager._envs

    def test_get_env_path_not_found(self, manager):
        """Test get_env_path returns None for non-existent lab."""
        assert manager.get_env_path("nonexistent") is None

    def test_get_env_path_found(self, manager, temp_runtime_dir):
        """Test get_env_path returns path when env exists."""
        venv_path = temp_runtime_dir / "envs" / "lab-test" / ".venv"
        manager._envs["test"] = venv_path
        assert manager.get_env_path("test") == venv_path

    def test_get_python_path_unix(self, manager, temp_runtime_dir):
        """Test getting Python path on Unix systems."""
        lab_dir = temp_runtime_dir / "envs" / "lab-test"
        venv_path = lab_dir / ".venv"
        venv_path.mkdir(parents=True)

        # Create fake Python executable
        bin_dir = venv_path / "bin"
        bin_dir.mkdir()
        python_path = bin_dir / "python"
        python_path.touch()

        manager._envs["test"] = venv_path

        result = manager.get_python_path("test")
        assert result == python_path

    def test_get_python_path_not_found(self, manager):
        """Test get_python_path returns None when env doesn't exist."""
        assert manager.get_python_path("nonexistent") is None

    def test_get_kernel_name(self, manager):
        """Test kernel name generation."""
        assert manager.get_kernel_name("test123") == "agent-runtime-test123"
        assert manager.get_kernel_name("my-lab") == "agent-runtime-my-lab"

    def test_create_env_rejects_path_like_lab_id(self, manager):
        with pytest.raises(ValueError, match="lab_id must be"):
            manager.create_env("../../outside")

    def test_create_env(self, manager, temp_runtime_dir):
        """Test creating a new virtual environment."""
        with patch("agent_runtime.envs.settings") as mock_settings:
            mock_settings.envs_dir = temp_runtime_dir / "envs"
            mock_settings.ensure_dirs = MagicMock()

            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)

                venv_path = manager.create_env("new-lab")

        assert "new-lab" in manager._envs
        assert venv_path == temp_runtime_dir / "envs" / "lab-new-lab" / ".venv"

        # Verify subprocess calls (venv creation and ipykernel install)
        assert mock_run.call_count == 2

    def test_create_env_with_python_version(self, manager, temp_runtime_dir):
        """Test creating env with specific Python version."""
        with patch("agent_runtime.envs.settings") as mock_settings:
            mock_settings.envs_dir = temp_runtime_dir / "envs"
            mock_settings.ensure_dirs = MagicMock()

            with patch("subprocess.run") as mock_run:
                # First call checks python version, rest create venv and install
                mock_run.return_value = MagicMock(returncode=0)

                manager.create_env("new-lab", python_version="3.11")

        # Should have tried to find python3.11
        assert mock_run.called

    def test_install_kernel_spec(self, manager, temp_runtime_dir):
        """Test installing a Jupyter kernel spec."""
        # Set up a fake environment
        lab_dir = temp_runtime_dir / "envs" / "lab-test"
        venv_path = lab_dir / ".venv"
        venv_path.mkdir(parents=True)
        bin_dir = venv_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "python").touch()

        manager._envs["test"] = venv_path

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            kernel_name = manager.install_kernel_spec("test")

        assert kernel_name == "agent-runtime-test"
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "-m" in call_args
        assert "ipykernel" in call_args
        assert "install" in call_args

    def test_install_kernel_spec_no_env(self, manager):
        """Test install_kernel_spec raises for non-existent env."""
        with pytest.raises(ValueError, match="No environment found"):
            manager.install_kernel_spec("nonexistent")

    def test_install_packages(self, manager, temp_runtime_dir):
        """Test installing packages into an environment."""
        # Set up a fake environment
        lab_dir = temp_runtime_dir / "envs" / "lab-test"
        venv_path = lab_dir / ".venv"
        venv_path.mkdir(parents=True)
        bin_dir = venv_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "pip").touch()

        manager._envs["test"] = venv_path

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="Successfully installed pandas", stderr=""
            )

            success, output = manager.install_packages("test", ["pandas", "numpy"])

        assert success is True
        assert "Successfully installed" in output

    def test_install_packages_failure(self, manager, temp_runtime_dir):
        """Test handling package installation failure."""
        lab_dir = temp_runtime_dir / "envs" / "lab-test"
        venv_path = lab_dir / ".venv"
        venv_path.mkdir(parents=True)
        bin_dir = venv_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "pip").touch()

        manager._envs["test"] = venv_path

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="ERROR: Could not find package"
            )

            success, output = manager.install_packages("test", ["nonexistent-pkg"])

        assert success is False
        assert "ERROR" in output

    def test_install_packages_no_env(self, manager):
        """Test install_packages returns error for non-existent env."""
        success, output = manager.install_packages("nonexistent", ["pandas"])
        assert success is False
        assert "No environment found" in output

    def test_delete_env(self, manager, temp_runtime_dir):
        """Test deleting an environment."""
        # Set up a fake environment
        lab_dir = temp_runtime_dir / "envs" / "lab-test"
        venv_path = lab_dir / ".venv"
        venv_path.mkdir(parents=True)
        (venv_path / "some_file.txt").touch()

        manager._envs["test"] = venv_path

        result = manager.delete_env("test")

        assert result is True
        assert "test" not in manager._envs
        assert not lab_dir.exists()

    def test_delete_env_not_found(self, manager):
        """Test deleting non-existent environment."""
        result = manager.delete_env("nonexistent")
        assert result is False

    def test_list_envs(self, manager, temp_runtime_dir):
        """Test listing all environments."""
        # Set up fake environments
        for lab_id in ["lab1", "lab2"]:
            lab_dir = temp_runtime_dir / "envs" / f"lab-{lab_id}"
            venv_path = lab_dir / ".venv"
            venv_path.mkdir(parents=True)
            bin_dir = venv_path / "bin"
            bin_dir.mkdir()
            (bin_dir / "python").touch()
            manager._envs[lab_id] = venv_path

        envs = manager.list_envs()

        assert len(envs) == 2
        lab_ids = [e["lab_id"] for e in envs]
        assert "lab1" in lab_ids
        assert "lab2" in lab_ids

        for env in envs:
            assert "path" in env
            assert "python_path" in env
            assert "kernel_name" in env
