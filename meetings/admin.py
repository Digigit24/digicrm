from django.contrib import admin

from common.admin_site import tenant_admin_site, TenantModelAdmin
from .models import Meeting, MeetingAttendee, MeetingReminder


class MeetingAttendeeInline(admin.TabularInline):
    model = MeetingAttendee
    extra = 0
    fields = ['user_id', 'lead', 'email', 'display_name', 'role', 'response_status', 'notify']


class MeetingReminderInline(admin.TabularInline):
    model = MeetingReminder
    extra = 0
    fields = ['minutes_before', 'method', 'for_attendees']


class MeetingAdmin(TenantModelAdmin):
    """Admin interface for Meeting"""

    list_display = [
        'title', 'lead', 'meeting_type', 'status', 'start_at', 'end_at',
        'all_day', 'timezone', 'is_deleted',
    ]
    list_filter = ['meeting_type', 'status', 'all_day', 'is_deleted', 'start_at', 'created_at']
    search_fields = ['title', 'location', 'description', 'lead__name']
    date_hierarchy = 'start_at'
    readonly_fields = ['uid', 'created_at', 'updated_at', 'recurrence_end_at',
                       'last_synced_at', 'sync_error']
    inlines = [MeetingAttendeeInline, MeetingReminderInline]

    fieldsets = (
        ('Meeting Details', {
            'fields': ('lead', 'title', 'meeting_type', 'location', 'conference_url', 'color')
        }),
        ('Schedule', {
            'fields': ('start_at', 'end_at', 'all_day', 'timezone')
        }),
        ('Lifecycle', {
            'fields': ('status', 'cancelled_at', 'cancelled_by_user_id',
                       'cancellation_reason', 'completed_at', 'is_deleted', 'deleted_at')
        }),
        ('Availability & privacy', {
            'fields': ('transparency', 'visibility')
        }),
        ('Recurrence', {
            'classes': ('collapse',),
            'fields': ('recurrence_rule', 'recurrence_end_at', 'recurrence_exdates',
                       'recurring_parent', 'recurrence_original_start')
        }),
        ('External sync', {
            'classes': ('collapse',),
            'fields': ('uid', 'external_provider', 'external_event_id',
                       'external_calendar_id', 'external_sync', 'sync_status',
                       'last_synced_at', 'sync_error')
        }),
        ('Content', {
            'fields': ('description', 'notes')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


# Register with tenant admin site
tenant_admin_site.register(Meeting, MeetingAdmin)
