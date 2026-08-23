"""
Celery tasks for WhatsApp follow-up sequences.

Audit item P0-4: ``LeadSequenceEnrollment`` has always documented "the Celery
beat task reads active enrollments where next_step_at <= now and fires the
appropriate template message", but no such task existed and no WhatsApp entry
existed in ``CELERY_BEAT_SCHEDULE``. Every enrollment ever created just sat
there with a ``next_step_at`` in the past. This module is that task.

Safety model, in order of importance:

1. **Kill switch.** ``WHATSAPP_SEQUENCES_ENABLED`` defaults to ``False``. This
   code sends real WhatsApp messages to real customers, so shipping it must not
   start a backlog of months-old enrollments firing at once. The operator turns
   it on deliberately, after reviewing the backlog.

2. **At-most-once per (enrollment, step, run).** Claiming is two-layered:
   ``select_for_update(skip_locked=True)`` stops two workers colliding inside
   the same instant, and a persisted ``locked_at`` stops the second worker's
   *next* poll re-picking the row. On top of both sits
   :class:`SequenceStepDelivery`, whose unique constraint makes a double send
   impossible even if the locking were wrong.

3. **Crash-safety biases towards silence.** The delivery marker is committed
   before the HTTP call. A marker found stuck in ``SENDING`` means some worker
   died with the outcome unknown -- we flip it to ``UNKNOWN`` and advance
   WITHOUT re-sending. A skipped follow-up is recoverable; a duplicate message
   to a customer is not.

4. **24-hour window.** Sequence steps are template sends, which is exactly what
   Meta requires outside the session window. The canonical
   ``reply_window {open, expires_at, requires_template}`` is resolved per send
   and recorded on the delivery. A step with no template configured is only
   sendable inside an open window -- and since a step carries no free-text body,
   that case fails loudly with a clear error rather than being dropped.
"""
import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import (
    LeadSequenceEnrollment,
    SequenceEnrollmentStatusEnum,
    SequenceStepDelivery,
    SequenceStepDeliveryStatusEnum,
    WhatsAppSequenceStep,
)
from .services.laravel_adapter import LaravelAdapterError, LaravelWhatsAppAdapter
from .services.normalizer import normalize_reply_window

logger = logging.getLogger(__name__)

# Adapter failures that are genuinely indeterminate: the request may well have
# reached Meta and been accepted before we lost the connection. Retrying one of
# these is exactly the double-send we are trying to prevent.
INDETERMINATE_STATUS_CODES = frozenset({504, 503})


class SequenceStepError(Exception):
    """A step could not be sent. ``permanent`` suppresses any retry."""

    def __init__(self, message, permanent=False):
        super().__init__(message)
        self.permanent = permanent


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

def _enabled():
    return bool(getattr(settings, 'WHATSAPP_SEQUENCES_ENABLED', False))


def _max_attempts():
    return int(getattr(settings, 'WHATSAPP_SEQUENCE_MAX_ATTEMPTS', 3))


def _retry_delay():
    return timedelta(minutes=int(getattr(settings, 'WHATSAPP_SEQUENCE_RETRY_MINUTES', 30)))


def _stale_claim_cutoff(now):
    minutes = int(getattr(settings, 'WHATSAPP_SEQUENCE_CLAIM_STALE_MINUTES', 15))
    return now - timedelta(minutes=minutes)


def _batch_size(override=None):
    if override:
        return int(override)
    return int(getattr(settings, 'WHATSAPP_SEQUENCE_BATCH_SIZE', 100))


# ---------------------------------------------------------------------------
# Template variables
# ---------------------------------------------------------------------------

