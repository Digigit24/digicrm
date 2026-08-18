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
    DuplicateDetectionCache,
    ComposioAuthConfig, ComposioConnection, ComposioConnectionEvent,
    ComposioLinkState, ComposioToolkit,
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


# ===========================================================================
# COMPOSIO
# ===========================================================================
# Every Composio admin below is intentionally READ-MOSTLY. Composio holds the
# credentials, not us, so there is nothing here to reveal - but the Composio
# identifiers (auth_config_id, connected_account_id, composio_user_id) address
# live third-party accounts and must never be hand-edited into pointing at
# another tenant's entity.


class ComposioConnectionEventInline(admin.TabularInline):
    """Last 20 audit events for a connection. Read-only."""
    model = ComposioConnectionEvent
    extra = 0
    can_delete = False
    fields = ['created_at', 'event_type', 'actor_user_id', 'message', 'source_ip']
    readonly_fields = fields
    ordering = ['-created_at']

    def get_queryset(self, request):
        qs = super().get_queryset(request).order_by('-created_at')
        recent = qs.values_list('id', flat=True)[:20]
        return qs.filter(id__in=list(recent))

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ComposioAuthConfig)
class ComposioAuthConfigAdmin(admin.ModelAdmin):
    """Admin for ComposioAuthConfig - platform-wide and per-tenant auth configs."""
    list_display = [
        'id', 'toolkit_slug', 'scope_display', 'auth_config_id', 'auth_scheme',
        'is_composio_managed', 'is_active', 'last_synced_at'
    ]
    list_filter = ['toolkit_slug', 'auth_scheme', 'is_composio_managed', 'is_active']
    search_fields = ['toolkit_slug', 'auth_config_id', 'name', 'tenant_id']
    readonly_fields = ['public_id', 'created_at', 'updated_at', 'last_synced_at']

    fieldsets = (
        ('Auth Config', {
            'fields': ('toolkit_slug', 'name', 'auth_config_id', 'auth_scheme',
                       'is_composio_managed', 'is_active')
        }),
        ('Scope', {
            'fields': ('tenant_id',),
            'description': 'Leave blank for the platform-wide, Composio-managed config.'
        }),
        ('Tool policy', {
            'fields': ('restrict_to_tools', 'default_tool_versions'),
            'description': 'restrict_to_tools bounds what POST /execute/ can ever call. '
                           'Empty means nothing may be executed (fail closed).'
        }),
        ('Metadata', {
            'fields': ('public_id', 'metadata', 'last_synced_at', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def scope_display(self, obj):
        return str(obj.tenant_id) if obj.tenant_id else 'GLOBAL'

    scope_display.short_description = 'Scope'


@admin.register(ComposioConnection)
class ComposioConnectionAdmin(admin.ModelAdmin):
    """
    Admin for ComposioConnection.

    All Composio identifiers are read-only: editing composio_user_id or
    connected_account_id by hand would point this row at a different entity,
    which the service layer treats as tampering and refuses to act on.
    """
    list_display = [
        'id', 'toolkit_slug', 'alias', 'account_label', 'status', 'scope',
        'tenant_id', 'user_id', 'connected_at'
    ]
    list_filter = ['status', 'scope', 'toolkit_slug', 'created_at']
    search_fields = [
        'tenant_id', 'user_id', 'alias', 'account_label',
        'connected_account_id', 'composio_user_id', 'public_id'
    ]
    readonly_fields = [
        'public_id', 'composio_user_id', 'connected_account_id', 'toolkit_slug',
        'granted_scopes', 'expires_at', 'connected_at', 'last_status_check_at',
        'last_used_at', 'disconnected_at', 'last_error', 'last_error_at',
        'metadata', 'created_at', 'updated_at'
    ]
    inlines = [ComposioConnectionEventInline]

    fieldsets = (
        ('Ownership', {
            'fields': ('tenant_id', 'user_id', 'scope', 'created_by_user_id', 'composio_user_id'),
            'description': 'composio_user_id is the only isolation boundary Composio enforces. '
                           'It is derived from (namespace, tenant_id, user_id) and is read-only.'
        }),
        ('Composio identifiers', {
            'fields': ('auth_config', 'toolkit_slug', 'connected_account_id', 'status')
        }),
        ('Display', {
            'fields': ('alias', 'account_label', 'granted_scopes', 'expires_at')
        }),
        ('Health', {
            'fields': ('connected_at', 'last_status_check_at', 'last_used_at',
                       'disconnected_at', 'last_error', 'last_error_at')
        }),
        ('Metadata', {
            'fields': ('public_id', 'metadata', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(ComposioConnectionEvent)
class ComposioConnectionEventAdmin(admin.ModelAdmin):
    """Admin for the append-only Composio audit trail. Never editable."""
    list_display = ['id', 'created_at', 'event_type', 'connection', 'tenant_id',
                    'actor_user_id', 'source_ip']
    list_filter = ['event_type', 'created_at']
    search_fields = ['tenant_id', 'actor_user_id', 'message', 'connection__public_id']
    readonly_fields = ['tenant_id', 'connection', 'event_type', 'actor_user_id',
                       'message', 'payload', 'source_ip', 'created_at']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ComposioLinkState)
class ComposioLinkStateAdmin(admin.ModelAdmin):
    """Admin for the one-time hosted-auth nonces. Read-only; swept by Celery."""
    list_display = ['id', 'toolkit_slug', 'tenant_id', 'user_id', 'created_at',
                    'expires_at', 'consumed_at']
    list_filter = ['toolkit_slug', 'created_at']
    search_fields = ['tenant_id', 'user_id', 'toolkit_slug']
    readonly_fields = ['state', 'tenant_id', 'user_id', 'connection', 'toolkit_slug',
                       'return_to', 'link_expires_at', 'expires_at', 'consumed_at', 'created_at']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ComposioToolkit)
class ComposioToolkitAdmin(admin.ModelAdmin):
    """
    Admin for the cached Composio toolkit catalogue.

    is_enabled / is_featured / sort_order are OPERATOR-OWNED - the nightly sync
    task never clobbers them. Everything else is refreshed from Composio.
    """
    list_display = ['id', 'slug', 'name', 'is_enabled', 'is_featured', 'sort_order',
                    'tools_count', 'triggers_count', 'last_synced_at']
    list_filter = ['is_enabled', 'is_featured', 'no_auth']
    search_fields = ['slug', 'name', 'description']
    list_editable = ['is_enabled', 'is_featured', 'sort_order']
    readonly_fields = ['slug', 'name', 'description', 'logo_url', 'categories',
                       'auth_schemes', 'composio_managed_auth_schemes', 'no_auth',
                       'tools_count', 'triggers_count', 'metadata',
                       'last_synced_at', 'created_at', 'updated_at']

    fieldsets = (
        ('Catalogue entry (synced from Composio)', {
            'fields': ('slug', 'name', 'description', 'logo_url', 'categories',
                       'auth_schemes', 'composio_managed_auth_schemes', 'no_auth',
                       'tools_count', 'triggers_count')
        }),
        ('Operator controls', {
            'fields': ('is_enabled', 'is_featured', 'sort_order'),
            'description': 'Never overwritten by the nightly sync.'
        }),
        ('Metadata', {
            'fields': ('metadata', 'last_synced_at', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def has_add_permission(self, request):
        return False
