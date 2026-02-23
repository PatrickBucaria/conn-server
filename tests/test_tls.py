"""Tests for TLS certificate generation and management."""

import base64
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec


def test_ensure_certs_generates_on_first_run(tmp_path):
    """Certs are generated when they don't exist."""
    cert_file = tmp_path / "tls" / "server.crt"
    key_file = tmp_path / "tls" / "server.key"

    with patch("conn_server.tls.TLS_DIR", tmp_path / "tls"), \
         patch("conn_server.tls.CERT_FILE", cert_file), \
         patch("conn_server.tls.KEY_FILE", key_file):
        from conn_server.tls import ensure_certs
        c, k = ensure_certs()

    assert c.exists()
    assert k.exists()
    # Key should be owner-only
    assert oct(k.stat().st_mode & 0o777) == "0o600"


def test_ensure_certs_reuses_existing(tmp_path):
    """Existing certs are not regenerated."""
    cert_file = tmp_path / "tls" / "server.crt"
    key_file = tmp_path / "tls" / "server.key"

    with patch("conn_server.tls.TLS_DIR", tmp_path / "tls"), \
         patch("conn_server.tls.CERT_FILE", cert_file), \
         patch("conn_server.tls.KEY_FILE", key_file):
        from conn_server.tls import ensure_certs
        ensure_certs()
        mtime1 = cert_file.stat().st_mtime

        c, k = ensure_certs()
        mtime2 = cert_file.stat().st_mtime

    assert mtime1 == mtime2


def test_cert_is_ec_p256(tmp_path):
    """Generated cert uses EC P-256 key."""
    cert_file = tmp_path / "tls" / "server.crt"
    key_file = tmp_path / "tls" / "server.key"

    with patch("conn_server.tls.TLS_DIR", tmp_path / "tls"), \
         patch("conn_server.tls.CERT_FILE", cert_file), \
         patch("conn_server.tls.KEY_FILE", key_file):
        from conn_server.tls import ensure_certs
        ensure_certs()

    cert = x509.load_pem_x509_certificate(cert_file.read_bytes())
    pub_key = cert.public_key()
    assert isinstance(pub_key, ec.EllipticCurvePublicKey)
    assert isinstance(pub_key.curve, ec.SECP256R1)


def test_cert_has_san_localhost(tmp_path):
    """Generated cert includes localhost in SAN."""
    cert_file = tmp_path / "tls" / "server.crt"
    key_file = tmp_path / "tls" / "server.key"

    with patch("conn_server.tls.TLS_DIR", tmp_path / "tls"), \
         patch("conn_server.tls.CERT_FILE", cert_file), \
         patch("conn_server.tls.KEY_FILE", key_file):
        from conn_server.tls import ensure_certs
        ensure_certs()

    cert = x509.load_pem_x509_certificate(cert_file.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    dns_names = san.value.get_values_for_type(x509.DNSName)
    assert "localhost" in dns_names


def test_fingerprint_format(tmp_path):
    """Fingerprint is SHA256:XX:XX:... format."""
    cert_file = tmp_path / "tls" / "server.crt"
    key_file = tmp_path / "tls" / "server.key"

    with patch("conn_server.tls.TLS_DIR", tmp_path / "tls"), \
         patch("conn_server.tls.CERT_FILE", cert_file), \
         patch("conn_server.tls.KEY_FILE", key_file):
        from conn_server.tls import ensure_certs, get_cert_fingerprint
        ensure_certs()
        fp = get_cert_fingerprint()

    assert fp.startswith("SHA256:")
    hex_part = fp.removeprefix("SHA256:")
    octets = hex_part.split(":")
    assert len(octets) == 32  # SHA-256 = 32 bytes
    for octet in octets:
        assert len(octet) == 2
        int(octet, 16)  # Should be valid hex


def test_der_b64_roundtrips(tmp_path):
    """DER base64 export can be decoded back to a valid cert."""
    cert_file = tmp_path / "tls" / "server.crt"
    key_file = tmp_path / "tls" / "server.key"

    with patch("conn_server.tls.TLS_DIR", tmp_path / "tls"), \
         patch("conn_server.tls.CERT_FILE", cert_file), \
         patch("conn_server.tls.KEY_FILE", key_file):
        from conn_server.tls import ensure_certs, get_cert_der_b64
        ensure_certs()
        der_b64 = get_cert_der_b64()

    der_bytes = base64.b64decode(der_b64)
    cert = x509.load_der_x509_certificate(der_bytes)
    assert cert.subject.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)[0].value == "Conn Server"


def test_der_b64_size_fits_qr(tmp_path):
    """EC P-256 cert DER base64 should be small enough for QR codes."""
    cert_file = tmp_path / "tls" / "server.crt"
    key_file = tmp_path / "tls" / "server.key"

    with patch("conn_server.tls.TLS_DIR", tmp_path / "tls"), \
         patch("conn_server.tls.CERT_FILE", cert_file), \
         patch("conn_server.tls.KEY_FILE", key_file):
        from conn_server.tls import ensure_certs, get_cert_der_b64
        ensure_certs()
        der_b64 = get_cert_der_b64()

    # EC P-256 cert should be small enough for QR codes (typically ~600 bytes,
    # varies slightly with SANs and signature padding)
    assert len(der_b64) < 700, f"DER base64 too large for QR: {len(der_b64)} bytes"


def test_fingerprint_from_der_b64_matches(tmp_path):
    """Fingerprint computed from DER base64 matches the PEM-based fingerprint."""
    cert_file = tmp_path / "tls" / "server.crt"
    key_file = tmp_path / "tls" / "server.key"

    with patch("conn_server.tls.TLS_DIR", tmp_path / "tls"), \
         patch("conn_server.tls.CERT_FILE", cert_file), \
         patch("conn_server.tls.KEY_FILE", key_file):
        from conn_server.tls import ensure_certs, get_cert_fingerprint, get_cert_der_b64, get_cert_fingerprint_from_der_b64
        ensure_certs()
        fp_from_pem = get_cert_fingerprint()
        der_b64 = get_cert_der_b64()
        fp_from_der = get_cert_fingerprint_from_der_b64(der_b64)

    assert fp_from_pem == fp_from_der
