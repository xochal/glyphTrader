#!/usr/bin/env python3
"""
glyphTrader license key generator (offline tool).

Usage:
  # First time: generate Ed25519 keypair
  python generate_license.py --generate-keypair

  # Generate license key for a specific version
  python generate_license.py --version 3.0.0

  # Generate license key using VERSION file
  python generate_license.py
"""

import argparse
import base64
import os
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from cryptography.hazmat.primitives import serialization


PRIVATE_KEY_PATH = os.path.join(os.path.dirname(__file__), "license_private_key.pem")
VERSION_PATH = os.path.join(os.path.dirname(__file__), "..", "VERSION")
PAYLOAD_TEMPLATE = "glyphTrader:live:{version}"


def generate_keypair():
    """Generate Ed25519 keypair. Saves private key to PEM file, prints public key hex."""
    if os.path.exists(PRIVATE_KEY_PATH):
        print(f"ERROR: Private key already exists at {PRIVATE_KEY_PATH}")
        print("Delete it first if you want to regenerate (this will invalidate ALL existing keys).")
        sys.exit(1)

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    # Save private key
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    with open(PRIVATE_KEY_PATH, "wb") as f:
        f.write(pem)
    os.chmod(PRIVATE_KEY_PATH, 0o600)

    # Print public key hex for embedding in license.py
    pub_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    pub_hex = pub_bytes.hex()

    print(f"Keypair generated successfully.")
    print(f"Private key saved to: {PRIVATE_KEY_PATH}")
    print(f"\nEmbed this public key hex in backend/license.py:")
    print(f'_PUBLIC_KEY_HEX = "{pub_hex}"')


def load_private_key() -> Ed25519PrivateKey:
    """Load private key from PEM file."""
    if not os.path.exists(PRIVATE_KEY_PATH):
        print(f"ERROR: No private key at {PRIVATE_KEY_PATH}")
        print("Run with --generate-keypair first.")
        sys.exit(1)

    with open(PRIVATE_KEY_PATH, "rb") as f:
        private_key = serialization.load_pem_private_key(f.read(), password=None)
    return private_key


def get_version(version_arg: str | None) -> str:
    """Get version from arg or VERSION file."""
    if version_arg:
        return version_arg.strip()
    if os.path.exists(VERSION_PATH):
        with open(VERSION_PATH) as f:
            return f.read().strip()
    print("ERROR: No --version specified and no VERSION file found.")
    sys.exit(1)


def generate_license_key(version: str) -> str:
    """Sign the payload and return a GT-prefixed license key."""
    private_key = load_private_key()
    payload = PAYLOAD_TEMPLATE.format(version=version).encode()
    signature = private_key.sign(payload)
    encoded = base64.urlsafe_b64encode(signature).decode()
    return f"GT-{encoded}"


def main():
    parser = argparse.ArgumentParser(description="glyphTrader license key generator")
    parser.add_argument("--generate-keypair", action="store_true", help="Generate new Ed25519 keypair")
    parser.add_argument("--version", type=str, default=None, help="Version to sign (default: reads VERSION file)")
    args = parser.parse_args()

    if args.generate_keypair:
        generate_keypair()
        return

    version = get_version(args.version)
    key = generate_license_key(version)

    print(f"Version: {version}")
    print(f"Payload: {PAYLOAD_TEMPLATE.format(version=version)}")
    print(f"\nLicense Key:")
    print(key)
    print(f"\nKey length: {len(key)} chars")


if __name__ == "__main__":
    main()
