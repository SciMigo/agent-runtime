"""Configuration management for Agent Runtime."""

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def get_runtime_dir() -> Path:
    """Get the runtime data directory."""
    # Use XDG_DATA_HOME on Linux, or ~/.agent-runtime elsewhere
    if os.name == "posix":
        xdg_data = os.environ.get("XDG_DATA_HOME")
        if xdg_data:
            return Path(xdg_data) / "agent-runtime"
    return Path.home() / ".agent-runtime"


class Settings(BaseSettings):
    """Runtime settings with environment variable support."""

    model_config = SettingsConfigDict(
        env_prefix="AGENT_RUNTIME_",
        env_file=".env",
        extra="ignore",
    )

    # Server settings
    host: str = "127.0.0.1"
    port: int = 9477
    # Second listener, HTTPS on loopback, for Safari (see tls.py). Used once `tls setup` has run.
    https_port: int = 9478
    debug: bool = False

    # Runtime data directory
    runtime_dir: Path = Field(default_factory=get_runtime_dir)

    # Auth settings
    require_pairing: bool = True
    pairing_timeout: int = 300  # seconds to wait for pairing approval

    # Kernel settings
    default_kernel_timeout: int = 60  # seconds
    max_kernels_per_lab: int = 1
    kernel_idle_timeout: int = 3600  # 1 hour

    # Environment settings
    python_version: str | None = None  # Use system Python if not specified

    # Lab actions (labs.py)
    lab_git_protocols: str = "https"  # GIT_ALLOW_PROTOCOL for fetching lab repositories
    lab_output_limit: int = 1_000_000  # characters of output kept per run
    lab_stop_grace: float = 8.0  # seconds between SIGINT and SIGKILL when stopping a run

    @property
    def envs_dir(self) -> Path:
        """Directory for virtual environments."""
        return self.runtime_dir / "envs"

    @property
    def config_file(self) -> Path:
        """Path to the config file."""
        return self.runtime_dir / "config.toml"

    @property
    def labs_dir(self) -> Path:
        """Checkouts of lab repositories, and the record of approved lab versions."""
        return self.runtime_dir / "labs"

    @property
    def tls_dir(self) -> Path:
        """Directory for the loopback TLS certificate and its private key."""
        return self.runtime_dir / "tls"

    @property
    def tls_cert_file(self) -> Path:
        return self.tls_dir / "localhost.pem"

    @property
    def tls_key_file(self) -> Path:
        return self.tls_dir / "localhost-key.pem"

    @property
    def paired_origins_file(self) -> Path:
        """Path to the paired origins file."""
        return self.runtime_dir / "paired_origins.json"

    def ensure_dirs(self) -> None:
        """Ensure all required directories exist."""
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.envs_dir.mkdir(parents=True, exist_ok=True)


# Global settings instance
settings = Settings()
