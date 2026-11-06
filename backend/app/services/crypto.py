import base64
import hashlib
from cryptography.fernet import Fernet, InvalidToken
from app.config import settings


class CryptoError(Exception):
    """Base exception for cryptographic operations."""
    pass


class ServerEncryptionConfigurationError(CryptoError):
    """Raised when server-side ENCRYPTION_KEY configuration is missing or invalid."""
    pass


class InvalidAssistantKeyError(CryptoError):
    """Raised when the assistant's encrypted token is missing, corrupted, or incompatible."""
    pass


def _get_fernet() -> Fernet:
    raw_key = getattr(settings, "ENCRYPTION_KEY", None)
    if not raw_key or not str(raw_key).strip():
        raise ServerEncryptionConfigurationError(
            "Configuração operacional do servidor inválida: ENCRYPTION_KEY ausente ou vazia."
        )

    key = str(raw_key).strip()
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
    """
    Decrypts an OpenRouter API key only when executing model calls.
    Raises:
    - InvalidAssistantKeyError if the key is missing, empty, corrupted, or incompatible.
    - ServerEncryptionConfigurationError if the server's ENCRYPTION_KEY is misconfigured.
    """
    if not encrypted_key or not encrypted_key.strip():
        raise InvalidAssistantKeyError("A chave criptografada do assistente está ausente ou vazia.")
    fernet = _get_fernet()
    try:
        decrypted = fernet.decrypt(encrypted_key.encode("utf-8"))
        return decrypted.decode("utf-8")
    except InvalidToken as e:
        raise InvalidAssistantKeyError(
            "InvalidToken: token de criptografia do assistente inválido ou incompatível com a chave do servidor."
        ) from e
