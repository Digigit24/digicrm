from django.contrib import admin
from common.admin_site import tenant_admin_site, TenantModelAdmin
from .models import Task


class TaskAdmin(TenantModelAdmin):
    """Admin interface for Task"""
    list_display = [
        'title', 'lead', 'status', 'priority', 'assignee_user_id',
        'due_date', 'completed_at', 'created_at'
    ]
    list_filter = ['status', 'priority', 'created_at', 'due_date', 'completed_at']
    search_fields = ['title', 'description', 'lead__name']
    date_hierarchy = 'created_at'
    readonly_fields = ['created_at', 'updated_at', 'completed_at']
    
    fieldsets = (
        ('Task Details', {
            'fields': ('lead', 'title', 'description')
        }),
        ('Status & Priority', {
            'fields': ('status', 'priority')
        }),
        ('Assignment', {
            'fields': ('assignee_user_id', 'reporter_user_id')
        }),
        ('Dates', {
            'fields': ('due_date', 'completed_at')
        }),
        ('Additional Info', {
            'fields': ('checklist', 'attachments_count')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


# Register with tenant admin site
tenant_admin_site.register(Task, TaskAdmin)