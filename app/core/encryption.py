from cryptography.fernet import Fernet

from app.core.config import settings


def get_fernet() -> Fernet:
    """Returns a Fernet instance using the encryption key from settings."""
    if not settings.encryption_key:
        raise ValueError("ENCRYPTION_KEY is not set in environment or config.")
    return Fernet(settings.encryption_key.encode())

def encrypt_token(token: str) -> str:
    """Encrypts a plaintext token."""
    f = get_fernet()
    return f.encrypt(token.encode()).decode()

def decrypt_token(encrypted_token: str) -> str:
    """Decrypts an encrypted token."""
    f = get_fernet()
    return f.decrypt(encrypted_token.encode()).decode()
