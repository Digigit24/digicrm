"""Recurrence edit semantics (plan B.3) and reminder materialisation helpers.

Every mutating call on a recurring meeting carries ``edit_scope`` (``this`` |
``this_and_following`` | ``all``) plus ``occurrence_start`` (required unless the
scope is ``all``).
"""
import uuid
from datetime import timedelta

from django.db import transaction
from django.utils import timezone as dj_timezone
from rest_framework.exceptions import ValidationError

from . import recurrence
from .models import Meeting, MeetingAttendee, MeetingReminder, MeetingStatusEnum

EDIT_SCOPE_THIS = 'this'
EDIT_SCOPE_THIS_AND_FOLLOWING = 'this_and_following'
EDIT_SCOPE_ALL = 'all'
EDIT_SCOPES = (EDIT_SCOPE_THIS, EDIT_SCOPE_THIS_AND_FOLLOWING, EDIT_SCOPE_ALL)

# Fields copied verbatim onto an override / split row.
_COPIED_FIELDS = (
    'tenant_id', 'lead_id', 'title', 'location', 'description', 'notes',
    'owner_user_id', 'meeting_type', 'color', 'all_day', 'timezone', 'status',
    'transparency', 'visibility', 'conference_url', 'external_provider',
    'external_calendar_id',
)


def parse_edit_scope(value):
    scope = (value or EDIT_SCOPE_THIS).strip().lower()
    if scope not in EDIT_SCOPES:
        raise ValidationError({'edit_scope': f'Must be one of {", ".join(EDIT_SCOPES)}.'})
    return scope


def resolve_occurrence_start(meeting, edit_scope, raw_occurrence_start):
    """Validate ``occurrence_start`` for a scoped edit.

    Returns ``None`` for ``edit_scope='all'`` or a non-recurring meeting.
    Raises 400 when the instant is missing or is not a real occurrence.
    """
    if edit_scope == EDIT_SCOPE_ALL:
        return None
    if not meeting.recurrence_rule:
        # A single meeting has exactly one "occurrence"; scope is a no-op.
        return None

    occurrence_start = recurrence.parse_instant(raw_occurrence_start)
    if occurrence_start is None:
        raise ValidationError({
            'occurrence_start':
                "Required when edit_scope is not 'all'. Send the UTC start of the "
                'occurrence the user clicked.'
        })
    if not recurrence.is_valid_occurrence(meeting, occurrence_start):
        raise ValidationError({
            'occurrence_start': 'Does not fall on a real occurrence of this series.'
        })
    return occurrence_start


def series_master(meeting):
    """The master row of ``meeting``'s series (itself when it is not an override)."""
    return meeting.recurring_parent or meeting


def _copy_children(source, target):
    attendees = [
        MeetingAttendee(
            tenant_id=target.tenant_id,
            meeting=target,
            user_id=attendee.user_id,
            lead_id=attendee.lead_id,
            email=attendee.email,
            display_name=attendee.display_name,
            role=attendee.role,
            response_status=attendee.response_status,
            comment=attendee.comment,
            is_organizer=attendee.is_organizer,
            notify=attendee.notify,
        )
        for attendee in source.attendees.all()
    ]
    if attendees:
        MeetingAttendee.objects.bulk_create(attendees)

    rules = [
        MeetingReminder(
            tenant_id=target.tenant_id,
            meeting=target,
            minutes_before=rule.minutes_before,
            method=rule.method,
            for_attendees=rule.for_attendees,
        )
        for rule in source.reminder_rules.all()
    ]
    if rules:
        MeetingReminder.objects.bulk_create(rules)


@transaction.atomic
def get_or_create_override(master, occurrence_start):
    """Create-or-fetch the override row that replaces one occurrence.

    An override is a full ``Meeting`` row (Google models it the same way): it
    needs every field a meeting has, so a separate model would duplicate the
    entire schema.
    """
    occurrence_start = recurrence.to_utc(occurrence_start)
    existing = Meeting.objects.filter(
        recurring_parent=master, recurrence_original_start=occurrence_start
    ).first()
    if existing is not None:
        return existing, False

    duration = recurrence.to_utc(master.end_at) - recurrence.to_utc(master.start_at)
    defaults = {field: getattr(master, field) for field in _COPIED_FIELDS}
    override = Meeting.objects.create(
        uid=uuid.uuid4(),
        start_at=occurrence_start,
        end_at=occurrence_start + duration,
        recurrence_rule=None,
        recurrence_end_at=None,
        recurrence_exdates=[],
        recurring_parent=master,
        recurrence_original_start=occurrence_start,
        external_event_id=None,
        external_sync=None,
        **defaults,
    )
    _copy_children(master, override)
    return override, True


@transaction.atomic
def exclude_occurrence(master, occurrence_start):
    """RFC 5545 EXDATE: drop one occurrence from the series.

    Cheaper than a tombstone row, and removes any override for that instant.
    """
    occurrence_start = recurrence.to_utc(occurrence_start)
    stamp = occurrence_start.isoformat()
    exdates = list(master.recurrence_exdates or [])
    known = recurrence.parse_exdates(exdates)
    if occurrence_start not in known:
        exdates.append(stamp)
        master.recurrence_exdates = exdates
        master.save(update_fields=['recurrence_exdates', 'updated_at'])
    Meeting.objects.filter(
        recurring_parent=master, recurrence_original_start=occurrence_start
    ).delete()
    return master