def resolve_template_components(step, lead):
    """
    Turn ``template_variable_mapping`` -- ``{"1": "name", "2": "company"}`` --
    into the Meta body component Laravel's ``normalizeTemplateComponents()``
    expects.

    Keys are variable *positions* and are ordered numerically, not by dict
    insertion order: ``{"10": ..., "2": ...}`` must not send {{10}}'s value as
    {{2}}. A mapped field that resolves to blank is an error rather than an
    empty parameter, because Meta rejects empty positional parameters and we
    would rather surface that than watch every send 400.
    """
    mapping = step.template_variable_mapping or {}
    if not isinstance(mapping, dict) or not mapping:
        return []

    try:
        positions = sorted(mapping.keys(), key=lambda key: int(key))
    except (TypeError, ValueError):
        raise SequenceStepError(
            'Step %s has a non-numeric key in template_variable_mapping: %r'
            % (step.step_number, list(mapping.keys())),
            permanent=True,
        )

    parameters = []
    for position in positions:
        field = mapping[position]
        value = getattr(lead, field, None) if isinstance(field, str) else None
        if value is None and isinstance(lead.metadata, dict):
            value = lead.metadata.get(field)
        text = '' if value is None else str(value).strip()
        if not text:
            raise SequenceStepError(
                'Step %s maps template variable {{%s}} to lead field "%s", '
                'which is empty on lead %s. Meta rejects empty template '
                'parameters, so the step was not sent.'
                % (step.step_number, position, field, lead.id),
                permanent=True,
            )
        parameters.append({'type': 'text', 'text': text})

    return [{'type': 'body', 'parameters': parameters}]


# ---------------------------------------------------------------------------
# 24-hour session window
# ---------------------------------------------------------------------------

def resolve_reply_window(adapter, phone):
    """
    Canonical ``{open, expires_at, requires_template, expires_human}`` for a
    phone number.

    Laravel exposes the window on the chat-history route
    (``AdapterController:519-532``). A failure here is deliberately NOT fatal:
    a sequence step is a template send, which is legal whether the window is
    open or shut, so we degrade to "unknown" rather than block the send.
    """
    try:
        payload = adapter.get_chat_history(phone, page=1, per_page=1)
    except LaravelAdapterError as exc:
        logger.warning(
            '[WA Sequences] Reply-window lookup failed for %s: %s. '
            'Proceeding as unknown — template sends are legal either way.',
            phone, exc,
        )
        return normalize_reply_window({})
    if isinstance(payload, dict) and isinstance(payload.get('data'), dict):
        payload = {**payload, **payload['data']}
    return normalize_reply_window(payload)


# ---------------------------------------------------------------------------
# Step selection
# ---------------------------------------------------------------------------

def _next_step_for(enrollment):
    """The step that should be sent now, or ``None`` if the sequence is done."""
    steps = WhatsAppSequenceStep.objects.filter(sequence_id=enrollment.sequence_id)
    if enrollment.current_step_id:
        current = enrollment.current_step
        if current is not None:
            steps = steps.filter(step_number__gt=current.step_number)
    return steps.order_by('step_number').first()


def _following_step(enrollment, step):
    return (
        WhatsAppSequenceStep.objects
        .filter(sequence_id=enrollment.sequence_id, step_number__gt=step.step_number)
        .order_by('step_number')
        .first()
    )


# ---------------------------------------------------------------------------
# Terminal / advance transitions
# ---------------------------------------------------------------------------

def _complete(enrollment, now):
    enrollment.status = SequenceEnrollmentStatusEnum.COMPLETED
    enrollment.completed_at = now
    enrollment.next_step_at = None
    enrollment.locked_at = None
    enrollment.attempt_count = 0
    enrollment.save(update_fields=[
        'status', 'completed_at', 'next_step_at', 'locked_at',
        'attempt_count', 'updated_at',
    ])


def _advance(enrollment, step, now):
    """Record ``step`` as sent and schedule (or finish) whatever follows it."""
    following = _following_step(enrollment, step)
    enrollment.current_step = step
    enrollment.attempt_count = 0
    enrollment.last_error = ''
    enrollment.locked_at = None
    if following is None:
        enrollment.status = SequenceEnrollmentStatusEnum.COMPLETED
        enrollment.completed_at = now
        enrollment.next_step_at = None
    else:
        enrollment.next_step_at = now + timedelta(days=max(0, following.delay_days or 0))
    enrollment.save(update_fields=[
        'current_step', 'status', 'completed_at', 'next_step_at',
        'locked_at', 'attempt_count', 'last_error', 'updated_at',
    ])


def _halt(enrollment, reason, error):
    """
    Stop stepping this enrollment without deleting it.

    PAUSED is reused rather than adding a FAILED status: it is already in the
    enum, already rendered by both frontends, and already means "not stepping,
    resumable by a human". ``last_error`` carries the why.
    """
    enrollment.status = SequenceEnrollmentStatusEnum.PAUSED
    enrollment.stopped_reason = reason
    enrollment.last_error = str(error)[:2000]
    enrollment.locked_at = None
    enrollment.save(update_fields=[
        'status', 'stopped_reason', 'last_error', 'locked_at', 'updated_at',
    ])
    logger.error(
        '[WA Sequences] Enrollment %s halted (%s): %s',
        enrollment.id, reason, enrollment.last_error,
    )


