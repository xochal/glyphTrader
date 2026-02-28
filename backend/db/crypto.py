"""
Fernet encryption with Argon2id key derivation.
Key derived from admin password — lives only in memory, never on disk.
"""

import os
import base64
import logging
from typing import Optional

from argon2.low_level import hash_secret_raw, Type
from cryptography.fernet import Fernet

logger = logging.getLogger("glyphTrader.crypto")

# Module-level cache — single worker only
_fernet_key: Optional[bytes] = None
_fernet: Optional[Fernet] = None

# Locked Argon2id parameters (changing invalidates all encrypted data)
ARGON2_TIME_COST = 3
ARGON2_MEMORY_COST = 65536  # 64 MB
ARGON2_PARALLELISM = 4
ARGON2_HASH_LEN = 32


def derive_key(password: str, salt: bytes) -> bytes:
    raw_hash = hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=salt,
        time_cost=ARGON2_TIME_COST,
        memory_cost=ARGON2_MEMORY_COST,
        parallelism=ARGON2_PARALLELISM,
        hash_len=ARGON2_HASH_LEN,
        type=Type.ID,
    )
    return base64.urlsafe_b64encode(raw_hash[:32])


def unlock(password: str, salt: bytes):
    global _fernet_key, _fernet
    _fernet_key = derive_key(password, salt)
    _fernet = Fernet(_fernet_key)
    logger.info("System unlocked — Fernet key derived and cached")


def lock():
    global _fernet_key, _fernet
    _fernet_key = None
    _fernet = None
    logger.info("System locked — Fernet key zeroed")


def is_unlocked() -> bool:
    return _fernet is not None


def encrypt(plaintext: str) -> str:
    if not _fernet:
        raise RuntimeError("System is locked — cannot encrypt")
    return _fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(ciphertext: str) -> str:
    if not _fernet:
        raise RuntimeError("System is locked — cannot decrypt")
    return _fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")


def generate_salt() -> bytes:
    return os.urandom(32)


def encrypt_with_key(plaintext: str, key: str) -> str:
    """Encrypt with an arbitrary key (for degraded-mode token)."""
    fkey = base64.urlsafe_b64encode(key.encode("utf-8").ljust(32, b"\0")[:32])
    f = Fernet(fkey)
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_with_key(ciphertext: str, key: str) -> str:
    """Decrypt with an arbitrary key (for degraded-mode token)."""
    fkey = base64.urlsafe_b64encode(key.encode("utf-8").ljust(32, b"\0")[:32])
    f = Fernet(fkey)
    return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
