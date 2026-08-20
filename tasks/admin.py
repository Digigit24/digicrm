from django.contrib import admin

from common.admin_site import tenant_admin_site, TenantModelAdmin
from .models import Task, TaskChecklistItem


class TaskChecklistItemInline(admin.TabularInline):
    model = TaskChecklistItem
    extra = 0
    fields = ['order_index', 'text', 'is_done', 'done_at']
    readonly_fields = ['done_at']
    ordering = ['order_index', 'id']


class TaskAdmin(TenantModelAdmin):
    """Admin interface for Task"""
    list_display = [
        'title', 'related_type', 'related_id', 'lead', 'status', 'priority',
        'assignee_user_id', 'due_date', 'completed_at', 'created_at'
    ]
    list_filter = [
        'status', 'priority', 'related_type', 'created_at', 'due_date',
        'completed_at',
    ]
    search_fields = ['title', 'description', 'lead__name']
    date_hierarchy = 'created_at'
    readonly_fields = ['created_at', 'updated_at', 'completed_at', 'recurrence_end_at']
    inlines = [TaskChecklistItemInline]

    fieldsets = (
        ('Task Details', {
            'fields': ('title', 'description')
        }),
        ('Linked object', {
            'fields': ('related_type', 'related_id', 'lead'),
            'description': 'lead is kept in sync with related_id whenever '
                           'related_type is LEAD. Leave everything blank for a '
                           'standalone task.',
        }),
        ('Status & Priority', {
            'fields': ('status', 'priority', 'labels', 'order_index')
        }),
        ('Assignment', {
            'fields': ('assignee_user_id', 'reporter_user_id')
        }),
        ('Dates', {
            'fields': ('start_date', 'due_date', 'is_all_day', 'timezone',
                       'completed_at')
        }),
        ('Recurrence', {
            'fields': ('rrule', 'recurrence_end_at', 'recurring_parent'),
            'classes': ('collapse',)
        }),
        ('Reminders', {
            'fields': ('reminder_minutes_before', 'snoozed_until'),
        }),
        ('Additional Info', {
            'fields': ('checklist', 'attachments_count'),
            'description': 'checklist is the deprecated JSON blob, kept for '
                           'rollback only; edit the checklist rows above instead.',
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


class TaskChecklistItemAdmin(TenantModelAdmin):
    list_display = ['text', 'task', 'is_done', 'order_index', 'done_at']
    list_filter = ['is_done', 'created_at']
    search_fields = ['text', 'task__title']
    readonly_fields = ['done_at', 'created_at', 'updated_at']


# Register with tenant admin site
tenant_admin_site.register(Task, TaskAdmin)
tenant_admin_site.register(TaskChecklistItem, TaskChecklistItemAdmin)