def _schedule_retry(enrollment, error, now):
    enrollment.last_error = str(error)[:2000]
    enrollment.locked_at = None
    enrollment.next_step_at = now + _retry_delay()
    enrollment.save(update_fields=[
        'last_error', 'locked_at', 'next_step_at', 'updated_at',
    ])


# ---------------------------------------------------------------------------
# Claim
# ---------------------------------------------------------------------------

def _claim_due_enrollments(now, batch_size):
    """
    Take ownership of up to ``batch_size`` due enrollments.

    ``skip_locked`` is what makes this safe to run on N workers: a worker that
    finds a row already locked by a peer steps over it instead of blocking and
    then duplicating the send once the lock clears. Persisting ``locked_at``
    inside the same transaction extends that protection past the lock's
    lifetime, so the peer's *next* poll skips the row too.
    """
    with transaction.atomic():
        # A worker that died holding a claim must not strand the enrollment.
        LeadSequenceEnrollment.objects.filter(
            status=SequenceEnrollmentStatusEnum.ACTIVE,
            locked_at__lt=_stale_claim_cutoff(now),
        ).update(locked_at=None)

        claimed_ids = list(
            LeadSequenceEnrollment.objects
            .select_for_update(skip_locked=True)
            .filter(
                status=SequenceEnrollmentStatusEnum.ACTIVE,
                locked_at__isnull=True,
                next_step_at__isnull=False,
                next_step_at__lte=now,
            )
            .order_by('next_step_at')
            .values_list('id', flat=True)[:batch_size]
        )
        if claimed_ids:
            LeadSequenceEnrollment.objects.filter(id__in=claimed_ids).update(
                locked_at=now,
            )
    return claimed_ids


# ---------------------------------------------------------------------------
# Per-enrollment processing
# ---------------------------------------------------------------------------

