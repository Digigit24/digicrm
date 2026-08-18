"""
Call log service — bridges TeleCMI CDR data and the CRM.

Responsibilities:
- Sync CDR from TeleCMI REST API (manual/scheduled)
- Process inbound webhook CDR payloads (real-time)
- Match from/to phone numbers to CRM Leads
- Create LeadActivity records for completed calls
- Upsert CallLog records (idempotent via cmiuid)
"""
import logging
import json
import re
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone as dt_timezone

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

# TeleCMI timestamps are in UTC milliseconds
MS_PER_SECOND = 1000
VALID_RECORDING_RE = re.compile(r'^[A-Za-z0-9_.\-/]+\.(?:mp3|wav|ogg|m4a)$', re.IGNORECASE)


def set_call_outcome(call_log_id, tenant_id, outcome, note, user_id):
    """Set an agent disposition on a tenant-scoped call log."""
    from telephony.models import CallLog

    call_log = CallLog.objects.get(id=call_log_id, tenant_id=tenant_id)
    call_log.call_outcome = outcome
    call_log.call_outcome_note = note
    call_log.call_outcome_set_at = timezone.now()
    call_log.save(update_fields=[
        'call_outcome',
        'call_outcome_note',
        'call_outcome_set_at',
        'updated_at',
    ])
    return call_log


def _first_value(payload, *keys, default=None):
    for key in keys:
        value = payload.get(key)
        if value is not None and value != '':
            return value
    return default


def _as_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_decimal(value, default=Decimal('0')):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _recording_filename(raw_cdr):
    """Return TeleCMI's filename; `record` is only an enabled flag."""
    value = _first_value(raw_cdr, 'filename', 'recording_file', 'file', default='')
    if isinstance(value, bool):
        return ''
    value = str(value).strip()
    if value.lower() in {'true', 'false', '1', '0', 'yes', 'no'}:
        return ''
    return value if VALID_RECORDING_RE.match(value) else ''


def _payload_lead_id(raw_cdr):
    for key in ('extra_params', 'custom'):
        value = raw_cdr.get(key)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                value = None
        if isinstance(value, dict) and value.get('lead_id'):
            return _as_int(value.get('lead_id'), None)
    return None


def _resolve_agent_user_id(tenant_id, raw_cdr, explicit_user_id=None):
    if explicit_user_id:
        return explicit_user_id
    telecmi_user_id = _first_value(raw_cdr, 'user', 'agent')
    if not telecmi_user_id:
        return None
    from telephony.models import TeleCMIAgent
    return (
        TeleCMIAgent.objects.filter(
            tenant_id=tenant_id,
            telecmi_user_id=str(telecmi_user_id),
            is_active=True,
        ).values_list('user_id', flat=True).first()
    )


def _queue_recording_archive(call_log):
    from telephony.models import ZataStorageCredential, RecordingStorageStatusEnum

    if not call_log.recording_file or not ZataStorageCredential.objects.filter(
        tenant_id=call_log.tenant_id, is_active=True
    ).exists():
        return
    if call_log.recording_storage_status == RecordingStorageStatusEnum.ARCHIVED:
        return
    CallLog = call_log.__class__
    CallLog.objects.filter(pk=call_log.pk).update(
        recording_storage_status=RecordingStorageStatusEnum.PENDING,
        recording_archive_error='',
    )

    def enqueue():
        try:
            from telephony.tasks import archive_call_recording
            archive_call_recording.delay(call_log.pk)
        except Exception as exc:
            logger.error('Could not queue recording archive for call %s: %s', call_log.pk, exc)

    transaction.on_commit(enqueue)


