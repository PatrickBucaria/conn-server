"""TLS certificate generation and management for Conn server.

Generates EC P-256 self-signed certificates on first run.
Certs are stored in ~/.conn/tls/ and reused across restarts.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import os
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from .config import CONFIG_DIR

TLS_DIR = CONFIG_DIR / "tls"
CERT_FILE = TLS_DIR / "server.crt"
KEY_FILE = TLS_DIR / "server.key"


def _get_local_ips() -> list[str]:
    """Get all local network IP addresses for SAN entries."""
    ips = {"127.0.0.1", "::1"}
    try:
        # Primary local IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        # All IPs from hostname resolution
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            addr = info[4][0]
            if not addr.startswith("fe80"):  # Skip link-local IPv6
                ips.add(addr)
    except Exception:
        pass
    return sorted(ips)


def _generate_cert() -> tuple[Path, Path]:
    """Generate a new EC P-256 self-signed certificate."""
    TLS_DIR.mkdir(mode=0o700, exist_ok=True)

    # Generate EC P-256 private key
    key = ec.generate_private_key(ec.SECP256R1())

    # Build certificate
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Conn Server"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Conn"),
    ])

    # Subject Alternative Names: all local IPs + localhost
    san_entries: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
    ]
    for ip_str in _get_local_ips():
        try:
            san_entries.append(x509.IPAddress(ipaddress.ip_address(ip_str)))
        except ValueError:
            pass

    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=3650))  # 10 years
        .add_extension(
            x509.SubjectAlternativeName(san_entries),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )

    # Write private key (owner-only)
    key_bytes = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    fd = os.open(str(KEY_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, key_bytes)
    finally:
        os.close(fd)

    # Write certificate
    cert_bytes = cert.public_bytes(serialization.Encoding.PEM)
    fd = os.open(str(CERT_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, cert_bytes)
    finally:
        os.close(fd)

    return CERT_FILE, KEY_FILE


def ensure_certs() -> tuple[Path, Path]:
    """Generate certs if missing. Returns (cert_path, key_path)."""
    if CERT_FILE.exists() and KEY_FILE.exists():
        return CERT_FILE, KEY_FILE
    return _generate_cert()


def get_cert_fingerprint() -> str:
    """Returns SHA-256 fingerprint like 'SHA256:AB:CD:EF:...' for display."""
    cert_pem = CERT_FILE.read_bytes()
    cert = x509.load_pem_x509_certificate(cert_pem)
    digest = cert.fingerprint(hashes.SHA256())
    hex_str = ":".join(f"{b:02X}" for b in digest)
    return f"SHA256:{hex_str}"


def get_cert_der_b64() -> str:
    """Returns base64-encoded DER cert for QR code payload."""
    cert_pem = CERT_FILE.read_bytes()
    cert = x509.load_pem_x509_certificate(cert_pem)
    der_bytes = cert.public_bytes(serialization.Encoding.DER)
    return base64.b64encode(der_bytes).decode("ascii")


def get_cert_fingerprint_from_der_b64(der_b64: str) -> str:
    """Compute SHA-256 fingerprint from base64-encoded DER cert."""
    der_bytes = base64.b64decode(der_b64)
    digest = hashlib.sha256(der_bytes).hexdigest()
    hex_str = ":".join(digest[i:i+2].upper() for i in range(0, len(digest), 2))
    return f"SHA256:{hex_str}"