def _open_delivery(enrollment_id, now):
    """
    Phase 1 — pick the step and commit the send marker.

    Returns ``(enrollment, step, delivery, outcome)``. When ``outcome`` is not
    ``None`` the caller must NOT send: the work was already resolved here.
    Everything in this function runs inside one transaction and is durable
    before any HTTP call happens.
    """
    with transaction.atomic():
        # of=('self',) locks the enrollment row and nothing else. Without it
        # Postgres rejects the statement outright -- current_step is a nullable
        # FK, so select_related emits a LEFT JOIN and "FOR UPDATE cannot be
        # applied to the nullable side of an outer join". It is also the
        # behaviour we want: stepping an enrollment must not lock the lead.
        enrollment = (
            LeadSequenceEnrollment.objects
            .select_for_update(of=('self',))
            .select_related('lead', 'sequence', 'current_step')
            .filter(pk=enrollment_id)
            .first()
        )
        if enrollment is None:
            return None, None, None, 'vanished'

        # The inbound webhook flips ACTIVE -> REPLIED. It may well have landed
        # between our claim and now, so status is re-read under the row lock
        # and never trusted from the claim query. A REPLIED, OPTED_OUT, PAUSED
        # or COMPLETED enrollment is never stepped.
        if enrollment.status != SequenceEnrollmentStatusEnum.ACTIVE:
            if enrollment.locked_at is not None:
                enrollment.locked_at = None
                enrollment.save(update_fields=['locked_at', 'updated_at'])
            return enrollment, None, None, 'not_active'

        if not enrollment.sequence.is_active:
            _halt(enrollment, 'sequence_inactive',
                  'The parent sequence was deactivated.')
            return enrollment, None, None, 'halted'

        step = _next_step_for(enrollment)
        if step is None:
            _complete(enrollment, now)
            return enrollment, None, None, 'completed'

        run_number = enrollment.run_number or 1
        try:
            with transaction.atomic():
                delivery, created = SequenceStepDelivery.objects.get_or_create(
                    enrollment=enrollment,
                    step=step,
                    run_number=run_number,
                    defaults={
                        'tenant_id': enrollment.tenant_id,
                        'status': SequenceStepDeliveryStatusEnum.SENDING,
                        'attempt': 1,
                        'template_uid': step.template_uid or '',
                    },
                )
        except IntegrityError:
            # Another worker inserted the marker between our SELECT and INSERT.
            # It owns the send; we must not race it.
            logger.warning(
                '[WA Sequences] Delivery marker for enrollment %s step %s '
                'was created concurrently — standing down.',
                enrollment.id, step.id,
            )
            enrollment.locked_at = None
            enrollment.save(update_fields=['locked_at', 'updated_at'])
            return enrollment, step, None, 'raced'

        if not created:
            if delivery.status == SequenceStepDeliveryStatusEnum.SENT:
                # Already delivered on a previous run of this task. Advance the
                # bookkeeping the earlier worker never got to finish.
                _advance(enrollment, step, now)
                return enrollment, step, delivery, 'already_sent'

            if delivery.status == SequenceStepDeliveryStatusEnum.SENDING:
                # A worker committed the marker and then died. Whether Meta
                # accepted the message is unknowable from here, so we refuse to
                # send again and move on.
                delivery.status = SequenceStepDeliveryStatusEnum.UNKNOWN
                delivery.last_error = (
                    'A worker claimed this step and never reported an outcome. '
                    'Not re-sent: a duplicate WhatsApp message cannot be undone.'
                )
                delivery.save(update_fields=['status', 'last_error', 'updated_at'])
                logger.error(
                    '[WA Sequences] Enrollment %s step %s left in SENDING by a '
                    'dead worker — marked UNKNOWN and skipped, not re-sent.',
                    enrollment.id, step.id,
                )
                _advance(enrollment, step, now)
                return enrollment, step, delivery, 'indeterminate'

            if delivery.status == SequenceStepDeliveryStatusEnum.UNKNOWN:
                _advance(enrollment, step, now)
                return enrollment, step, delivery, 'indeterminate'

            # FAILED — a definite non-delivery, safe to try again.
            delivery.status = SequenceStepDeliveryStatusEnum.SENDING
            delivery.attempt = (delivery.attempt or 0) + 1
            delivery.last_error = ''
            delivery.save(update_fields=[
                'status', 'attempt', 'last_error', 'updated_at',
            ])

        enrollment.attempt_count = (enrollment.attempt_count or 0) + 1
        enrollment.save(update_fields=['attempt_count', 'updated_at'])
        return enrollment, step, delivery, None


def _send_step(enrollment, step, delivery):
    """Phase 2 — the actual outbound call. No DB locks are held here."""
    lead = enrollment.lead
    if not (lead.phone or '').strip():
        raise SequenceStepError(
            'Lead %s has no phone number.' % lead.id, permanent=True,
        )

    adapter = LaravelWhatsAppAdapter(tenant_id=str(enrollment.tenant_id))

    window = resolve_reply_window(adapter, lead.phone)
    delivery.reply_window_open = window['open']
    delivery.reply_window_expires_at = window['expires_at'] or ''
    delivery.save(update_fields=[
        'reply_window_open', 'reply_window_expires_at', 'updated_at',
    ])

    template_uid = (step.template_uid or '').strip()
    if not template_uid:
        # Outside the session window only a template may be sent, and a step
        # carries no free-text body to fall back on. Fail loudly.
        window_shut = window['requires_template'] is True or window['open'] is False
        if window_shut:
            raise SequenceStepError(
                'Step %s of sequence "%s" has no template_uid, and lead %s is '
                'outside the 24-hour WhatsApp session window%s, so only a '
                'template could be delivered. Configure a template on this step.'
                % (
                    step.step_number, enrollment.sequence.name, lead.id,
                    (' (expired %s)' % window['expires_at']) if window['expires_at'] else '',
                ),
                permanent=True,
            )
        raise SequenceStepError(
            'Step %s of sequence "%s" has no template_uid configured.'
            % (step.step_number, enrollment.sequence.name),
            permanent=True,
        )

    components = resolve_template_components(step, lead)

    # Always a template send. That is precisely what makes a sequence step
    # legal when the 24-hour window has closed.
    result = adapter.send_message(
        phone=lead.phone,
        name=lead.name or '',
        template_uid=template_uid,
        template_components=components,
        digicrm_lead_id=lead.id,
    )
    return result or {}