def process_cdr_record(
    tenant_id,
    raw_cdr: dict,
    direction: str = None,
    synced_via: str = 'webhook',
    agent_user_id=None,
    queue_archive: bool = False,
) -> 'CallLog':
    """
    Create or update a CallLog from a single TeleCMI CDR dict.
    Also creates a LeadActivity if a matching Lead is found.

    direction: 'inbound' or 'outbound'
    synced_via: 'webhook' or 'manual_sync'

    Returns the CallLog instance.
    """
    from telephony.models import CallLog, CallDirectionEnum, CallTypeEnum
    from crm.models import LeadActivity, ActivityTypeEnum

    cmiuid = _first_value(raw_cdr, 'cmiuid', 'cmiuuid')
    if not cmiuid:
        logger.warning('CDR record missing cmiuid/cmiuuid; keys=%s', sorted(raw_cdr.keys()))
        return None

    direction = str(raw_cdr.get('direction') or direction or '').lower()
    if direction not in (CallDirectionEnum.INBOUND, CallDirectionEnum.OUTBOUND):
        direction = CallDirectionEnum.INBOUND
    duration = _as_int(_first_value(raw_cdr, 'duration', 'answeredsec', 'answered_sec', default=0))
    status_value = str(raw_cdr.get('status') or '').lower()
    missed_statuses = {'missed', 'failed', 'no-answer', 'no_answer', 'busy', 'rejected'}
    call_type = CallTypeEnum.MISSED if status_value in missed_statuses or duration <= 0 else CallTypeEnum.ANSWERED
    from_number = str(raw_cdr.get('from') or '')
    to_number = str(raw_cdr.get('to') or from_number)
    call_time_ms = _as_int(_first_value(raw_cdr, 'time', 'start_time', default=0))
    if call_time_ms <= 0:
        call_time = timezone.now()
    else:
        if call_time_ms < 10_000_000_000:
            call_time_ms *= MS_PER_SECOND
        call_time = datetime.fromtimestamp(call_time_ms / MS_PER_SECOND, tz=dt_timezone.utc)

    # Find matching lead by phone number (tenant-scoped)
    lead_id = _payload_lead_id(raw_cdr) or _find_lead_id(
        tenant_id, from_number if direction == 'inbound' else to_number
    )
    recording_file = _recording_filename(raw_cdr)
    agent_user_id = _resolve_agent_user_id(tenant_id, raw_cdr, agent_user_id)
    telecmi_call_id = _first_value(raw_cdr, 'call_id')
    conversation_uuid = _first_value(raw_cdr, 'conversation_uuid')
    call_leg = str(raw_cdr.get('leg') or '').lower() or None

    normalized = {
        'direction': direction,
        'call_type': call_type,
        'from_number': from_number,
        'to_number': to_number,
        'duration': duration,
        'billed_sec': _as_int(_first_value(raw_cdr, 'billedsec', 'billed_sec', default=0)),
        'rate': _as_decimal(_first_value(raw_cdr, 'rate', 'call_rate', default=0)),
        'caller_name': raw_cdr.get('name') or '',
        'telecmi_notes': raw_cdr.get('notes'),
        'recording_file': recording_file or None,
        'call_time': call_time,
        'lead_id': lead_id,
        'agent_user_id': agent_user_id,
        'synced_via': synced_via,
        'call_leg': call_leg,
        'telecmi_call_id': telecmi_call_id,
        'conversation_uuid': conversation_uuid,
        'request_id': _first_value(raw_cdr, 'request_id'),
        'ivr_name': _first_value(raw_cdr, 'ivr_name'),
        'team_name': _first_value(raw_cdr, 'team', 'team_name'),
        'is_voicemail': bool(raw_cdr.get('voicemail')),
        'voicemail_filename': _first_value(raw_cdr, 'voicename'),
        'wait_seconds': _as_int(_first_value(raw_cdr, 'waitedsec', 'wait_seconds'), None),
        'hangup_reason': _first_value(raw_cdr, 'hangup_reason'),
        'raw_payload': raw_cdr,
    }

    with transaction.atomic():
        log = CallLog.objects.select_for_update().filter(
            tenant_id=tenant_id, cmiuid=str(cmiuid)
        ).first()
        if not log and telecmi_call_id:
            log = CallLog.objects.select_for_update().filter(
                tenant_id=tenant_id, telecmi_call_id=str(telecmi_call_id)
            ).order_by('-call_leg', '-created_at').first()

        created = log is None
        if created:
            log = CallLog.objects.create(tenant_id=tenant_id, cmiuid=str(cmiuid), **normalized)
        else:
            # Leg B is the customer leg and contains the final destination and recording.
            if call_leg == 'b' and log.cmiuid != str(cmiuid):
                log.cmiuid = str(cmiuid)
            for field, value in normalized.items():
                if value is not None and value != '':
                    setattr(log, field, value)
            log.save()

    # Create a CRM Activity if we have a lead and haven't done it yet
    if (
        lead_id
        and not log.activity_created
        and not (direction == CallDirectionEnum.OUTBOUND and call_leg == 'a')
    ):
        _create_call_activity(tenant_id, log, lead_id)
        log.activity_created = True
        log.save(update_fields=['activity_created', 'updated_at'])

    if queue_archive and recording_file:
        _queue_recording_archive(log)

    return log


