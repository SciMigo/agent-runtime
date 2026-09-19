"""A loopback-only TLS certificate, so pages served over HTTPS can reach the runtime in Safari.

Chrome and Firefox let an HTTPS page call http://127.0.0.1 because they treat loopback as
potentially trustworthy. Safari does not: it blocks the request as mixed content. Serving the
runtime over HTTPS on loopback, with a certificate the user's machine trusts, removes the mixed
content. Measured on macOS 2026-09-19: Safari 26.6 failed against http://127.0.0.1:9477 ("Load
failed") and passed against https://127.0.0.1:9478 with a certificate of this shape.

The certificate is deliberately narrow:

- self-signed, generated on this machine; the private key never leaves the runtime directory
  and is readable by the owner only;
- valid for 127.0.0.1, ::1 and localhost only, for server authentication only;
- not a certificate authority (basicConstraints CA:FALSE), so trusting it cannot make the
  machine accept a certificate for any other name;
- valid for 397 days, inside the limits Apple platforms apply to TLS server certificates.

On macOS, `trust()` adds it to the login keychain with SSL trust only. Other platforms need no
trust step for the browsers that matter here: they already accept http://127.0.0.1.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

VALIDITY_DAYS = 397
RENEW_BEFORE_DAYS = 30
COMMON_NAME = "Agent Runtime loopback"
DNS_NAMES = ("localhost",)
IP_ADDRESSES = ("127.0.0.1", "::1")
LOGIN_KEYCHAIN = Path.home() / "Library" / "Keychains" / "login.keychain-db"


class TLSError(RuntimeError):
    """A certificate or trust operation failed."""


@dataclass(frozen=True)
class CertInfo:
    """What `status` and `serve` need to know about the certificate on disk."""

    cert_path: Path
    key_path: Path
    not_after: dt.datetime
    sha1: str
    sha256: str

    def expires_within(self, days: int, now: dt.datetime | None = None) -> bool:
        now = now or dt.datetime.now(dt.UTC)
        return self.not_after - now < dt.timedelta(days=days)


def _info(cert: x509.Certificate, cert_path: Path, key_path: Path) -> CertInfo:
    return CertInfo(
        cert_path=cert_path,
        key_path=key_path,
        not_after=cert.not_valid_after_utc,
        sha1=cert.fingerprint(hashes.SHA1()).hex().upper(),  # noqa: S324 - keychain lookup key
        sha256=cert.fingerprint(hashes.SHA256()).hex().upper(),
    )


def load(cert_path: Path, key_path: Path) -> CertInfo | None:
    """Return the certificate on disk, or None when either file is missing or unreadable."""
    if not (cert_path.is_file() and key_path.is_file()):
        return None
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    except (OSError, ValueError):
        return None
    return _info(cert, cert_path, key_path)


def _write_private(path: Path, data: bytes) -> None:
    """Write a file readable by the owner only, replacing any previous version atomically."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def generate(cert_path: Path, key_path: Path, now: dt.datetime | None = None) -> CertInfo:
    """Create a new key and self-signed loopback certificate, replacing any existing pair."""
    now = now or dt.datetime.now(dt.UTC)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, COMMON_NAME)])
    alt_names: list[x509.GeneralName] = [x509.DNSName(n) for n in DNS_NAMES]
    alt_names += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in IP_ADDRESSES]
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=VALIDITY_DAYS))
        .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(cert_path.parent, 0o700)
    _write_private(
        key_path,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    _write_private(cert_path, cert.public_bytes(serialization.Encoding.PEM))
    return _info(cert, cert_path, key_path)


def ensure(cert_path: Path, key_path: Path, force: bool = False) -> tuple[CertInfo, bool]:
    """Return a usable certificate, generating one when missing, expiring or forced.

    The flag is True when a new certificate was written (and so needs trusting again).
    """
    existing = None if force else load(cert_path, key_path)
    if existing and not existing.expires_within(RENEW_BEFORE_DAYS):
        return existing, False
    return generate(cert_path, key_path), True


def _security(*args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(["security", *args], capture_output=True, text=True, check=False)
    except FileNotFoundError as error:
        raise TLSError("The macOS 'security' tool was not found") from error


def trust_supported() -> bool:
    """Whether this platform has an automated trust step (macOS only)."""
    return sys.platform == "darwin"


def is_trusted(info: CertInfo) -> bool | None:
    """Whether macOS trusts the certificate for SSL; None where this cannot be checked."""
    if not trust_supported():
        return None
    try:
        result = _security("verify-cert", "-c", str(info.cert_path), "-p", "ssl")
    except TLSError:
        return None
    return result.returncode == 0


def trust(info: CertInfo) -> None:
    """Add the certificate to the login keychain, trusted for SSL only (asks for a password)."""
    if not trust_supported():
        raise TLSError("Automatic trust is only available on macOS")
    result = _security(
        "add-trusted-cert",
        "-r",
        "trustRoot",
        "-p",
        "ssl",
        "-k",
        str(LOGIN_KEYCHAIN),
        str(info.cert_path),
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise TLSError(f"security add-trusted-cert failed: {detail}")


def untrust(info: CertInfo) -> None:
    """Remove the certificate's trust setting and the certificate from the login keychain."""
    if not trust_supported():
        return
    _security("remove-trusted-cert", str(info.cert_path))
    _security("delete-certificate", "-Z", info.sha1, str(LOGIN_KEYCHAIN))


def remove_files(info: CertInfo) -> None:
    for path in (info.cert_path, info.key_path):
        path.unlink(missing_ok=True)
