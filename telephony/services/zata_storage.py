"""Private, tenant-scoped Zata S3 storage for call recordings."""
import hashlib
import mimetypes
import tempfile
import uuid

from django.utils import timezone

from integrations.utils.encryption import EncryptionError
from telephony.services.crypto import decrypt_secret


class ZataStorageError(Exception):
    pass


def get_storage_credential(tenant_id):
    from telephony.models import ZataStorageCredential

    try:
        return ZataStorageCredential.objects.get(tenant_id=tenant_id, is_active=True)
    except ZataStorageCredential.DoesNotExist as exc:
        raise ZataStorageError('Zata recording storage is not configured for this tenant.') from exc


def _client(credential):
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise ZataStorageError('The server is missing the boto3 storage dependency.') from exc

    try:
        secret = decrypt_secret(_SecretAdapter(credential))
    except EncryptionError as exc:
        raise ZataStorageError(
            'The Zata secret key cannot be decrypted. Re-enter it in Tenant Settings.'
        ) from exc

    return boto3.client(
        's3',
        endpoint_url=credential.endpoint_url.rstrip('/'),
        aws_access_key_id=credential.access_key_id,
        aws_secret_access_key=secret,
        region_name=credential.region_name or 'us-east-1',
        config=Config(
            signature_version='s3v4',
            s3={'addressing_style': 'path'},
            connect_timeout=10,
            read_timeout=60,
            retries={'max_attempts': 3, 'mode': 'standard'},
        ),
    )


class _SecretAdapter:
    """Expose Zata's encrypted field names to the shared envelope decryptor."""

    def __init__(self, credential):
        self.secret_encrypted = credential.secret_access_key_encrypted
        self.dek_wrapped = credential.dek_wrapped


def test_connection(credential):
    """Verify read/write/delete permissions without leaving a permanent object."""
    client = _client(credential)
    prefix = credential.object_prefix.strip('/')
    key = f'{prefix + "/" if prefix else ""}.connection-test-{uuid.uuid4().hex}'
    created = False
    try:
        client.put_object(
            Bucket=credential.bucket_name,
            Key=key,
            Body=b'',
            ContentType='application/octet-stream',
        )
        created = True
        client.head_object(Bucket=credential.bucket_name, Key=key)
    except Exception as exc:
        raise ZataStorageError(f'Could not write to the configured Zata bucket: {exc}') from exc
    finally:
        if created:
            try:
                client.delete_object(Bucket=credential.bucket_name, Key=key)
            except Exception:
                pass
    return True


def archive_telecmi_response(call_log, telecmi_response):
    """Stream a TeleCMI response into Zata and return stored object metadata."""
    credential = get_storage_credential(call_log.tenant_id)
    content_type = (
        (telecmi_response.headers.get('Content-Type') or '').split(';')[0].strip()
        or mimetypes.guess_type(call_log.recording_file or '')[0]
        or 'audio/mpeg'
    )
    extension = mimetypes.guess_extension(content_type) or ''
    if call_log.recording_file and '.' in call_log.recording_file:
        extension = '.' + call_log.recording_file.rsplit('.', 1)[-1].lower()

    call_date = call_log.call_time or timezone.now()
    prefix = credential.object_prefix.strip('/')
    key_parts = [
        prefix,
        str(call_log.tenant_id),
        f'{call_date:%Y}',
        f'{call_date:%m}',
        f'{call_log.cmiuid}{extension}',
    ]
    object_key = '/'.join(part.strip('/') for part in key_parts if part)

    digest = hashlib.sha256()
    size = 0
    with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as spool:
        for chunk in telecmi_response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            digest.update(chunk)
            size += len(chunk)
            spool.write(chunk)
        if size == 0:
            raise ZataStorageError('TeleCMI returned an empty recording.')
        spool.seek(0)
        try:
            _client(credential).upload_fileobj(
                spool,
                credential.bucket_name,
                object_key,
                ExtraArgs={
                    'ContentType': content_type,
                    'ContentDisposition': 'inline',
                    'Metadata': {
                        'tenant-id': str(call_log.tenant_id),
                        'call-id': str(call_log.cmiuid),
                        'sha256': digest.hexdigest(),
                    },
                },
            )
        except ZataStorageError:
            raise
        except Exception as exc:
            raise ZataStorageError(
                f'Could not archive the recording in Zata: {exc}'
            ) from exc

    return {
        'object_key': object_key,
        'content_type': content_type,
        'size': size,
        'sha256': digest.hexdigest(),
    }


def create_presigned_playback_url(call_log, expires_in=600):
    credential = get_storage_credential(call_log.tenant_id)
    if not call_log.recording_object_key:
        raise ZataStorageError('This recording has not been archived to Zata.')
    try:
        return _client(credential).generate_presigned_url(
            'get_object',
            Params={
                'Bucket': credential.bucket_name,
                'Key': call_log.recording_object_key,
                'ResponseContentType': call_log.recording_content_type or 'audio/mpeg',
                'ResponseContentDisposition': 'inline',
            },
            ExpiresIn=expires_in,
        )
    except ZataStorageError:
        raise
    except Exception as exc:
        raise ZataStorageError(
            f'Could not create a secure Zata playback URL: {exc}'
        ) from exc


def get_archived_recording(call_log, range_header=None):
    """Return Zata's streaming object response, optionally for an HTTP byte range."""
    credential = get_storage_credential(call_log.tenant_id)
    params = {
        'Bucket': credential.bucket_name,
        'Key': call_log.recording_object_key,
    }
    if range_header:
        params['Range'] = range_header
    try:
        return _client(credential).get_object(**params)
    except Exception as exc:
        raise ZataStorageError(f'Could not read the archived recording: {exc}') from exc
