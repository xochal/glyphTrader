"""
glyphTrader production license verification.

Ed25519 signature-based license keys. The private key is kept offline;
only the public key is embedded here for verification.

License key format: GT-<base64url_ed25519_signature>
Signed payload:    glyphTrader:live:<version>
"""

import base64
import logging

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

from db.database import get_db, get_version

logger = logging.getLogger("glyphTrader.license")

_PUBLIC_KEY_HEX = "820c2c62b0029297e9ec2aabde8d934591db7b870900c05c5029fe9ad50667c5"
_PAYLOAD_TEMPLATE = "glyphTrader:live:{version}"


def _get_public_key() -> Ed25519PublicKey:
    """Load the embedded Ed25519 public key."""
    pub_bytes = bytes.fromhex(_PUBLIC_KEY_HEX)
    return Ed25519PublicKey.from_public_bytes(pub_bytes)


def verify_license_key(key: str) -> bool:
    """
    Verify a license key against the current VERSION.
    Returns True if the signature is valid for this version.
    """
    if not key or not key.startswith("GT-"):
        return False

    try:
        encoded = key[3:]  # Strip GT- prefix
        signature = base64.urlsafe_b64decode(encoded)
        version = get_version()
        payload = _PAYLOAD_TEMPLATE.format(version=version).encode()
        public_key = _get_public_key()
        public_key.verify(signature, payload)
        return True
    except (InvalidSignature, Exception) as e:
        logger.debug(f"License verification failed: {e}")
        return False


def is_production_licensed() -> bool:
    """
    Check if the system has a valid production license.
    Returns True only if a stored key exists, matches current version, and verifies.
    """
    try:
        with get_db() as conn:
            key_row = conn.execute(
                "SELECT value FROM settings WHERE key = 'license_key'"
            ).fetchone()
            ver_row = conn.execute(
                "SELECT value FROM settings WHERE key = 'license_version'"
            ).fetchone()

        if not key_row or not ver_row:
            return False

        # Version must match
        if ver_row["value"] != get_version():
            return False

        return verify_license_key(key_row["value"])
    except Exception as e:
        logger.error(f"License check failed: {e}")
        return False


def store_license_key(key: str) -> bool:
    """
    Validate and store a license key. Returns True on success.
    Key is stored as plaintext (it's a public signature, not a secret).
    """
    if not verify_license_key(key):
        return False

    version = get_version()
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            ("license_key", key),
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            ("license_version", version),
        )
    logger.info(f"Production license stored for v{version}")
    return True


def clear_license():
    """Clear stored license key and version."""
    with get_db() as conn:
        conn.execute("DELETE FROM settings WHERE key = 'license_key'")
        conn.execute("DELETE FROM settings WHERE key = 'license_version'")
    logger.info("Production license cleared")
