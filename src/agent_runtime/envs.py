"""Virtual environment and kernel spec management.

Handles:
- Creating isolated venvs per lab
- Installing ipykernel in venvs
- Registering Jupyter kernel specs
"""

import re
import subprocess
import sys
from pathlib import Path

from agent_runtime.config import settings
from agent_runtime.events import EventType, emit_event

LAB_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"


def validate_lab_id(lab_id: str) -> None:
    """Reject identifiers that are unsafe in filesystem and kernelspec names."""
    if re.fullmatch(LAB_ID_PATTERN, lab_id) is None:
        raise ValueError(
            "lab_id must be 1-64 ASCII letters, digits, dots, underscores, or hyphens "
            "and must start with a letter or digit"
        )


class EnvManager:
    """Manages virtual environments and kernel specs for labs."""

    def __init__(self) -> None:
        self._envs: dict[str, Path] = {}
        self._load_existing_envs()

    def _load_existing_envs(self) -> None:
        """Load existing environments from disk."""
        settings.ensure_dirs()

        for env_dir in settings.envs_dir.iterdir():
            if env_dir.is_dir() and (env_dir / ".venv").exists():
                lab_id = env_dir.name.replace("lab-", "")
                self._envs[lab_id] = env_dir / ".venv"

    def get_env_path(self, lab_id: str) -> Path | None:
        """Get the venv path for a lab if it exists."""
        return self._envs.get(lab_id)

    def get_python_path(self, lab_id: str) -> Path | None:
        """Get the Python executable path for a lab's venv."""
        env_path = self.get_env_path(lab_id)
        if env_path:
            # macOS/Linux
            python_path = env_path / "bin" / "python"
            if python_path.exists():
                return python_path
            # Windows
            python_path = env_path / "Scripts" / "python.exe"
            if python_path.exists():
                return python_path
        return None

    def create_env(self, lab_id: str, python_version: str | None = None) -> Path:
        """Create a new virtual environment for a lab.

        Args:
            lab_id: Unique identifier for the lab
            python_version: Optional Python version (e.g., "3.11")

        Returns:
            Path to the created venv
        """
        validate_lab_id(lab_id)
        settings.ensure_dirs()

        lab_dir = settings.envs_dir / f"lab-{lab_id}"
        lab_dir.mkdir(parents=True, exist_ok=True)

        venv_path = lab_dir / ".venv"

        emit_event(EventType.ENV_CREATING, {"lab_id": lab_id, "path": str(venv_path)})

        # Determine which Python to use
        python_cmd = sys.executable
        if python_version:
            # Try to find the specified version
            for cmd in [f"python{python_version}", f"python{python_version.replace('.', '')}"]:
                try:
                    result = subprocess.run(
                        [cmd, "--version"],
                        capture_output=True,
                        text=True,
                    )
                    if result.returncode == 0:
                        python_cmd = cmd
                        break
                except FileNotFoundError:
                    continue

        # Create venv
        subprocess.run(
            [python_cmd, "-m", "venv", str(venv_path)],
            check=True,
            capture_output=True,
        )

        # Install ipykernel
        pip_path = venv_path / "bin" / "pip"
        if not pip_path.exists():
            pip_path = venv_path / "Scripts" / "pip.exe"

        subprocess.run(
            [str(pip_path), "install", "--quiet", "ipykernel"],
            check=True,
            capture_output=True,
        )

        self._envs[lab_id] = venv_path

        emit_event(EventType.ENV_CREATED, {"lab_id": lab_id, "path": str(venv_path)})

        return venv_path

    def install_kernel_spec(self, lab_id: str) -> str:
        """Install a Jupyter kernel spec for a lab's venv.

        Returns the kernel name.
        """
        python_path = self.get_python_path(lab_id)
        if not python_path:
            raise ValueError(f"No environment found for lab {lab_id}")

        kernel_name = f"agent-runtime-{lab_id}"
        display_name = f"Agent Runtime ({lab_id})"

        emit_event(EventType.KERNEL_SPEC_INSTALLING, {"lab_id": lab_id, "kernel_name": kernel_name})

        # Install kernel spec
        subprocess.run(
            [
                str(python_path),
                "-m",
                "ipykernel",
                "install",
                "--user",
                "--name",
                kernel_name,
                "--display-name",
                display_name,
            ],
            check=True,
            capture_output=True,
        )

        emit_event(EventType.KERNEL_SPEC_INSTALLED, {"lab_id": lab_id, "kernel_name": kernel_name})

        return kernel_name

    def get_kernel_name(self, lab_id: str) -> str:
        """Get the kernel name for a lab."""
        validate_lab_id(lab_id)
        return f"agent-runtime-{lab_id}"

    def install_packages(
        self,
        lab_id: str,
        packages: list[str],
    ) -> tuple[bool, str]:
        """Install packages into a lab's venv.

        Returns (success, output).
        """
        env_path = self.get_env_path(lab_id)
        if not env_path:
            return False, f"No environment found for lab {lab_id}"

        pip_path = env_path / "bin" / "pip"
        if not pip_path.exists():
            pip_path = env_path / "Scripts" / "pip.exe"

        emit_event(EventType.PACKAGES_INSTALLING, {"lab_id": lab_id, "packages": packages})

        try:
            result = subprocess.run(
                [str(pip_path), "install"] + packages,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
            )

            success = result.returncode == 0
            output = result.stdout if success else result.stderr

            emit_event(
                EventType.PACKAGES_INSTALLED if success else EventType.PACKAGES_FAILED,
                {"lab_id": lab_id, "packages": packages, "output": output},
            )

            return success, output

        except subprocess.TimeoutExpired:
            return False, "Package installation timed out"

    def delete_env(self, lab_id: str) -> bool:
        """Delete a lab's virtual environment."""
        env_path = self.get_env_path(lab_id)
        if not env_path:
            return False

        import shutil

        lab_dir = env_path.parent
        shutil.rmtree(lab_dir, ignore_errors=True)

        if lab_id in self._envs:
            del self._envs[lab_id]

        emit_event(EventType.ENV_DELETED, {"lab_id": lab_id})

        return True

    def list_envs(self) -> list[dict[str, str | None]]:
        """List all managed environments."""
        envs = []
        for lab_id, path in self._envs.items():
            python_path = self.get_python_path(lab_id)
            envs.append(
                {
                    "lab_id": lab_id,
                    "path": str(path),
                    "python_path": str(python_path) if python_path else None,
                    "kernel_name": self.get_kernel_name(lab_id),
                }
            )
        return envs


# Global env manager
env_manager = EnvManager()
