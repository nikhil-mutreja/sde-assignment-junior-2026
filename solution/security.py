"""Security utilities for PII encryption at rest and deterministic phone hashing.

Uses AES-256-GCM for randomised field-level encryption with a 12-byte nonce.
Uses HMAC-SHA256 for deterministic matching of canonical phone numbers.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from solution.config import PHONE_HMAC_KEY_HEX, PII_ENCRYPTION_KEY_HEX


def get_encryption_key(key_hex: Optional[str] = None) -> bytes:
    """Retrieve the 32-byte AES-256 encryption key from hex string."""
    hex_str = key_hex or PII_ENCRYPTION_KEY_HEX
    try:
        key_bytes = bytes.fromhex(hex_str.strip())
    except (ValueError, TypeError) as exc:
        raise ValueError("Encryption key must be a valid 64-character hexadecimal string") from exc
    if len(key_bytes) != 32:
        raise ValueError(
            f"AES-256 key must be exactly 32 bytes (64 hex characters), got {len(key_bytes)}"
        )
    return key_bytes


def get_hmac_key(key_hex: Optional[str] = None) -> bytes:
    """Retrieve the HMAC key from hex string."""
    hex_str = key_hex or PHONE_HMAC_KEY_HEX
    try:
        key_bytes = bytes.fromhex(hex_str.strip())
    except (ValueError, TypeError) as exc:
        raise ValueError("HMAC key must be a valid 64-character hexadecimal string") from exc
    if len(key_bytes) < 32:
        raise ValueError(
            f"HMAC key should be at least 32 bytes (64 hex characters), got {len(key_bytes)}"
        )
    return key_bytes


def encrypt_string(plaintext: str, key_hex: Optional[str] = None) -> str:
    """Encrypt a string using AES-256-GCM with a random 12-byte nonce.

    Returns base64-encoded string containing (nonce || ciphertext || tag).
    """
    if plaintext is None:
        raise ValueError("Cannot encrypt None")
    key = get_encryption_key(key_hex)
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)  # 96-bit standard nonce for GCM
    data = plaintext.encode("utf-8")
    ct = aesgcm.encrypt(nonce, data, None)
    return base64.b64encode(nonce + ct).decode("utf-8")


def decrypt_string(ciphertext_b64: str, key_hex: Optional[str] = None) -> str:
    """Decrypt a base64-encoded AES-256-GCM ciphertext.

    Extracts 12-byte nonce and verifies GCM authentication tag.
    """
    if not ciphertext_b64:
        raise ValueError("Cannot decrypt empty string")
    key = get_encryption_key(key_hex)
    raw = base64.b64decode(ciphertext_b64.encode("utf-8"))
    if len(raw) < 12 + 16:  # Nonce (12) + at least 16-byte tag
        raise ValueError("Ciphertext is too short to be valid AES-GCM data")
    nonce = raw[:12]
    ct = raw[12:]
    aesgcm = AESGCM(key)
    decrypted_bytes = aesgcm.decrypt(nonce, ct, None)
    return decrypted_bytes.decode("utf-8")


def compute_phone_hash(canonical_phone: str, key_hex: Optional[str] = None) -> str:
    """Compute deterministic HMAC-SHA256 hex digest of a canonical phone number.

    Phone numbers must already be canonicalised (e.g. '+91XXXXXXXXXX') before hashing.
    """
    if not canonical_phone:
        raise ValueError("Canonical phone cannot be empty for HMAC generation")
    key = get_hmac_key(key_hex)
    return hmac.new(key, canonical_phone.strip().encode("utf-8"), hashlib.sha256).hexdigest()
