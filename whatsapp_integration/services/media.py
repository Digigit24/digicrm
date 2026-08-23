"""
Authenticated media references.

Laravel's ``GET /api/{vendorUid}/media/{filename}`` is an unauthenticated
arbitrary file read: the handler does ``public_path($filename)`` with the route
constrained to ``->where('filename', '.*')`` and no traversal guard, and
``$vendorUid`` is never used.  ``/api/x/media/../.env`` yields ``APP_KEY``,
which decrypts every vendor's Meta token.  The frontend must never be pointed
at that route, and DigiCRM must never proxy a client-supplied path into it.

So media references handed to the client are **opaque, signed ids**:

    <base64url(path)>.<hmac-sha256(tenant_id, path)[:32]>

Properties:

* Deterministic — the same message always yields the same URL, so the browser
  and CDN can cache it.
* Tenant-bound — an id minted for tenant A does not verify for tenant B, so
  ids cannot be traded between tenants even though both resolve against the
  same Laravel vendor today.
* Unforgeable — a caller cannot mint an id for ``../.env``; only paths this
  server extracted from a Laravel message payload are ever signed.

Traversal is *also* rejected structurally on both the mint and the redeem side,
so a bug in one layer does not become a file read.
"""

import base64
import hashlib
import hmac
import logging
import posixpath

from django.conf import settings

logger = logging.getLogger(__name__)

_SALT = b'whatsapp.media.v1'

# Content types we are willing to hand back to a browser. Anything else is
# served as application/octet-stream. Combined with the forced
# Content-Disposition: attachment and X-Content-Type-Options: nosniff, this
# stops a stored .html/.svg from becoming stored XSS on the CRM origin.
ALLOWED_CONTENT_TYPES = {
    # images
    'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/bmp',
    # audio
    'audio/aac', 'audio/mp4', 'audio/mpeg', 'audio/amr', 'audio/ogg',
    'audio/opus', 'audio/wav', 'audio/webm',
    # video
    'video/mp4', 'video/3gpp', 'video/quicktime', 'video/webm',
    # documents
    'application/pdf',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/vnd.ms-powerpoint',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'text/plain', 'text/csv',
    # stickers
    'image/webp',
}

FALLBACK_CONTENT_TYPE = 'application/octet-stream'

MAX_MEDIA_BYTES = 64 * 1024 * 1024  # WhatsApp's own ceiling is well under this


class MediaReferenceError(Exception):
    """Raised when a media id is missing, malformed, or fails verification."""


def is_safe_media_path(path: str) -> bool:
    """
    Structural rejection of anything that could escape Laravel's public dir.

    Rejects traversal, absolute paths, backslashes, NULs, schemes, protocol
    -relative URLs and control characters.  Applied on BOTH mint and redeem.
    """
    if not path or not isinstance(path, str):
        return False
    if len(path) > 512:
        return False
    if '\x00' in path or '\\' in path:
        return False
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in path):
        return False
    if path.startswith('/') or path.startswith('//'):
        return False
    if '://' in path:
        return False
    # Normalising must not move the path — that catches "..", "a/../..",
    # "./../", percent-free traversal of every shape.
    if posixpath.normpath(path) != path:
        return False
    if path == '..' or path.startswith('../') or '/../' in path or path.endswith('/..'):
        return False
    return True


def _secret() -> bytes:
    return (getattr(settings, 'SECRET_KEY', '') or '').encode('utf-8')


def _signature(tenant_id, path: str) -> str:
    mac = hmac.new(_secret(), _SALT, hashlib.sha256)
    mac.update(str(tenant_id).encode('utf-8'))
    mac.update(b'\x1f')
    mac.update(path.encode('utf-8'))
    return mac.hexdigest()[:32]


def _b64encode(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode('utf-8')).decode('ascii').rstrip('=')


def _b64decode(value: str) -> str:
    padding = '=' * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding).decode('utf-8')


def make_media_id(tenant_id, path: str) -> str:
    """
    Mint an opaque media id, or raise if the path is not safe to serve.

    Callers that are normalising a message should use ``media_url`` instead,
    which degrades to ``None`` rather than raising — one weird attachment must
    never take down a whole conversation.
    """
    if not is_safe_media_path(path):
        raise MediaReferenceError('Unsafe media path')
    return f'{_b64encode(path)}.{_signature(tenant_id, path)}'


def resolve_media_id(tenant_id, media_id: str) -> str:
    """Verify a media id and return the Laravel path it points at."""
    if not media_id or not isinstance(media_id, str) or len(media_id) > 1024:
        raise MediaReferenceError('Invalid media id')

    encoded, _, signature = media_id.rpartition('.')
    if not encoded or not signature:
        raise MediaReferenceError('Invalid media id')

    try:
        path = _b64decode(encoded)
    except Exception:
        raise MediaReferenceError('Invalid media id')

    if not hmac.compare_digest(signature, _signature(tenant_id, path)):
        raise MediaReferenceError('Invalid media id')

    # Defence in depth: even a correctly signed id must still be a sane path.
    if not is_safe_media_path(path):
        raise MediaReferenceError('Unsafe media path')

    return path


def media_url(tenant_id, path: str):
    """Return the DigiCRM-proxied URL for a Laravel media path, or None."""
    if not path:
        return None
    try:
        return f'/api/whatsapp/media/{make_media_id(tenant_id, path)}/'
    except MediaReferenceError:
        logger.warning('Refusing to mint a media id for unsafe path %r', path)
        return None


def safe_content_type(content_type: str) -> str:
    """Collapse anything not on the allowlist to octet-stream."""
    if not content_type:
        return FALLBACK_CONTENT_TYPE
    base = content_type.split(';', 1)[0].strip().lower()
    return base if base in ALLOWED_CONTENT_TYPES else FALLBACK_CONTENT_TYPE


def safe_filename(path: str) -> str:
    """Last path segment, stripped of anything a header should not carry."""
    name = posixpath.basename(path or '') or 'download'
    name = name.replace('"', '').replace('\r', '').replace('\n', '')
    return name[:200] or 'download'
