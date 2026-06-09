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
from datetime import datetime, timezone as dt_timezone

from django.utils import timezone

logger = logging.getLogger(__name__)

# TeleCMI timestamps are in UTC milliseconds
MS_PER_SECOND = 1000


def process_cdr_record(tenant_id, raw_cdr: dict, direction: str, synced_via: str = 'webhook') -> 'CallLog':
    """
    Create or update a CallLog from a single TeleCMI CDR dict.
    Also creates a LeadActivity if a matching Lead is found.

    direction: 'inbound' or 'outbound'
    synced_via: 'webhook' or 'manual_sync'

    Returns the CallLog instance.
    """
    from telephony.models import CallLog, CallDirectionEnum, CallTypeEnum
    from crm.models import LeadActivity, ActivityTypeEnum

    cmiuid = raw_cdr.get('cmiuid')
    if not cmiuid:
        logger.warning('CDR record missing cmiuid, skipping: %s', raw_cdr)
        return None

    duration = raw_cdr.get('duration', 0)
    call_type = CallTypeEnum.MISSED if duration == 0 else CallTypeEnum.ANSWERED
    from_number = str(raw_cdr.get('from', ''))
    to_number = str(raw_cdr.get('to', from_number))
    call_time_ms = raw_cdr.get('time', 0)
    call_time = datetime.fromtimestamp(call_time_ms / MS_PER_SECOND, tz=dt_timezone.utc)

    # Find matching lead by phone number (tenant-scoped)
    lead_id = _find_lead_id(tenant_id, from_number if direction == 'inbound' else to_number)

    recording_file = raw_cdr.get('record') or raw_cdr.get('file') or ''

    log, created = CallLog.objects.get_or_create(
        tenant_id=tenant_id,
        cmiuid=cmiuid,
        defaults={
            'direction': direction,
            'call_type': call_type,
            'from_number': from_number,
            'to_number': to_number,
            'duration': duration,
            'billed_sec': raw_cdr.get('billedsec', 0),
            'rate': raw_cdr.get('rate', 0),
            'caller_name': raw_cdr.get('name') or '',
            'telecmi_notes': raw_cdr.get('notes'),
            'recording_file': recording_file,
            'call_time': call_time,
            'lead_id': lead_id,
            'synced_via': synced_via,
        },
    )

    if not created:
        # Update mutable fields on re-sync (e.g. notes or recording added post-call)
        updated_fields = []
        if raw_cdr.get('notes') and log.telecmi_notes != raw_cdr['notes']:
            log.telecmi_notes = raw_cdr['notes']
            updated_fields.append('telecmi_notes')
        if lead_id and log.lead_id != lead_id:
            log.lead_id = lead_id
            updated_fields.append('lead_id')
        if recording_file and not log.recording_file:
            log.recording_file = recording_file
            updated_fields.append('recording_file')
        if updated_fields:
            log.save(update_fields=updated_fields + ['updated_at'])

    # Create a CRM Activity if we have a lead and haven't done it yet
    if lead_id and not log.activity_created:
        _create_call_activity(tenant_id, log, lead_id)
        log.activity_created = True
        log.save(update_fields=['activity_created', 'updated_at'])

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

    # Try exact match first, then suffix match (handles country code variants)
    lead = (
        Lead.objects.filter(tenant_id=tenant_id, phone=phone_number)
        .only('id')
        .first()
    )
    if not lead:
        # Try matching last 10 digits to handle +91XXXXXXXXXX vs 0XXXXXXXXXX
        suffix = digits[-10:] if len(digits) >= 10 else digits
        lead = (
            Lead.objects.filter(tenant_id=tenant_id, phone__endswith=suffix)
            .only('id')
            .first()
        )
    return lead.id if lead else None


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


def sync_cdr_for_agent(tenant_id, user_id, hours_back: int = 24) -> dict:
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

    stats = {'created': 0, 'updated': 0, 'errors': 0}

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
                logger.error(
                    'CDR fetch error (%s, type=%s, page=%s): %s',
                    direction, call_type_int, page, exc
                )
                stats['errors'] += 1
                break

            records = result.get('cdr', [])
            for raw in records:
                existing_count = _count_existing(tenant_id, raw.get('cmiuid'))
                process_cdr_record(tenant_id, raw, direction, synced_via='manual_sync')
                if existing_count == 0:
                    stats['created'] += 1
                else:
                    stats['updated'] += 1

            if len(records) < 10:
                break  # no more pages
            page += 1

    logger.info('CDR sync for user %s: %s', user_id, stats)
    return stats


def _count_existing(tenant_id, cmiuid) -> int:
    from telephony.models import CallLog
    if not cmiuid:
        return 0
    return CallLog.objects.filter(tenant_id=tenant_id, cmiuid=cmiuid).count()
