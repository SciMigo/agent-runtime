"""Tests for the loopback TLS certificate (agent_runtime.tls)."""

import datetime as dt
import ipaddress
import stat
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ExtendedKeyUsageOID

from agent_runtime import tls


@pytest.fixture
def paths(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "tls" / "localhost.pem", tmp_path / "tls" / "localhost-key.pem"


def _cert(path: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(path.read_bytes())


class TestGenerate:
    def test_names_only_loopback(self, paths):
        info = tls.generate(*paths)
        san = _cert(info.cert_path).extensions.get_extension_for_class(x509.SubjectAlternativeName)
        assert san.value.get_values_for_type(x509.DNSName) == ["localhost"]
        assert san.value.get_values_for_type(x509.IPAddress) == [
            ipaddress.ip_address("127.0.0.1"),
            ipaddress.ip_address("::1"),
        ]

    def test_cannot_act_as_a_certificate_authority(self, paths):
        cert = _cert(tls.generate(*paths).cert_path)
        constraints = cert.extensions.get_extension_for_class(x509.BasicConstraints)
        assert constraints.critical and constraints.value.ca is False
        usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
        assert usage.key_cert_sign is False and usage.crl_sign is False
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        assert list(eku) == [ExtendedKeyUsageOID.SERVER_AUTH]

    def test_validity_within_apple_limits(self, paths):
        cert = _cert(tls.generate(*paths).cert_path)
        lifetime = cert.not_valid_after_utc - cert.not_valid_before_utc
        assert lifetime <= dt.timedelta(days=398)

    def test_key_is_private_and_matches(self, paths):
        info = tls.generate(*paths)
        assert stat.S_IMODE(info.key_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(info.key_path.parent.stat().st_mode) == 0o700
        key = serialization.load_pem_private_key(info.key_path.read_bytes(), password=None)
        cert_key = _cert(info.cert_path).public_key()
        spki = serialization.PublicFormat.SubjectPublicKeyInfo
        assert key.public_key().public_bytes(
            serialization.Encoding.DER, spki
        ) == cert_key.public_bytes(serialization.Encoding.DER, spki)


class TestEnsure:
    def test_reuses_a_valid_certificate(self, paths):
        first, created = tls.ensure(*paths)
        again, created_again = tls.ensure(*paths)
        assert created and not created_again
        assert again.sha256 == first.sha256

    def test_renews_one_that_expires_soon(self, paths):
        long_ago = dt.datetime.now(dt.UTC) - dt.timedelta(days=tls.VALIDITY_DAYS - 10)
        old = tls.generate(*paths, now=long_ago)
        assert old.expires_within(tls.RENEW_BEFORE_DAYS)
        renewed, created = tls.ensure(*paths)
        assert created and renewed.sha256 != old.sha256

    def test_force_replaces(self, paths):
        first, _ = tls.ensure(*paths)
        forced, created = tls.ensure(*paths, force=True)
        assert created and forced.sha256 != first.sha256

    def test_load_missing_or_corrupt_is_none(self, paths):
        cert_path, key_path = paths
        assert tls.load(cert_path, key_path) is None
        tls.generate(cert_path, key_path)
        cert_path.write_text("not a certificate")
        assert tls.load(cert_path, key_path) is None


class TestTrust:
    def _ok(self, *args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, "", "")

    def test_trust_adds_ssl_only_trust_to_the_login_keychain(self, paths):
        info = tls.generate(*paths)
        with (
            patch.object(tls.sys, "platform", "darwin"),
            patch.object(tls.subprocess, "run", side_effect=self._ok) as run,
        ):
            tls.trust(info)
        assert run.call_args.args[0] == [
            "security",
            "add-trusted-cert",
            "-r",
            "trustRoot",
            "-p",
            "ssl",
            "-k",
            str(tls.LOGIN_KEYCHAIN),
            str(info.cert_path),
        ]

    def test_trust_failure_is_reported(self, paths):
        info = tls.generate(*paths)
        failed = subprocess.CompletedProcess([], 1, "", "User canceled the operation.")
        with (
            patch.object(tls.sys, "platform", "darwin"),
            patch.object(tls.subprocess, "run", return_value=failed),
            pytest.raises(tls.TLSError, match="User canceled"),
        ):
            tls.trust(info)

    def test_untrust_removes_trust_then_the_certificate(self, paths):
        info = tls.generate(*paths)
        with (
            patch.object(tls.sys, "platform", "darwin"),
            patch.object(tls.subprocess, "run", side_effect=self._ok) as run,
        ):
            tls.untrust(info)
        calls = [c.args[0] for c in run.call_args_list]
        assert calls == [
            ["security", "remove-trusted-cert", str(info.cert_path)],
            ["security", "delete-certificate", "-Z", info.sha1, str(tls.LOGIN_KEYCHAIN)],
        ]

    def test_other_platforms_have_no_trust_step(self, paths):
        info = tls.generate(*paths)
        with patch.object(tls.sys, "platform", "linux"):
            assert tls.is_trusted(info) is None
            with pytest.raises(tls.TLSError):
                tls.trust(info)
            tls.untrust(info)  # a no-op, not an error


class TestCli:
    def _run(self, runtime_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
        import os
        import sys

        env = dict(os.environ, AGENT_RUNTIME_RUNTIME_DIR=str(runtime_dir))
        return subprocess.run(
            [sys.executable, "-m", "agent_runtime.cli", "tls", *args],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_setup_status_remove(self, tmp_path: Path):
        runtime_dir = tmp_path / "runtime"
        assert "No certificate" in self._run(runtime_dir, "status").stdout

        setup = self._run(runtime_dir, "setup", "--no-trust")
        assert setup.returncode == 0, setup.stderr
        assert "Created certificate" in setup.stdout and "Not trusted" in setup.stdout
        assert (runtime_dir / "tls" / "localhost.pem").is_file()

        again = self._run(runtime_dir, "setup", "--no-trust")
        assert "Using certificate" in again.stdout

        status = self._run(runtime_dir, "status")
        assert "expires" in status.stdout and "SHA-256" in status.stdout

        removed = self._run(runtime_dir, "remove")
        assert removed.returncode == 0 and not (runtime_dir / "tls" / "localhost.pem").exists()