@transaction.atomic
def split_series(master, occurrence_start):
    """``this_and_following``: clip the master and start a new series.

    Returns ``(master, new_master)``. The caller applies its patch to
    ``new_master``.
    """
    occurrence_start = recurrence.to_utc(occurrence_start)
    duration = recurrence.to_utc(master.end_at) - recurrence.to_utc(master.start_at)

    tail_rule = recurrence.remaining_rule(
        master.recurrence_rule, master.start_at, master.timezone, occurrence_start
    )

    # 1. clip the original master to just before the split point
    clipped_rule = recurrence.set_rule_until(
        master.recurrence_rule, occurrence_start - timedelta(seconds=1)
    )

    # 4. exdates are partitioned between the two masters
    head_exdates, tail_exdates = [], []
    for raw in (master.recurrence_exdates or []):
        parsed = recurrence.parse_instant(raw)
        if parsed is None:
            continue
        (tail_exdates if parsed >= occurrence_start else head_exdates).append(parsed.isoformat())

    defaults = {field: getattr(master, field) for field in _COPIED_FIELDS}
    new_master = Meeting.objects.create(
        uid=uuid.uuid4(),
        start_at=occurrence_start,
        end_at=occurrence_start + duration,
        recurrence_rule=tail_rule,
        recurrence_exdates=tail_exdates,
        recurrence_end_at=recurrence.compute_recurrence_end(
            tail_rule, occurrence_start, master.timezone
        ),
        external_event_id=None,
        external_sync=None,
        **defaults,
    )
    _copy_children(master, new_master)

    master.recurrence_rule = clipped_rule
    master.recurrence_exdates = head_exdates
    master.recurrence_end_at = recurrence.compute_recurrence_end(
        clipped_rule, master.start_at, master.timezone
    )
    master.save(update_fields=[
        'recurrence_rule', 'recurrence_exdates', 'recurrence_end_at', 'updated_at',
    ])

    # 3. re-parent overrides at or after the split point
    Meeting.objects.filter(
        recurring_parent=master, recurrence_original_start__gte=occurrence_start
    ).update(recurring_parent=new_master)

    return master, new_master


@transaction.atomic
def resolve_edit_target(meeting, edit_scope, raw_occurrence_start):
    """Return ``(target, created_rows)`` -- the row a mutation should be applied to.

    ``all``                -> the master itself
    ``this``               -> an override row for the clicked occurrence
    ``this_and_following`` -> a fresh master carrying the remainder of the series
    """
    master = series_master(meeting)

    if not master.recurrence_rule or edit_scope == EDIT_SCOPE_ALL:
        return meeting if not master.recurrence_rule else master, []

    occurrence_start = resolve_occurrence_start(master, edit_scope, raw_occurrence_start)

    if edit_scope == EDIT_SCOPE_THIS:
        override, created = get_or_create_override(master, occurrence_start)
        return override, ([override] if created else [])

    master, new_master = split_series(master, occurrence_start)
    return new_master, [new_master]


@transaction.atomic
def soft_delete(meeting, edit_scope, raw_occurrence_start):
    """Soft delete honouring recurrence scope. Returns a short result dict."""
    master = series_master(meeting)
    now = dj_timezone.now()

    if not master.recurrence_rule or edit_scope == EDIT_SCOPE_ALL:
        target = master if master.recurrence_rule else meeting
        Meeting.objects.filter(pk=target.pk).update(
            is_deleted=True, deleted_at=now, updated_at=now
        )
        Meeting.objects.filter(recurring_parent=target).update(
            is_deleted=True, deleted_at=now, updated_at=now
        )
        return {'deleted': target.pk, 'edit_scope': edit_scope or EDIT_SCOPE_ALL}

    occurrence_start = resolve_occurrence_start(master, edit_scope, raw_occurrence_start)

    if edit_scope == EDIT_SCOPE_THIS:
        exclude_occurrence(master, occurrence_start)
        return {
            'deleted_occurrence': occurrence_start.isoformat(),
            'series_id': master.pk,
            'edit_scope': EDIT_SCOPE_THIS,
        }

    # this_and_following: clip the master; nothing new is created.
    clipped = recurrence.set_rule_until(
        master.recurrence_rule, occurrence_start - timedelta(seconds=1)
    )
    master.recurrence_rule = clipped
    master.recurrence_end_at = recurrence.compute_recurrence_end(
        clipped, master.start_at, master.timezone
    )
    master.save(update_fields=['recurrence_rule', 'recurrence_end_at', 'updated_at'])
    Meeting.objects.filter(
        recurring_parent=master, recurrence_original_start__gte=occurrence_start
    ).update(is_deleted=True, deleted_at=now, updated_at=now)
    return {
        'series_id': master.pk,
        'truncated_at': occurrence_start.isoformat(),
        'edit_scope': EDIT_SCOPE_THIS_AND_FOLLOWING,
    }


def cancel_meeting(target, user_id, reason=''):
    now = dj_timezone.now()
    target.status = MeetingStatusEnum.CANCELLED
    target.cancelled_at = now
    target.cancelled_by_user_id = user_id
    target.cancellation_reason = reason or ''
    target.save(update_fields=[
        'status', 'cancelled_at', 'cancelled_by_user_id', 'cancellation_reason', 'updated_at',
    ])
    return target


def complete_meeting(target, notes=''):
    now = dj_timezone.now()
    target.status = MeetingStatusEnum.COMPLETED
    target.completed_at = now
    if notes:
        target.notes = f'{target.notes}\n{notes}' if target.notes else notes
    target.save(update_fields=['status', 'completed_at', 'notes', 'updated_at'])
    return target
