"""AES-256-GCM encryption for OAuth tokens stored in Supabase."""
import os
import base64
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from ..lib.config import settings


def _key() -> bytes:
    return bytes.fromhex(settings.TOKEN_ENCRYPTION_KEY)


def encrypt(plaintext: str) -> str:
    """Returns base64(nonce + ciphertext)."""
    nonce = os.urandom(12)
    aesgcm = AESGCM(_key())
    ct = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(nonce + ct).decode()


def decrypt(encrypted: str) -> str:
    """Decrypts base64(nonce + ciphertext) back to plaintext."""
    raw = base64.b64decode(encrypted)
    nonce, ct = raw[:12], raw[12:]
    aesgcm = AESGCM(_key())
    return aesgcm.decrypt(nonce, ct, None).decode()