def _find_lead_id(tenant_id, phone_number: str):
    """
    Find a Lead in this tenant whose phone matches the given number.
    Strips non-digit characters before comparing.
    Returns lead.id or None.
    """
    from crm.models import Lead

    if not phone_number:
        return None

    digits = ''.join(c for c in phone_number if c.isdigit())
    if not digits:
        return None

    # Try exact match first, then compare normalized digits. CRM phone numbers
    # are commonly stored as "+91 98765-43210" while TeleCMI returns
    # "919876543210"; a raw `endswith` lookup cannot match those separators.
    lead = (
        Lead.objects.filter(tenant_id=tenant_id, phone=phone_number)
        .only('id')
        .first()
    )
    if not lead:
        from django.db.models import F

        # Match the last ten digits to handle +91XXXXXXXXXX, 0XXXXXXXXXX and
        # formatted values without ever crossing the tenant boundary.
        suffix = digits[-10:] if len(digits) >= 10 else digits
        lead = (
            Lead.objects.filter(tenant_id=tenant_id)
            .annotate(phone_digits=phone_digits_expression(F('phone')))
            .filter(phone_digits__endswith=suffix)
            .order_by('id')
            .only('id')
            .first()
        )
    return lead.id if lead else None


def phone_digits_expression(expression):
    """Return a database expression stripping common phone separators."""
    from django.db.models import TextField, Value
    from django.db.models.functions import Replace

    for character in ('+', ' ', '-', '(', ')', '.', '/', '\\'):
        expression = Replace(
            expression,
            Value(character),
            Value(''),
            output_field=TextField(),
        )
    return expression


def _create_call_activity(tenant_id, call_log, lead_id):
    """
    Create a LeadActivity (type=CALL) for a completed call.
    """
    from crm.models import LeadActivity, ActivityTypeEnum

    direction_label = 'Inbound' if call_log.direction == 'inbound' else 'Outbound'
    type_label = call_log.get_call_type_display()
    duration_str = _format_duration(call_log.duration)

    notes_text = ''
    if call_log.telecmi_notes:
        msgs = [n.get('msg', '') for n in call_log.telecmi_notes if n.get('msg')]
        if msgs:
            notes_text = '\n'.join(msgs)

    content = (
        f'{direction_label} {type_label} call\n'
        f'Duration: {duration_str}\n'
        f'From: {call_log.from_number}\n'
        f'To: {call_log.to_number}'
    )
    if notes_text:
        content += f'\nNotes: {notes_text}'

    import uuid as _uuid
    from django.db import transaction
    # Use the handling agent's user_id if available; fall back to a zero UUID
    # (represents a system/automation action — by_user_id is NOT NULL in the schema)
    actor = call_log.agent_user_id or _uuid.UUID(int=0)

    try:
        # Use a savepoint so a failure here does not break the caller's transaction
        with transaction.atomic():
            LeadActivity.objects.create(
                tenant_id=tenant_id,
                lead_id=lead_id,
                type=ActivityTypeEnum.CALL,
                content=content,
                happened_at=call_log.call_time,
                by_user_id=actor,
                meta={
                    'cmiuid': call_log.cmiuid,
                    'direction': call_log.direction,
                    'call_type': call_log.call_type,
                    'duration': call_log.duration,
                    'billed_sec': call_log.billed_sec,
                    'rate': str(call_log.rate),
                    'from_number': call_log.from_number,
                    'to_number': call_log.to_number,
                    'source': 'telecmi',
                },
            )
            logger.info('Created CALL activity for lead %s (cmiuid=%s)', lead_id, call_log.cmiuid)
    except Exception as exc:
        logger.error('Failed to create CALL activity for lead %s: %s', lead_id, exc)


