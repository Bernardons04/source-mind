import base64
import hashlib
from cryptography.fernet import Fernet
from app.config import settings


def _get_fernet() -> Fernet:
    key = settings.ENCRYPTION_KEY.strip()
    try:
        # Check if already a valid 32-byte base64 Fernet key
        return Fernet(key.encode("utf-8"))
    except Exception:
        # Derive a 32-byte urlsafe base64 key using sha256
        digest = hashlib.sha256(key.encode("utf-8")).digest()
        urlsafe_key = base64.urlsafe_b64encode(digest)
        return Fernet(urlsafe_key)


def encrypt_api_key(plain_key: str) -> str:
    """Encrypts an OpenRouter API key before persisting to DB."""
    if not plain_key:
        return ""
    fernet = _get_fernet()
    encrypted = fernet.encrypt(plain_key.strip().encode("utf-8"))
    return encrypted.decode("utf-8")


def decrypt_api_key(encrypted_key: str) -> str:
    """Decrypts an OpenRouter API key only when executing model calls."""
    if not encrypted_key:
        return ""
    fernet = _get_fernet()
    decrypted = fernet.decrypt(encrypted_key.encode("utf-8"))
    return decrypted.decode("utf-8")
