"""
Encryption utilities for securely storing OAuth tokens and sensitive data.

Uses Fernet symmetric encryption from the cryptography library.
Encryption key should be stored in environment variables.
"""

import base64
import logging
from typing import Optional
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2
from django.conf import settings

logger = logging.getLogger(__name__)


class EncryptionError(Exception):
    """Custom exception for encryption errors"""
    pass


class TokenEncryptor:
    """
    Handles encryption and decryption of sensitive tokens.

    Uses Fernet (symmetric encryption) with a key derived from
    the Django SECRET_KEY and a salt.
    """

    def __init__(self):
        """Initialize the encryptor with a derived key"""
        self._cipher = self._get_cipher()

    def _get_cipher(self) -> Fernet:
        """
        Create a Fernet cipher using a key derived from SECRET_KEY.

        Returns:
            Fernet: Configured Fernet cipher instance

        Raises:
            EncryptionError: If cipher creation fails
        """
        try:
            # Get encryption key from settings or derive from SECRET_KEY
            encryption_key = getattr(settings, 'INTEGRATION_ENCRYPTION_KEY', None)

            if encryption_key:
                # Use provided encryption key
                key = encryption_key.encode() if isinstance(encryption_key, str) else encryption_key
            else:
                # Derive key from SECRET_KEY
                key = self._derive_key_from_secret()

            # Ensure key is properly formatted for Fernet
            if len(key) != 44:  # Fernet key must be 44 bytes (base64-encoded 32 bytes)
                # Use KDF to generate proper key
                kdf = PBKDF2(
                    algorithm=hashes.SHA256(),
                    length=32,
                    salt=b'integration-salt',  # Static salt for consistency
                    iterations=100000,
                )
                derived_key = kdf.derive(key)
                key = base64.urlsafe_b64encode(derived_key)

            return Fernet(key)

        except Exception as e:
            logger.error(f"Failed to create encryption cipher: {e}")
            raise EncryptionError(f"Cipher creation failed: {e}")

    def _derive_key_from_secret(self) -> bytes:
        """
        Derive an encryption key from Django SECRET_KEY.

        Returns:
            bytes: Derived encryption key
        """
        secret = settings.SECRET_KEY.encode()

        # Use PBKDF2 to derive a proper encryption key
        kdf = PBKDF2(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b'django-integrations',
            iterations=100000,
        )

        return kdf.derive(secret)

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt a plaintext string.

        Args:
            plaintext: The string to encrypt

        Returns:
            str: Base64-encoded encrypted string

        Raises:
            EncryptionError: If encryption fails
        """
        if not plaintext:
            return ""

        try:
            plaintext_bytes = plaintext.encode('utf-8')
            encrypted_bytes = self._cipher.encrypt(plaintext_bytes)
            return encrypted_bytes.decode('utf-8')

        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            raise EncryptionError(f"Failed to encrypt data: {e}")

    def decrypt(self, ciphertext: str) -> str:
        """
        Decrypt a ciphertext string.

        Args:
            ciphertext: The encrypted string to decrypt

        Returns:
            str: Decrypted plaintext string

        Raises:
            EncryptionError: If decryption fails
        """
        if not ciphertext:
            return ""

        try:
            ciphertext_bytes = ciphertext.encode('utf-8')
            decrypted_bytes = self._cipher.decrypt(ciphertext_bytes)
            return decrypted_bytes.decode('utf-8')

        except InvalidToken:
            logger.error("Invalid token or key - decryption failed")
            raise EncryptionError("Invalid encrypted data or encryption key")

        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            raise EncryptionError(f"Failed to decrypt data: {e}")


# Singleton instance for global use
_encryptor_instance: Optional[TokenEncryptor] = None


def get_encryptor() -> TokenEncryptor:
    """
    Get the singleton TokenEncryptor instance.

    Returns:
        TokenEncryptor: The encryption instance
    """
    global _encryptor_instance

    if _encryptor_instance is None:
        _encryptor_instance = TokenEncryptor()

    return _encryptor_instance


def encrypt_token(token: str) -> str:
    """
    Convenience function to encrypt a token.

    Args:
        token: The token to encrypt

    Returns:
        str: Encrypted token

    Example:
        >>> encrypted = encrypt_token("my-secret-token")
    """
    encryptor = get_encryptor()
    return encryptor.encrypt(token)


def decrypt_token(encrypted_token: str) -> str:
    """
    Convenience function to decrypt a token.

    Args:
        encrypted_token: The encrypted token

    Returns:
        str: Decrypted token

    Example:
        >>> decrypted = decrypt_token(encrypted_token)
    """
    encryptor = get_encryptor()
    return encryptor.decrypt(encrypted_token)


def generate_encryption_key() -> str:
    """
    Generate a new Fernet encryption key.

    This should only be used once during initial setup.
    The key should be stored securely in environment variables.

    Returns:
        str: Base64-encoded encryption key

    Example:
        >>> key = generate_encryption_key()
        >>> # Save this key to your .env file as INTEGRATION_ENCRYPTION_KEY
    """
    key = Fernet.generate_key()
    return key.decode('utf-8')