def _format_duration(seconds: int) -> str:
    if seconds < 60:
        return f'{seconds}s'
    minutes, secs = divmod(seconds, 60)
    return f'{minutes}m {secs}s'


def sync_cdr_for_agent(tenant_id, user_id, hours_back: int = 24, queue_archives: bool = True) -> dict:
    """
    Pull CDR from TeleCMI for the given agent and upsert into CallLog.
    Returns summary dict: {'created': N, 'updated': N, 'errors': N}.

    Called manually via API or by a scheduled Celery task.
    """
    from telephony.services.token_service import get_agent_token, TokenServiceError
    from telephony.services.telecmi_client import get_incoming_cdr, get_outgoing_cdr, TeleCMIError
    import time

    to_ts = int(time.time() * 1000)
    from_ts = to_ts - (hours_back * 3600 * 1000)

    try:
        token = get_agent_token(tenant_id, user_id)
    except TokenServiceError as exc:
        logger.error('sync_cdr_for_agent: cannot get token for user %s: %s', user_id, exc)
        return {'created': 0, 'updated': 0, 'errors': 1, 'error': str(exc)}

    stats = {'created': 0, 'updated': 0, 'errors': 0, 'status': 'success', 'error_details': []}

    # Sync each combination of direction × call_type
    combinations = [
        ('inbound', 0, get_incoming_cdr),   # missed inbound
        ('inbound', 1, get_incoming_cdr),   # answered inbound
        ('outbound', 0, get_outgoing_cdr),  # missed outbound
        ('outbound', 1, get_outgoing_cdr),  # answered outbound
    ]

    for direction, call_type_int, fetch_fn in combinations:
        page = 1
        while True:
            try:
                result = fetch_fn(token, call_type_int, from_ts, to_ts, page=page, limit=10)
            except TeleCMIError as exc:
                # TeleCMI reports invalid/expired user tokens as 404. Refresh once.
                if exc.status_code == 404:
                    from telephony.services.token_service import invalidate_token
                    try:
                        invalidate_token(tenant_id, user_id)
                        token = get_agent_token(tenant_id, user_id)
                        result = fetch_fn(token, call_type_int, from_ts, to_ts, page=page, limit=10)
                    except (TokenServiceError, TeleCMIError) as retry_exc:
                        exc = retry_exc
                        result = None
                else:
                    result = None
                if result is not None:
                    records = result.get('cdr', [])
                else:
                    logger.error(
                        'CDR fetch error (%s, type=%s, page=%s): %s',
                        direction, call_type_int, page, exc
                    )
                    stats['errors'] += 1
                    stats['error_details'].append(f'{direction}/{call_type_int}: {exc}')
                    break
            else:
                records = result.get('cdr', [])
            for raw in records:
                existing_count = _count_existing(
                    tenant_id,
                    _first_value(raw, 'cmiuid', 'cmiuuid'),
                    _first_value(raw, 'call_id'),
                )
                log = process_cdr_record(
                    tenant_id,
                    raw,
                    direction,
                    synced_via='manual_sync',
                    agent_user_id=user_id,
                    queue_archive=queue_archives,
                )
                if not log:
                    stats['errors'] += 1
                    stats['error_details'].append(f'{direction}/{call_type_int}: invalid CDR payload')
                    continue
                if existing_count == 0:
                    stats['created'] += 1
                else:
                    stats['updated'] += 1

            if len(records) < 10:
                break  # no more pages
            page += 1

    if stats['errors']:
        stats['status'] = 'partial' if stats['created'] or stats['updated'] else 'failed'
    logger.info('CDR sync for user %s: %s', user_id, {**stats, 'error_details': len(stats['error_details'])})
    return stats


def _count_existing(tenant_id, cmiuid, telecmi_call_id=None) -> int:
    from telephony.models import CallLog
    from django.db.models import Q
    if not cmiuid and not telecmi_call_id:
        return 0
    identity = Q(cmiuid=str(cmiuid)) if cmiuid else Q()
    if telecmi_call_id:
        identity |= Q(telecmi_call_id=str(telecmi_call_id))
    return CallLog.objects.filter(tenant_id=tenant_id).filter(identity).count()
