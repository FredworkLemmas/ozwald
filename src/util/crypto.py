import base64
import hashlib
import json
from typing import Any, Dict

from cryptography.fernet import Fernet


def _derive_key(token: str) -> bytes:
    """Derive a 32-byte base64 encoded key from a string token."""
    digest = hashlib.sha256(token.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_payload(payload: Dict[str, Any], token: str) -> str:
    """Encrypt a dictionary payload using a token.

    Args:
        payload: The dictionary to encrypt.
        token: The user-provided token.

    Returns:
        A base64-encoded encrypted string.
    """
    key = _derive_key(token)
    f = Fernet(key)
    json_payload = json.dumps(payload).encode()
    encrypted = f.encrypt(json_payload)
    return encrypted.decode()


def decrypt_payload(encrypted_blob: str, token: str) -> Dict[str, Any]:
    """Decrypt an encrypted blob using a token.

    Args:
        encrypted_blob: The base64-encoded encrypted string.
        token: The user-provided token.

    Returns:
        The decrypted dictionary.

    Raises:
        cryptography.fernet.InvalidToken: If decryption fails.
    """
    key = _derive_key(token)
    f = Fernet(key)
    decrypted = f.decrypt(encrypted_blob.encode())
    return json.loads(decrypted.decode())