def _process_enrollment(enrollment_id, now):
    enrollment, step, delivery, outcome = _open_delivery(enrollment_id, now)
    if outcome is not None:
        return outcome

    try:
        result = _send_step(enrollment, step, delivery)
    except SequenceStepError as exc:
        delivery.status = SequenceStepDeliveryStatusEnum.FAILED
        delivery.last_error = str(exc)[:2000]
        delivery.save(update_fields=['status', 'last_error', 'updated_at'])
        if exc.permanent:
            _halt(enrollment, 'step_misconfigured', exc)
            return 'halted'
        _schedule_retry(enrollment, exc, now)
        return 'failed'
    except LaravelAdapterError as exc:
        if exc.status_code in INDETERMINATE_STATUS_CODES:
            # Timed out or lost the connection: Meta may already have the
            # message. Never retry this step.
            delivery.status = SequenceStepDeliveryStatusEnum.UNKNOWN
            delivery.last_error = str(exc)[:2000]
            delivery.save(update_fields=['status', 'last_error', 'updated_at'])
            logger.error(
                '[WA Sequences] Indeterminate send for enrollment %s step %s '
                '(%s). Advancing without retry to avoid a duplicate message.',
                enrollment.id, step.id, exc,
            )
            _advance(enrollment, step, now)
            enrollment.last_error = str(exc)[:2000]
            enrollment.save(update_fields=['last_error', 'updated_at'])
            return 'indeterminate'
        return _record_failure(enrollment, step, delivery, exc, now)
    except Exception as exc:  # noqa: BLE001 — one bad row must not kill the batch
        logger.exception(
            '[WA Sequences] Unexpected error sending enrollment %s step %s',
            enrollment.id, step.id,
        )
        return _record_failure(enrollment, step, delivery, exc, now)

    delivery.status = SequenceStepDeliveryStatusEnum.SENT
    delivery.wa_message_id = str(result.get('wa_message_id') or '')
    delivery.last_error = ''
    delivery.save(update_fields=[
        'status', 'wa_message_id', 'last_error', 'updated_at',
    ])
    _advance(enrollment, step, now)

    lead = enrollment.lead
    lead.last_contacted_at = now
    lead.save(update_fields=['last_contacted_at'])
    return 'sent'


def _record_failure(enrollment, step, delivery, exc, now):
    delivery.status = SequenceStepDeliveryStatusEnum.FAILED
    delivery.last_error = str(exc)[:2000]
    delivery.save(update_fields=['status', 'last_error', 'updated_at'])

    if (enrollment.attempt_count or 0) >= _max_attempts():
        _halt(enrollment, 'max_send_attempts_exceeded', exc)
        return 'halted'

    _schedule_retry(enrollment, exc, now)
    return 'failed'


# ---------------------------------------------------------------------------
# The beat task
# ---------------------------------------------------------------------------

@shared_task(name='whatsapp_integration.tasks.step_due_sequence_enrollments')
def step_due_sequence_enrollments(batch_size=None):
    """
    Fire the next due step of every active WhatsApp sequence enrollment.

    Returns a counter dict so the beat log is readable at a glance.
    """
    if not _enabled():
        # Deliberately quiet: this runs on every beat tick until an operator
        # flips the switch, and a warning per minute would be noise.
        logger.debug(
            '[WA Sequences] WHATSAPP_SEQUENCES_ENABLED is False — no enrollment '
            'was claimed or stepped.'
        )
        return {'enabled': False, 'claimed': 0, 'sent': 0}

    now = timezone.now()
    claimed_ids = _claim_due_enrollments(now, _batch_size(batch_size))

    counters = {'enabled': True, 'claimed': len(claimed_ids), 'sent': 0}
    for enrollment_id in claimed_ids:
        try:
            outcome = _process_enrollment(enrollment_id, now)
        except Exception:  # noqa: BLE001 — never let one row poison the batch
            logger.exception(
                '[WA Sequences] Enrollment %s could not be processed', enrollment_id,
            )
            LeadSequenceEnrollment.objects.filter(pk=enrollment_id).update(
                locked_at=None,
            )
            outcome = 'error'
        counters[outcome] = counters.get(outcome, 0) + 1

    if counters['claimed']:
        logger.info('[WA Sequences] %s', counters)
    return counters
