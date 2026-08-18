"""Backfill an ORGANIZER MeetingAttendee row for every pre-existing meeting.

Without this, meetings created before the calendar work have no attendee rows,
so the ``team`` ownership scope (``owner_user_id``, ``attendees__user_id``) and
the attendee-based RSVP path would silently skip them.
"""
from django.db import migrations

BATCH = 1000


def create_organizer_attendees(apps, schema_editor):
    Meeting = apps.get_model('meetings', 'Meeting')
    MeetingAttendee = apps.get_model('meetings', 'MeetingAttendee')

    existing = set(
        MeetingAttendee.objects.filter(is_organizer=True).values_list('meeting_id', flat=True)
    )

    pending = []
    qs = Meeting.objects.exclude(owner_user_id=None).only(
        'id', 'tenant_id', 'owner_user_id'
    ).iterator(chunk_size=BATCH)
    for meeting in qs:
        if meeting.id in existing:
            continue
        pending.append(MeetingAttendee(
            tenant_id=meeting.tenant_id,
            meeting_id=meeting.id,
            user_id=meeting.owner_user_id,
            role='ORGANIZER',
            response_status='ACCEPTED',
            is_organizer=True,
            notify=True,
        ))
        if len(pending) >= BATCH:
            MeetingAttendee.objects.bulk_create(pending, batch_size=BATCH)
            pending = []

    if pending:
        MeetingAttendee.objects.bulk_create(pending, batch_size=BATCH)


def drop_organizer_attendees(apps, schema_editor):
    MeetingAttendee = apps.get_model('meetings', 'MeetingAttendee')
    MeetingAttendee.objects.filter(is_organizer=True, role='ORGANIZER').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('meetings', '0002_calendar_foundation'),
    ]

    operations = [
        migrations.RunPython(create_organizer_attendees, drop_organizer_attendees),
    ]
