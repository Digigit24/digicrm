from datetime import time

from django.db import models


class CalendarViewEnum(models.TextChoices):
    MONTH = 'MONTH', 'Month'
    WEEK = 'WEEK', 'Week'
    DAY = 'DAY', 'Day'
    AGENDA = 'AGENDA', 'Agenda'


DEFAULT_VISIBLE_LAYERS = ['meetings', 'tasks', 'follow_ups']
DEFAULT_WORKING_DAYS = [1, 2, 3, 4, 5]


def default_visible_layers():
    return list(DEFAULT_VISIBLE_LAYERS)


def default_working_days():
    return list(DEFAULT_WORKING_DAYS)


class CalendarPreference(models.Model):
    """Per-user calendar settings.

    There is no user table in this service, so preferences are keyed by the
    SuperAdmin user UUID from the JWT. ``timezone`` is the local source of truth
    for the caller's IANA zone and is seeded from the browser.
    """

    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    user_id = models.UUIDField(db_index=True)
    timezone = models.CharField(max_length=64, default='UTC')
    default_view = models.CharField(
        max_length=8, choices=CalendarViewEnum.choices, default=CalendarViewEnum.MONTH
    )
    week_starts_on = models.SmallIntegerField(default=1, help_text='0=Sunday, 1=Monday')
    working_hours_start = models.TimeField(default=time(9, 0))
    working_hours_end = models.TimeField(default=time(18, 0))
    working_days = models.JSONField(
        default=default_working_days, blank=True, help_text='[1,2,3,4,5] = Mon-Fri'
    )
    visible_layers = models.JSONField(
        default=default_visible_layers, blank=True,
        help_text="['meetings','tasks','follow_ups']"
    )
    visible_user_ids = models.JSONField(
        default=list, blank=True, help_text='Team members whose lanes are toggled on.'
    )
    default_meeting_duration_minutes = models.PositiveIntegerField(default=30)
    show_declined = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'calendar_preferences'
        constraints = [
            models.UniqueConstraint(fields=['tenant_id', 'user_id'], name='uniq_calendar_pref'),
        ]

    def __str__(self):
        return f'CalendarPreference<{self.user_id}>'
