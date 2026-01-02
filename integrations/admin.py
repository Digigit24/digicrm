"""
Django Admin Configuration for Integration System

Provides admin interface for managing integrations, connections,
workflows, and viewing execution logs.
"""

from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe

from integrations.models import (
    Integration, Connection, Workflow, WorkflowTrigger,
    WorkflowAction, WorkflowMapping, ExecutionLog,
    DuplicateDetectionCache
)


@admin.register(Integration)
class IntegrationAdmin(admin.ModelAdmin):
    """Admin for Integration model"""
    list_display = ['id', 'name', 'type', 'is_active', 'requires_oauth', 'created_at']
    list_filter = ['type', 'is_active', 'requires_oauth']
    search_fields = ['name', 'description']
    readonly_fields = ['created_at', 'updated_at']

    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'type', 'description', 'icon_url', 'is_active', 'requires_oauth')
        }),
        ('Configuration', {
            'fields': ('oauth_config', 'api_config'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(Connection)
class ConnectionAdmin(admin.ModelAdmin):
    """Admin for Connection model"""
    list_display = [
        'id', 'name', 'integration_link', 'tenant_id', 'status',
        'connected_at', 'last_used_at', 'created_at'
    ]
    list_filter = ['status', 'integration__type', 'created_at']
    search_fields = ['name', 'tenant_id', 'user_id']
    readonly_fields = [
        'created_at', 'updated_at', 'connected_at', 'last_used_at',
        'last_error_at', 'token_expires_at'
    ]

    fieldsets = (
        ('Basic Information', {
            'fields': ('tenant_id', 'user_id', 'integration', 'name', 'status')
        }),
        ('Connection Data', {
            'fields': ('connection_data', 'token_expires_at'),
        }),
        ('Error Information', {
            'fields': ('last_error', 'last_error_at'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('connected_at', 'last_used_at', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def integration_link(self, obj):
        """Link to integration"""
        url = reverse('admin:integrations_integration_change', args=[obj.integration.id])
        return format_html('<a href="{}">{}</a>', url, obj.integration.name)

    integration_link.short_description = 'Integration'

    # Hide encrypted fields from admin for security
    exclude = ['access_token_encrypted', 'refresh_token_encrypted']


class WorkflowMappingInline(admin.TabularInline):
    """Inline for WorkflowMapping"""
    model = WorkflowMapping
    extra = 0
    fields = ['source_field', 'destination_field', 'default_value', 'is_required']


class WorkflowActionInline(admin.StackedInline):
    """Inline for WorkflowAction"""
    model = WorkflowAction
    extra = 0
    fields = ['action_type', 'order', 'action_config', 'retry_on_failure', 'max_retries']
    show_change_link = True


@admin.register(Workflow)
class WorkflowAdmin(admin.ModelAdmin):
    """Admin for Workflow model"""
    list_display = [
        'id', 'name', 'connection_link', 'tenant_id', 'is_active_badge',
        'total_executions', 'success_rate_display', 'last_executed_at', 'created_at'
    ]
    list_filter = ['is_active', 'is_deleted', 'last_execution_status', 'created_at']
    search_fields = ['name', 'description', 'tenant_id', 'user_id']
    readonly_fields = [
        'created_at', 'updated_at', 'deleted_at', 'last_executed_at',
        'last_execution_status', 'total_executions', 'successful_executions',
        'failed_executions', 'success_rate_display'
    ]

    inlines = [WorkflowActionInline]

    fieldsets = (
        ('Basic Information', {
            'fields': ('tenant_id', 'user_id', 'name', 'description', 'connection')
        }),
        ('Status', {
            'fields': ('is_active', 'is_deleted', 'deleted_at')
        }),
        ('Execution Statistics', {
            'fields': (
                'total_executions', 'successful_executions', 'failed_executions',
                'success_rate_display', 'last_executed_at', 'last_execution_status'
            ),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def connection_link(self, obj):
        """Link to connection"""
        url = reverse('admin:integrations_connection_change', args=[obj.connection.id])
        return format_html('<a href="{}">{}</a>', url, obj.connection.name)

    connection_link.short_description = 'Connection'

    def is_active_badge(self, obj):
        """Show active status as badge"""
        if obj.is_active:
            return format_html('<span style="color: green;"> Active</span>')
        return format_html('<span style="color: red;"> Inactive</span>')

    is_active_badge.short_description = 'Status'

    def success_rate_display(self, obj):
        """Display success rate"""
        if obj.total_executions == 0:
            return "N/A"

        rate = (obj.successful_executions / obj.total_executions) * 100
        color = 'green' if rate >= 80 else 'orange' if rate >= 50 else 'red'

        return format_html(
            '<span style="color: {};">{:.1f}%</span>',
            color, rate
        )

    success_rate_display.short_description = 'Success Rate'


@admin.register(WorkflowTrigger)
class WorkflowTriggerAdmin(admin.ModelAdmin):
    """Admin for WorkflowTrigger model"""
    list_display = [
        'id', 'workflow_link', 'trigger_type', 'poll_interval_minutes',
        'last_checked_at', 'created_at'
    ]
    list_filter = ['trigger_type', 'created_at']
    search_fields = ['workflow__name']
    readonly_fields = ['created_at', 'updated_at', 'last_checked_at']

    fieldsets = (
        ('Workflow', {
            'fields': ('workflow',)
        }),
        ('Trigger Configuration', {
            'fields': ('trigger_type', 'trigger_config', 'poll_interval_minutes')
        }),
        ('State', {
            'fields': ('last_checked_at', 'last_processed_record'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def workflow_link(self, obj):
        """Link to workflow"""
        url = reverse('admin:integrations_workflow_change', args=[obj.workflow.id])
        return format_html('<a href="{}">{}</a>', url, obj.workflow.name)

    workflow_link.short_description = 'Workflow'


@admin.register(WorkflowAction)
class WorkflowActionAdmin(admin.ModelAdmin):
    """Admin for WorkflowAction model"""
    list_display = [
        'id', 'workflow_link', 'action_type', 'order',
        'retry_on_failure', 'max_retries', 'created_at'
    ]
    list_filter = ['action_type', 'retry_on_failure', 'created_at']
    search_fields = ['workflow__name']
    readonly_fields = ['created_at', 'updated_at']

    inlines = [WorkflowMappingInline]

    fieldsets = (
        ('Workflow', {
            'fields': ('workflow', 'order')
        }),
        ('Action Configuration', {
            'fields': ('action_type', 'action_config', 'conditions')
        }),
        ('Retry Settings', {
            'fields': ('retry_on_failure', 'max_retries')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def workflow_link(self, obj):
        """Link to workflow"""
        url = reverse('admin:integrations_workflow_change', args=[obj.workflow.id])
        return format_html('<a href="{}">{}</a>', url, obj.workflow.name)

    workflow_link.short_description = 'Workflow'


@admin.register(WorkflowMapping)
class WorkflowMappingAdmin(admin.ModelAdmin):
    """Admin for WorkflowMapping model"""
    list_display = [
        'id', 'workflow_action_link', 'source_field', 'destination_field',
        'is_required', 'created_at'
    ]
    list_filter = ['is_required', 'created_at']
    search_fields = ['source_field', 'destination_field', 'workflow_action__workflow__name']
    readonly_fields = ['created_at', 'updated_at']

    fieldsets = (
        ('Mapping', {
            'fields': (
                'workflow_action', 'source_field', 'destination_field',
                'default_value', 'is_required'
            )
        }),
        ('Transformation', {
            'fields': ('transformation', 'validation_rules'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def workflow_action_link(self, obj):
        """Link to workflow action"""
        url = reverse('admin:integrations_workflowaction_change', args=[obj.workflow_action.id])
        return format_html('<a href="{}">{}</a>', url, f"Action {obj.workflow_action.id}")

    workflow_action_link.short_description = 'Workflow Action'


@admin.register(ExecutionLog)
class ExecutionLogAdmin(admin.ModelAdmin):
    """Admin for ExecutionLog model"""
    list_display = [
        'id', 'workflow_link', 'execution_id', 'status_badge',
        'started_at', 'duration_display', 'retry_count'
    ]
    list_filter = ['status', 'is_retry', 'started_at']
    search_fields = ['execution_id', 'workflow__name', 'tenant_id']
    readonly_fields = [
        'created_at', 'updated_at', 'started_at', 'completed_at',
        'duration_ms', 'execution_id'
    ]
    date_hierarchy = 'started_at'

    fieldsets = (
        ('Execution Info', {
            'fields': (
                'workflow', 'execution_id', 'status', 'tenant_id',
                'is_retry', 'parent_execution_id', 'retry_count'
            )
        }),
        ('Timing', {
            'fields': ('started_at', 'completed_at', 'duration_ms')
        }),
        ('Data', {
            'fields': ('trigger_data', 'result_data'),
            'classes': ('collapse',)
        }),
        ('Error Information', {
            'fields': ('error_message', 'error_traceback'),
            'classes': ('collapse',)
        }),
        ('Execution Steps', {
            'fields': ('execution_steps',),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def workflow_link(self, obj):
        """Link to workflow"""
        url = reverse('admin:integrations_workflow_change', args=[obj.workflow.id])
        return format_html('<a href="{}">{}</a>', url, obj.workflow.name)

    workflow_link.short_description = 'Workflow'

    def status_badge(self, obj):
        """Show status as colored badge"""
        colors = {
            'PENDING': 'gray',
            'RUNNING': 'blue',
            'SUCCESS': 'green',
            'FAILED': 'red',
            'RETRYING': 'orange'
        }
        color = colors.get(obj.status, 'gray')

        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color, obj.status
        )

    status_badge.short_description = 'Status'

    def duration_display(self, obj):
        """Display duration in human-readable format"""
        if not obj.duration_ms:
            return "N/A"

        seconds = obj.duration_ms / 1000

        if seconds < 1:
            return f"{obj.duration_ms}ms"
        elif seconds < 60:
            return f"{seconds:.2f}s"
        else:
            minutes = seconds / 60
            return f"{minutes:.2f}m"

    duration_display.short_description = 'Duration'


@admin.register(DuplicateDetectionCache)
class DuplicateDetectionCacheAdmin(admin.ModelAdmin):
    """Admin for DuplicateDetectionCache model"""
    list_display = [
        'id', 'workflow_link', 'source_identifier', 'created_object_type',
        'created_object_id', 'created_at'
    ]
    list_filter = ['created_object_type', 'created_at']
    search_fields = ['source_identifier', 'workflow__name', 'tenant_id']
    readonly_fields = ['created_at', 'updated_at']

    fieldsets = (
        ('Cache Information', {
            'fields': (
                'tenant_id', 'workflow', 'source_identifier',
                'created_object_type', 'created_object_id', 'source_data_hash'
            )
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def workflow_link(self, obj):
        """Link to workflow"""
        url = reverse('admin:integrations_workflow_change', args=[obj.workflow.id])
        return format_html('<a href="{}">{}</a>', url, obj.workflow.name)

    workflow_link.short_description = 'Workflow'
