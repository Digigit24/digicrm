"""
Per-tenant envelope encryption for TeleCMI credentials.

Why this exists
---------------
The shared `integrations.utils.encryption` module derives one Fernet key from
`SECRET_KEY`. Any environment pointed at the same database with a different
`SECRET_KEY` silently writes ciphertext the others cannot read — the failure
mode being "TeleCMI credentials cannot be decrypted" on the recording endpoint,
which is unrecoverable without re-entering the secret.

The scheme here
---------------
Two layers:

  * DEK  — a Fernet key generated per tenant, stored on the credential row as
           `dek_wrapped`. It never appears in plaintext at rest and is never
           entered by a human; the server mints it the first time a tenant
           saves a secret.
  * KEK  — one master key from settings (`TELECMI_MASTER_KEY`). It wraps every
           tenant's DEK. This is a single constant shared by every deployment
           reading the database, and it never changes per tenant.

The app secret is encrypted with the tenant's DEK; the DEK is encrypted with
the KEK. A database dump alone yields nothing without the KEK, and rotating one
tenant's key does not touch any other tenant.

Backward compatibility
----------------------
Rows written before this scheme have an empty `dek_wrapped`. `decrypt_secret()`
falls back to the legacy `SECRET_KEY`-derived path for those, so nothing breaks
mid-rollout; the row upgrades to envelope encryption the next time its secret
is saved. See `needs_upgrade()`.
"""
import base64
import logging

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from django.conf import settings

from integrations.utils.encryption import EncryptionError, decrypt_token

logger = logging.getLogger(__name__)


def _coerce_fernet_key(raw: bytes) -> bytes:
    """Accept either a real 44-char Fernet key or arbitrary material."""
    if len(raw) == 44:
        return raw
    derived = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b'telecmi-master-key',
        iterations=100_000,
    ).derive(raw)
    return base64.urlsafe_b64encode(derived)


def _master_cipher() -> Fernet:
    """
    Resolve the KEK.

    Order: TELECMI_MASTER_KEY -> INTEGRATION_ENCRYPTION_KEY -> SECRET_KEY.

    The last fallback exists only so a misconfigured box still boots; it
    reintroduces the exact cross-environment fragility this module removes, so
    it warns loudly. Set TELECMI_MASTER_KEY in every environment.
    """
    key = getattr(settings, 'TELECMI_MASTER_KEY', None) or getattr(
        settings, 'INTEGRATION_ENCRYPTION_KEY', None
    )
    if not key:
        logger.warning(
            'TELECMI_MASTER_KEY is not set — falling back to SECRET_KEY. Any '
            'environment sharing this database with a different SECRET_KEY will '
            'be unable to read TeleCMI secrets.'
        )
        key = settings.SECRET_KEY

    raw = key.encode() if isinstance(key, str) else key
    try:
        return Fernet(_coerce_fernet_key(raw))
    except Exception as exc:  # malformed key material
        raise EncryptionError(f'Invalid TeleCMI master key: {exc}')


def generate_dek() -> bytes:
    """Mint a fresh per-tenant data key."""
    return Fernet.generate_key()


def wrap_dek(dek: bytes) -> str:
    """Encrypt a tenant DEK with the master key, for storage."""
    return _master_cipher().encrypt(dek).decode('utf-8')


def unwrap_dek(dek_wrapped: str) -> bytes:
    """Recover a tenant DEK from its stored wrapped form."""
    try:
        return _master_cipher().decrypt(dek_wrapped.encode('utf-8'))
    except InvalidToken:
        raise EncryptionError(
            'The stored tenant key cannot be unwrapped with this environment\'s '
            'TELECMI_MASTER_KEY.'
        )


def encrypt_secret(plaintext: str, dek_wrapped: str = '') -> tuple[str, str]:
    """
    Encrypt an app secret for storage.

    Reuses the tenant's existing DEK when one is present so that rotating the
    secret does not invalidate anything else keyed to it; mints a new DEK
    otherwise. Returns `(secret_encrypted, dek_wrapped)` — persist both.
    """
    if dek_wrapped:
        dek = unwrap_dek(dek_wrapped)
    else:
        dek = generate_dek()
        dek_wrapped = wrap_dek(dek)

    secret_encrypted = Fernet(dek).encrypt(plaintext.encode('utf-8')).decode('utf-8')
    return secret_encrypted, dek_wrapped


def decrypt_secret(credential) -> str:
    """
    Decrypt a TeleCMICredential's app secret.

    Falls back to the legacy shared-key path for rows that predate envelope
    encryption, so a rollout needs no data migration.
    """
    if not credential.secret_encrypted:
        raise EncryptionError('No TeleCMI secret is stored for this tenant.')

    if not credential.dek_wrapped:
        # Legacy row: encrypted with the SECRET_KEY-derived shared key.
        return decrypt_token(credential.secret_encrypted)

    dek = unwrap_dek(credential.dek_wrapped)
    try:
        return Fernet(dek).decrypt(credential.secret_encrypted.encode('utf-8')).decode('utf-8')
    except InvalidToken:
        raise EncryptionError(
            'The stored TeleCMI secret does not match this tenant\'s key.'
        )


def needs_upgrade(credential) -> bool:
    """True when a row is still on the legacy shared-key scheme."""
    return bool(credential.secret_encrypted) and not credential.dek_wrapped
