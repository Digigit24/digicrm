"""
Integration System Models for Django CRM

This module contains all models for the integration system that works like Zapier.
Supports multiple integration types (Google Sheets, Webhooks, etc.) with OAuth,
workflow automation, field mapping, and execution logging.
"""

from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
import json
import secrets
import uuid


class IntegrationTypeEnum(models.TextChoices):
    """Available integration types"""
    GOOGLE_SHEETS = 'GOOGLE_SHEETS', 'Google Sheets'
    WEBHOOK = 'WEBHOOK', 'Webhook'
    ZAPIER = 'ZAPIER', 'Zapier'
    MAKE = 'MAKE', 'Make.com'
    API = 'API', 'Generic API'
    EMAIL = 'EMAIL', 'Email'
    TELECMI = 'TELECMI', 'TeleCMI Telephony'


class ConnectionStatusEnum(models.TextChoices):
    """OAuth connection status"""
    CONNECTED = 'CONNECTED', 'Connected'
    DISCONNECTED = 'DISCONNECTED', 'Disconnected'
    EXPIRED = 'EXPIRED', 'Expired'
    ERROR = 'ERROR', 'Error'


class TriggerTypeEnum(models.TextChoices):
    """Types of workflow triggers"""
    NEW_ROW = 'NEW_ROW', 'New Row Added'
    UPDATED_ROW = 'UPDATED_ROW', 'Row Updated'
    WEBHOOK_RECEIVED = 'WEBHOOK_RECEIVED', 'Webhook Received'
    SCHEDULE = 'SCHEDULE', 'Scheduled'
    MANUAL = 'MANUAL', 'Manual Trigger'


class ActionTypeEnum(models.TextChoices):
    """Types of workflow actions"""
    CREATE_LEAD = 'CREATE_LEAD', 'Create Lead'
    UPDATE_LEAD = 'UPDATE_LEAD', 'Update Lead'
    CREATE_TASK = 'CREATE_TASK', 'Create Task'
    SEND_EMAIL = 'SEND_EMAIL', 'Send Email'
    WEBHOOK = 'WEBHOOK', 'Send Webhook'


class ExecutionStatusEnum(models.TextChoices):
    """Workflow execution status"""
    PENDING = 'PENDING', 'Pending'
    RUNNING = 'RUNNING', 'Running'
    SUCCESS = 'SUCCESS', 'Success'
    FAILED = 'FAILED', 'Failed'
    RETRYING = 'RETRYING', 'Retrying'


class Integration(models.Model):
    """
    Represents an available integration type (e.g., Google Sheets, Webhook).
    This is more of a template/configuration for what integrations are available.
    """
    id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=100, unique=True, help_text='Integration name (e.g., Google Sheets)')
    type = models.CharField(
        max_length=20,
        choices=IntegrationTypeEnum.choices,
        help_text='Type of integration'
    )
    description = models.TextField(null=True, blank=True, help_text='Description of what this integration does')
    icon_url = models.URLField(null=True, blank=True, help_text='URL to integration icon/logo')
    is_active = models.BooleanField(default=True, help_text='Whether this integration is available')
    requires_oauth = models.BooleanField(default=False, help_text='Whether OAuth is required')

    # OAuth configuration (stored as JSON for flexibility)
    oauth_config = models.JSONField(
        null=True,
        blank=True,
        help_text='OAuth configuration (client_id, scopes, etc.)'
    )

    # API configuration
    api_config = models.JSONField(
        null=True,
        blank=True,
        help_text='API configuration (endpoints, headers, etc.)'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'integrations'
        ordering = ['name']
        indexes = [
            models.Index(fields=['type'], name='idx_integrations_type'),
            models.Index(fields=['is_active'], name='idx_integrations_active'),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_type_display()})"


class Connection(models.Model):
    """
    Stores user's connection to an integration (OAuth tokens, API keys, etc.).
    Each user can have multiple connections to the same integration type.
    """
    id = models.BigAutoField(primary_key=True)
    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        db_index=True,
        help_text=(
            'Stable, non-sequential identifier safe to expose in public inbound '
            'webhook URLs (unlike the auto-increment id). Used by push-based '
            'integrations — Make.com, Zapier, generic webhooks — where the '
            'external service POSTs to us instead of us polling it. The actual '
            'auth is the inbound key (see access_token_encrypted / '
            'generate_inbound_key); public_id alone does not grant access.'
        )
    )
    tenant_id = models.UUIDField(db_index=True, help_text='Tenant ID for multi-tenancy')
    user_id = models.UUIDField(db_index=True, help_text='User who owns this connection')

    integration = models.ForeignKey(
        Integration,
        on_delete=models.CASCADE,
        related_name='connections',
        db_column='integration_id'
    )

    name = models.CharField(
        max_length=200,
        help_text='User-friendly name for this connection (e.g., "Marketing Leads Sheet")'
    )

    status = models.CharField(
        max_length=20,
        choices=ConnectionStatusEnum.choices,
        default=ConnectionStatusEnum.DISCONNECTED,
        help_text='Connection status'
    )

    # Encrypted credentials (access tokens, refresh tokens, API keys)
    # These will be encrypted before storing
    access_token_encrypted = models.TextField(
        null=True,
        blank=True,
        help_text='Encrypted OAuth access token'
    )
    refresh_token_encrypted = models.TextField(
        null=True,
        blank=True,
        help_text='Encrypted OAuth refresh token'
    )

    # Token metadata
    token_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When the access token expires'
    )

    # Additional connection data (API keys, account info, etc.)
    connection_data = models.JSONField(
        null=True,
        blank=True,
        help_text='Additional connection metadata (account email, sheet IDs, etc.)'
    )

    # Error tracking
    last_error = models.TextField(null=True, blank=True, help_text='Last error message if any')
    last_error_at = models.DateTimeField(null=True, blank=True, help_text='When the last error occurred')

    # Timestamps
    connected_at = models.DateTimeField(null=True, blank=True, help_text='When the connection was established')
    last_used_at = models.DateTimeField(null=True, blank=True, help_text='When this connection was last used')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'connections'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_connections_tenant'),
            models.Index(fields=['user_id'], name='idx_connections_user'),
            models.Index(fields=['status'], name='idx_connections_status'),
            models.Index(fields=['tenant_id', 'user_id'], name='idx_connections_tenant_user'),
        ]

    def __str__(self):
        return f"{self.name} - {self.integration.name}"

    def is_token_expired(self):
        """Check if the access token is expired"""
        if not self.token_expires_at:
            return False
        return timezone.now() >= self.token_expires_at

    def mark_as_expired(self):
        """Mark connection as expired"""
        self.status = ConnectionStatusEnum.EXPIRED
        self.save(update_fields=['status', 'updated_at'])

    def mark_as_error(self, error_message: str):
        """Mark connection as having an error"""
        self.status = ConnectionStatusEnum.ERROR
        self.last_error = error_message
        self.last_error_at = timezone.now()
        self.save(update_fields=['status', 'last_error', 'last_error_at', 'updated_at'])

    # ── Inbound (push-based) webhook auth ──────────────────────────────
    # For provider-initiated integrations (Make.com, Zapier, generic webhooks)
    # the external service pushes data to us, so there's no OAuth handshake —
    # instead we hand the tenant a per-connection secret key that must be sent
    # back on every inbound POST. We reuse access_token_encrypted as the
    # storage slot (same encrypted-secret shape as an OAuth token) rather than
    # adding a parallel field.

    def generate_inbound_key(self) -> str:
        """
        Generate a new inbound webhook key, store it encrypted, and mark the
        connection CONNECTED. Returns the PLAINTEXT key — this is the only
        time it is ever available in plaintext; only the encrypted form is
        persisted. Callers (the rotate_inbound_key API action) must return
        this value to the caller and never log it.
        """
        from integrations.utils.encryption import encrypt_token
        raw_key = secrets.token_urlsafe(32)
        self.access_token_encrypted = encrypt_token(raw_key)
        self.status = ConnectionStatusEnum.CONNECTED
        self.connected_at = timezone.now()
        self.save(update_fields=['access_token_encrypted', 'status', 'connected_at', 'updated_at'])
        return raw_key

    def verify_inbound_key(self, provided_key: str) -> bool:
        """Constant-time comparison of a provided key against the stored one."""
        from integrations.utils.encryption import decrypt_token, EncryptionError
        import hmac
        if not self.access_token_encrypted or not provided_key:
            return False
        try:
            stored = decrypt_token(self.access_token_encrypted)
        except EncryptionError:
            return False
        return hmac.compare_digest(stored, provided_key)

    @property
    def inbound_webhook_path(self) -> str:
        """Relative URL path Make/Zapier/etc. should POST leads to."""
        return f'/api/integrations/webhook/inbound/{self.public_id}/'


class Workflow(models.Model):
    """
    User-created automation workflow.
    Connects a trigger (e.g., new row in sheet) to actions (e.g., create lead).
    """
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True, help_text='Tenant ID for multi-tenancy')
    user_id = models.UUIDField(db_index=True, help_text='User who created this workflow')

    name = models.CharField(max_length=200, help_text='Workflow name')
    description = models.TextField(null=True, blank=True, help_text='Workflow description')

    connection = models.ForeignKey(
        Connection,
        on_delete=models.CASCADE,
        related_name='workflows',
        db_column='connection_id',
        help_text='The connection this workflow uses'
    )

    is_active = models.BooleanField(default=True, help_text='Whether this workflow is active')

    # Soft delete
    is_deleted = models.BooleanField(default=False, help_text='Soft delete flag')
    deleted_at = models.DateTimeField(null=True, blank=True, help_text='When workflow was deleted')

    # Execution tracking
    last_executed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When workflow was last executed'
    )
    last_execution_status = models.CharField(
        max_length=20,
        choices=ExecutionStatusEnum.choices,
        null=True,
        blank=True,
        help_text='Status of last execution'
    )

    # Statistics
    total_executions = models.IntegerField(default=0, help_text='Total number of executions')
    successful_executions = models.IntegerField(default=0, help_text='Number of successful executions')
    failed_executions = models.IntegerField(default=0, help_text='Number of failed executions')

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'workflows'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_workflows_tenant'),
            models.Index(fields=['user_id'], name='idx_workflows_user'),
            models.Index(fields=['is_active'], name='idx_workflows_active'),
            models.Index(fields=['is_deleted'], name='idx_workflows_deleted'),
            models.Index(fields=['tenant_id', 'is_active', 'is_deleted'], name='idx_workflows_active_lookup'),
        ]

    def __str__(self):
        return f"{self.name} ({'Active' if self.is_active else 'Inactive'})"

    def soft_delete(self):
        """Soft delete the workflow"""
        self.is_deleted = True
        self.is_active = False
        self.deleted_at = timezone.now()
        self.save(update_fields=['is_deleted', 'is_active', 'deleted_at', 'updated_at'])


class WorkflowTrigger(models.Model):
    """
    Defines what initiates a workflow (e.g., new row in Google Sheet).
    Each workflow has one trigger.
    """
    id = models.BigAutoField(primary_key=True)
    workflow = models.OneToOneField(
        Workflow,
        on_delete=models.CASCADE,
        related_name='trigger',
        db_column='workflow_id'
    )

    trigger_type = models.CharField(
        max_length=20,
        choices=TriggerTypeEnum.choices,
        help_text='Type of trigger'
    )

    # Trigger configuration (specific to trigger type)
    trigger_config = models.JSONField(
        help_text='Trigger configuration (sheet_id, sheet_name, column_mappings, etc.)'
    )

    # For polling triggers (e.g., Google Sheets)
    poll_interval_minutes = models.IntegerField(
        default=10,
        help_text='How often to poll for changes (in minutes)'
    )

    # State tracking for incremental updates
    last_checked_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When trigger was last checked'
    )
    last_processed_record = models.JSONField(
        null=True,
        blank=True,
        help_text='Last processed record metadata (row number, timestamp, etc.)'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'workflow_triggers'
        indexes = [
            models.Index(fields=['trigger_type'], name='idx_workflow_triggers_type'),
            models.Index(fields=['last_checked_at'], name='idx_workflow_triggers_checked'),
        ]

    def __str__(self):
        return f"{self.workflow.name} - {self.get_trigger_type_display()}"

    def should_poll(self):
        """Check if it's time to poll this trigger"""
        if not self.last_checked_at:
            return True

        next_poll_time = self.last_checked_at + timezone.timedelta(minutes=self.poll_interval_minutes)
        return timezone.now() >= next_poll_time


class WorkflowAction(models.Model):
    """
    Defines what action to perform when workflow is triggered.
    A workflow can have multiple actions executed in sequence.
    """
    id = models.BigAutoField(primary_key=True)
    workflow = models.ForeignKey(
        Workflow,
        on_delete=models.CASCADE,
        related_name='actions',
        db_column='workflow_id'
    )

    action_type = models.CharField(
        max_length=20,
        choices=ActionTypeEnum.choices,
        help_text='Type of action to perform'
    )

    # Execution order (for multiple actions)
    order = models.IntegerField(default=1, help_text='Execution order (lower executes first)')

    # Action configuration
    action_config = models.JSONField(
        help_text='Action-specific configuration (target model, default values, etc.)'
    )

    # Conditional logic (optional)
    conditions = models.JSONField(
        null=True,
        blank=True,
        help_text='Conditions that must be met for action to execute'
    )

    # Error handling
    retry_on_failure = models.BooleanField(
        default=True,
        help_text='Whether to retry this action on failure'
    )
    max_retries = models.IntegerField(default=3, help_text='Maximum number of retries')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'workflow_actions'
        ordering = ['order']
        indexes = [
            models.Index(fields=['workflow', 'order'], name='idx_workflow_actions_order'),
            models.Index(fields=['action_type'], name='idx_workflow_actions_type'),
        ]

    def __str__(self):
        return f"{self.workflow.name} - {self.get_action_type_display()} (Order: {self.order})"


class WorkflowMapping(models.Model):
    """
    Field mappings between source data and destination fields.
    Maps columns from Google Sheets to CRM Lead fields, for example.
    """
    id = models.BigAutoField(primary_key=True)
    workflow_action = models.ForeignKey(
        WorkflowAction,
        on_delete=models.CASCADE,
        related_name='field_mappings',
        db_column='workflow_action_id'
    )

    # Source field (from trigger data)
    source_field = models.CharField(
        max_length=200,
        help_text='Source field name (e.g., column name from sheet)'
    )

    # Destination field (in CRM)
    destination_field = models.CharField(
        max_length=200,
        help_text='Destination field name (e.g., lead.name, lead.email)'
    )

    # Transformation rules
    transformation = models.JSONField(
        null=True,
        blank=True,
        help_text='Transformation rules (trim, lowercase, format, etc.)'
    )

    # Default value if source is empty
    default_value = models.TextField(
        null=True,
        blank=True,
        help_text='Default value if source field is empty'
    )

    # Validation rules
    is_required = models.BooleanField(default=False, help_text='Whether this field is required')
    validation_rules = models.JSONField(
        null=True,
        blank=True,
        help_text='Validation rules (regex, min/max length, etc.)'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'workflow_mappings'
        ordering = ['id']
        indexes = [
            models.Index(fields=['workflow_action'], name='idx_workflow_mappings_action'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['workflow_action', 'destination_field'],
                name='unique_mapping_per_destination'
            )
        ]

    def __str__(self):
        return f"{self.source_field} -> {self.destination_field}"


class ExecutionLog(models.Model):
    """
    Tracks all workflow executions with detailed logs.
    Critical for debugging and monitoring automation health.
    """
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True, help_text='Tenant ID for multi-tenancy')

    workflow = models.ForeignKey(
        Workflow,
        on_delete=models.CASCADE,
        related_name='execution_logs',
        db_column='workflow_id'
    )

    # Execution metadata
    execution_id = models.UUIDField(unique=True, db_index=True, help_text='Unique execution identifier')
    status = models.CharField(
        max_length=20,
        choices=ExecutionStatusEnum.choices,
        default=ExecutionStatusEnum.PENDING,
        help_text='Execution status'
    )

    # Timing
    started_at = models.DateTimeField(auto_now_add=True, help_text='When execution started')
    completed_at = models.DateTimeField(null=True, blank=True, help_text='When execution completed')
    duration_ms = models.IntegerField(null=True, blank=True, help_text='Execution duration in milliseconds')

    # Input/Output data
    trigger_data = models.JSONField(
        null=True,
        blank=True,
        help_text='Data that triggered the workflow (e.g., new row data)'
    )
    result_data = models.JSONField(
        null=True,
        blank=True,
        help_text='Result of the execution (created lead ID, etc.)'
    )

    # Error tracking
    error_message = models.TextField(null=True, blank=True, help_text='Error message if failed')
    error_traceback = models.TextField(null=True, blank=True, help_text='Full error traceback')

    # Retry tracking
    retry_count = models.IntegerField(default=0, help_text='Number of retry attempts')
    is_retry = models.BooleanField(default=False, help_text='Whether this is a retry execution')
    parent_execution_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text='Parent execution ID if this is a retry'
    )

    # Step-by-step logs
    execution_steps = models.JSONField(
        null=True,
        blank=True,
        help_text='Detailed step-by-step execution log'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'execution_logs'
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_exec_logs_tenant'),
            models.Index(fields=['workflow'], name='idx_exec_logs_workflow'),
            models.Index(fields=['status'], name='idx_exec_logs_status'),
            models.Index(fields=['started_at'], name='idx_exec_logs_started'),
            models.Index(fields=['-started_at'], name='idx_exec_logs_started_desc'),
            models.Index(fields=['tenant_id', 'workflow', '-started_at'], name='idx_exec_logs_lookup'),
        ]

    def __str__(self):
        return f"{self.workflow.name} - {self.execution_id} ({self.status})"

    def mark_as_running(self):
        """Mark execution as running"""
        self.status = ExecutionStatusEnum.RUNNING
        self.save(update_fields=['status', 'updated_at'])

    def mark_as_success(self, result_data=None, execution_steps=None):
        """Mark execution as successful"""
        self.status = ExecutionStatusEnum.SUCCESS
        self.completed_at = timezone.now()

        if self.started_at:
            duration = (self.completed_at - self.started_at).total_seconds() * 1000
            self.duration_ms = int(duration)

        if result_data:
            self.result_data = result_data
        if execution_steps:
            self.execution_steps = execution_steps

        self.save(update_fields=[
            'status', 'completed_at', 'duration_ms',
            'result_data', 'execution_steps', 'updated_at'
        ])

    def mark_as_failed(self, error_message: str, error_traceback: str = None):
        """Mark execution as failed"""
        self.status = ExecutionStatusEnum.FAILED
        self.completed_at = timezone.now()
        self.error_message = error_message
        self.error_traceback = error_traceback

        if self.started_at:
            duration = (self.completed_at - self.started_at).total_seconds() * 1000
            self.duration_ms = int(duration)

        self.save(update_fields=[
            'status', 'completed_at', 'duration_ms',
            'error_message', 'error_traceback', 'updated_at'
        ])


class DuplicateDetectionCache(models.Model):
    """
    Cache for duplicate detection.
    Prevents creating duplicate leads from the same source row.
    """
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True, help_text='Tenant ID for multi-tenancy')

    workflow = models.ForeignKey(
        Workflow,
        on_delete=models.CASCADE,
        related_name='duplicate_cache',
        db_column='workflow_id'
    )

    # Source identifier (e.g., sheet_id + row_number, or unique hash of data)
    source_identifier = models.CharField(
        max_length=500,
        help_text='Unique identifier from source (e.g., sheet_id:row_number)'
    )

    # What was created
    created_object_type = models.CharField(
        max_length=50,
        help_text='Type of object created (Lead, Task, etc.)'
    )
    created_object_id = models.BigIntegerField(help_text='ID of created object')

    # Metadata
    source_data_hash = models.CharField(
        max_length=64,
        help_text='Hash of source data for change detection'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'duplicate_detection_cache'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_dup_cache_tenant'),
            models.Index(fields=['workflow'], name='idx_dup_cache_workflow'),
            models.Index(fields=['source_identifier'], name='idx_dup_cache_source_id'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['workflow', 'source_identifier'],
                name='unique_source_per_workflow'
            )
        ]

    def __str__(self):
        return f"{self.workflow.name} - {self.source_identifier}"


# ===========================================================================
# COMPOSIO
# ===========================================================================
#
# Composio (https://composio.dev) brokers OAuth for hundreds of third-party
# toolkits - priority: Gmail, Notion, Google Drive, Google Calendar. It is a
# SIBLING of the native Google flow above, not a replacement: `Integration`,
# `Connection` and `Workflow` are untouched and keep owning Google Sheets.
#
# The defining difference: we store NO credentials for Composio connections.
# Composio holds the tokens; we hold only the identifiers needed to address
# them. `integrations/utils/encryption.py` is deliberately NOT used here -
# there is nothing to encrypt.
#
# Isolation rests entirely on `composio_user_id`, the Composio "entity" id:
#     {namespace}:{tenant_id}:{user_id}     per-user connection
#     {namespace}:{tenant_id}:tenant        tenant-wide shared connection
# built by integrations.services.composio_client.build_composio_user_id().
# It is persisted on every row AND re-derived and asserted before every
# outbound call, so a tampered row can never redirect a call at another
# tenant's entity.
# ===========================================================================


class ComposioAuthSchemeEnum(models.TextChoices):
    """Auth schemes Composio reports for an auth config."""
    OAUTH2 = 'OAUTH2', 'OAuth 2.0'
    OAUTH1 = 'OAUTH1', 'OAuth 1.0'
    API_KEY = 'API_KEY', 'API Key'
    BEARER_TOKEN = 'BEARER_TOKEN', 'Bearer Token'
    BASIC = 'BASIC', 'Basic Auth'
    NO_AUTH = 'NO_AUTH', 'No Auth'


class ComposioConnectionStatusEnum(models.TextChoices):
    """Mirrors Composio connected-account statuses, plus our local pre-states."""
    PENDING = 'PENDING', 'Pending'                 # row created, link() not called yet
    INITIALIZING = 'INITIALIZING', 'Initializing'  # link() returned; user is authorising
    ACTIVE = 'ACTIVE', 'Active'
    INACTIVE = 'INACTIVE', 'Inactive'              # disabled at Composio; cannot execute tools
    FAILED = 'FAILED', 'Failed'
    EXPIRED = 'EXPIRED', 'Expired'
    REVOKED = 'REVOKED', 'Revoked'
    DELETED = 'DELETED', 'Deleted'                 # local tombstone after deletion at Composio

    @classmethod
    def terminal(cls):
        """Statuses a connection can never recover from."""
        return [cls.DELETED, cls.REVOKED, cls.FAILED]


class ComposioConnectionScopeEnum(models.TextChoices):
    """Who a connection belongs to."""
    USER = 'USER', 'Per user'
    TENANT = 'TENANT', 'Tenant-wide (shared)'


class ComposioEventTypeEnum(models.TextChoices):
    """Lifecycle events recorded on the append-only audit trail."""
    INITIATED = 'INITIATED', 'Connection initiated'
    CALLBACK = 'CALLBACK', 'Callback received'
    ACTIVATED = 'ACTIVATED', 'Connection activated'
    FAILED = 'FAILED', 'Connection failed'
    REFRESHED = 'REFRESHED', 'Re-authorised'
    DISABLED = 'DISABLED', 'Disabled'
    ENABLED = 'ENABLED', 'Enabled'
    DISCONNECTED = 'DISCONNECTED', 'Disconnected'
    STATUS_SYNC = 'STATUS_SYNC', 'Status synchronised'
    TOOL_EXECUTED = 'TOOL_EXECUTED', 'Tool executed'
    WEBHOOK = 'WEBHOOK', 'Webhook received'
    ERROR = 'ERROR', 'Error'


class ComposioAuthConfig(models.Model):
    """
    A Composio auth config ("ac_...") as we know it.

    Composio auth configs are project-level, not tenant-level. We keep a row per
    (scope, toolkit) so that:
      * tenant_id IS NULL  -> the platform-wide, Composio-managed config that
        every tenant uses for this toolkit (the normal case);
      * tenant_id set      -> a tenant that brought its own OAuth app.
    Resolution always prefers the tenant-specific row, falling back to global.

    We never store the customer's OAuth client_secret here - Composio holds it.
    """
    id = models.BigAutoField(primary_key=True)
    public_id = models.UUIDField(
        default=uuid.uuid4, unique=True, editable=False, db_index=True,
        help_text='Stable, non-sequential identifier safe to expose in API URLs.'
    )

    tenant_id = models.UUIDField(
        null=True, blank=True, db_index=True,
        help_text='Tenant that owns this auth config. NULL = platform-wide default.'
    )

    toolkit_slug = models.CharField(
        max_length=100, db_index=True,
        help_text='Composio toolkit slug, uppercase (e.g. GMAIL, NOTION, GOOGLEDRIVE, GOOGLECALENDAR).'
    )
    auth_config_id = models.CharField(
        max_length=64, unique=True,
        help_text='Composio auth config id ("ac_..."). Unique across the platform.'
    )
    auth_scheme = models.CharField(
        max_length=20, choices=ComposioAuthSchemeEnum.choices,
        default=ComposioAuthSchemeEnum.OAUTH2,
        help_text='Auth scheme Composio reports for this config.'
    )
    is_composio_managed = models.BooleanField(
        default=True,
        help_text='True when Composio owns the OAuth app (no client credentials of ours).'
    )

    name = models.CharField(max_length=200, help_text='Display name shown to tenant admins.')
    is_active = models.BooleanField(
        default=True, db_index=True,
        help_text='Whether users may create new connections against this config.'
    )

    restrict_to_tools = models.JSONField(
        null=True, blank=True,
        help_text='Allowlist of tool slugs this config may execute through /execute/. '
                  'Empty or NULL means no tool may be executed (fail closed).'
    )
    default_tool_versions = models.JSONField(
        null=True, blank=True,
        help_text='Map of tool slug to pinned Composio tool version, e.g. {"GMAIL_GET_PROFILE": "20251111_00"}.'
    )
    metadata = models.JSONField(
        null=True, blank=True,
        help_text='Raw non-secret auth-config payload from Composio (expected_input_fields, scopes). '
                  'Scrubbed before persisting; never contains secrets.'
    )

    last_synced_at = models.DateTimeField(
        null=True, blank=True, help_text='When we last reconciled this row against Composio.'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'composio_auth_configs'
        ordering = ['toolkit_slug', 'name']
        indexes = [
            models.Index(fields=['toolkit_slug'], name='idx_cmp_authcfg_toolkit'),
            models.Index(fields=['tenant_id'], name='idx_cmp_authcfg_tenant'),
            models.Index(fields=['tenant_id', 'toolkit_slug'], name='idx_cmp_authcfg_lookup'),
            models.Index(fields=['is_active'], name='idx_cmp_authcfg_active'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['toolkit_slug'],
                condition=models.Q(tenant_id__isnull=True),
                name='uniq_cmp_authcfg_global_per_toolkit',
            ),
            models.UniqueConstraint(
                fields=['tenant_id', 'toolkit_slug'],
                condition=models.Q(tenant_id__isnull=False),
                name='uniq_cmp_authcfg_tenant_per_toolkit',
            ),
        ]

    def __str__(self):
        scope = self.tenant_id or 'global'
        return f"{self.toolkit_slug} ({scope}) - {self.auth_config_id}"

    @classmethod
    def resolve(cls, toolkit_slug, tenant_id):
        """
        Return the auth config a tenant should use for a toolkit.

        A tenant's own BYO-OAuth config wins; otherwise the platform-wide
        (tenant_id IS NULL) Composio-managed row. Returns None when neither
        exists - callers must treat that as "toolkit not connectable yet".
        """
        return (cls.objects
                .filter(toolkit_slug=str(toolkit_slug).upper(), is_active=True)
                .filter(models.Q(tenant_id=tenant_id) | models.Q(tenant_id__isnull=True))
                .order_by(models.F('tenant_id').desc(nulls_last=True))
                .first())


class ComposioConnection(models.Model):
    """
    A Composio connected account, owned by one (tenant, user) pair.

    No credentials are stored here. Composio holds the tokens; we hold the
    identifiers needed to address them. ``composio_user_id`` is the isolation
    boundary - see ``build_composio_user_id``. It is persisted (not merely
    derived) so historical rows survive any change to the derivation rule, and
    every outbound call re-derives it and asserts equality (defence in depth).
    """
    id = models.BigAutoField(primary_key=True)
    public_id = models.UUIDField(
        default=uuid.uuid4, unique=True, editable=False, db_index=True,
        help_text='Stable, non-sequential identifier used in API URLs and OAuth return links.'
    )

    tenant_id = models.UUIDField(db_index=True, help_text='Tenant ID for multi-tenancy.')
    user_id = models.UUIDField(db_index=True, help_text='CRM user who owns this connection.')
    scope = models.CharField(
        max_length=10, choices=ComposioConnectionScopeEnum.choices,
        default=ComposioConnectionScopeEnum.USER,
        help_text='USER = private to the owner. TENANT = shared with everyone in the tenant.'
    )

    composio_user_id = models.CharField(
        max_length=200, db_index=True,
        help_text='Composio entity id: "{namespace}:{tenant_id}:{user_id|tenant}". '
                  'The only isolation boundary Composio enforces between end users.'
    )

    auth_config = models.ForeignKey(
        ComposioAuthConfig, on_delete=models.PROTECT,
        related_name='connections', db_column='auth_config_id',
        help_text='Auth config this connection was created against.'
    )
    toolkit_slug = models.CharField(
        max_length=100, db_index=True,
        help_text='Denormalised from auth_config for cheap filtering (e.g. GMAIL).'
    )

    connected_account_id = models.CharField(
        max_length=64, null=True, blank=True, unique=True,
        help_text='Composio connected account id ("ca_..."). link() returns it BEFORE the user '
                  'authorises, so it is set from initiate time onward.'
    )

    status = models.CharField(
        max_length=20, choices=ComposioConnectionStatusEnum.choices,
        default=ComposioConnectionStatusEnum.PENDING, db_index=True,
        help_text='Local mirror of the Composio connected-account status.'
    )
    alias = models.CharField(
        max_length=200, null=True, blank=True,
        help_text='User-facing label (e.g. "Sales inbox"). Passed to Composio as alias.'
    )
    account_label = models.CharField(
        max_length=320, null=True, blank=True,
        help_text='Third-party account identifier surfaced by the provider (e.g. the connected '
                  'Gmail address), when Composio returns it. Display only, never a credential.'
    )

    granted_scopes = models.JSONField(
        null=True, blank=True,
        help_text='OAuth scopes Composio reports as granted, when available.'
    )
    expires_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Credential expiry as reported by Composio, when available. We do NOT hold the token.'
    )

    connected_at = models.DateTimeField(
        null=True, blank=True, help_text='When the account first became ACTIVE.'
    )
    last_status_check_at = models.DateTimeField(
        null=True, blank=True, help_text='Last time we polled Composio for status.'
    )
    last_used_at = models.DateTimeField(
        null=True, blank=True, help_text='Last time a tool was executed with this account.'
    )
    disconnected_at = models.DateTimeField(
        null=True, blank=True, help_text='When this connection was disconnected/deleted.'
    )

    last_error = models.TextField(null=True, blank=True, help_text='Last error message from Composio, if any.')
    last_error_at = models.DateTimeField(null=True, blank=True, help_text='When the last error occurred.')

    metadata = models.JSONField(
        null=True, blank=True,
        help_text='Raw NON-SECRET Composio payload (toolkit meta, account type, link expiry). '
                  'Always passed through composio_client.scrub() first - state.val is dropped entirely.'
    )

    created_by_user_id = models.UUIDField(
        null=True, blank=True,
        help_text='User who initiated the connection (differs from user_id for TENANT scope).'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'composio_connections'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_cmp_conn_tenant'),
            models.Index(fields=['user_id'], name='idx_cmp_conn_user'),
            models.Index(fields=['status'], name='idx_cmp_conn_status'),
            models.Index(fields=['toolkit_slug'], name='idx_cmp_conn_toolkit'),
            models.Index(fields=['composio_user_id'], name='idx_cmp_conn_entity'),
            models.Index(fields=['tenant_id', 'user_id', 'toolkit_slug'], name='idx_cmp_conn_lookup'),
            models.Index(fields=['tenant_id', 'status'], name='idx_cmp_conn_tenant_status'),
        ]
        constraints = [
            # One LIVE connection per (tenant, user, toolkit, alias). Terminal
            # rows are kept as history and excluded so a user can reconnect.
            models.UniqueConstraint(
                fields=['tenant_id', 'user_id', 'toolkit_slug', 'alias'],
                condition=~models.Q(status__in=['DELETED', 'REVOKED', 'FAILED']),
                name='uniq_cmp_conn_live_per_user_toolkit_alias',
            ),
        ]

    def __str__(self):
        return f"{self.toolkit_slug} - {self.alias or self.account_label or self.public_id}"

    # -- status transitions (house style: transitions live on the model) --
    def mark_initializing(self, connected_account_id=None, metadata=None):
        """link() succeeded - Composio now owns a pending connected account."""
        self.status = ComposioConnectionStatusEnum.INITIALIZING
        if connected_account_id:
            self.connected_account_id = connected_account_id
        if metadata is not None:
            self.metadata = metadata
        self.save(update_fields=['status', 'connected_account_id', 'metadata', 'updated_at'])

    def mark_active(self, connected_account_id=None, metadata=None):
        self.status = ComposioConnectionStatusEnum.ACTIVE
        if connected_account_id:
            self.connected_account_id = connected_account_id
        if self.connected_at is None:
            self.connected_at = timezone.now()
        self.last_status_check_at = timezone.now()
        self.last_error = None
        self.last_error_at = None
        if metadata is not None:
            self.metadata = metadata
        self.save(update_fields=['status', 'connected_account_id', 'connected_at',
                                 'last_status_check_at', 'last_error', 'last_error_at',
                                 'metadata', 'updated_at'])

    def mark_failed(self, error_message):
        self.status = ComposioConnectionStatusEnum.FAILED
        self.last_error = str(error_message)[:2000]
        self.last_error_at = timezone.now()
        self.save(update_fields=['status', 'last_error', 'last_error_at', 'updated_at'])

    def mark_disconnected(self, status=ComposioConnectionStatusEnum.DELETED):
        self.status = status
        self.disconnected_at = timezone.now()
        self.save(update_fields=['status', 'disconnected_at', 'updated_at'])

    def record_event(self, event_type, actor_user_id=None, message=None,
                     payload=None, source_ip=None):
        """Append a row to the audit trail. Always tenant-stamped from self."""
        return ComposioConnectionEvent.objects.create(
            tenant_id=self.tenant_id,
            connection=self,
            event_type=event_type,
            actor_user_id=actor_user_id,
            message=(str(message)[:2000] if message else None),
            payload=payload,
            source_ip=source_ip,
        )

    @property
    def is_usable(self):
        """A connection can only execute tools when ACTIVE and addressable."""
        return (self.status == ComposioConnectionStatusEnum.ACTIVE
                and bool(self.connected_account_id))

    @property
    def is_terminal(self):
        return self.status in ComposioConnectionStatusEnum.terminal()


class ComposioConnectionEvent(models.Model):
    """
    Append-only audit trail for a Composio connection. Never mutated.

    Answers "who connected/disconnected what, when, from where" - required for
    the tenant-admin oversight screen and for incident response.
    """
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True, help_text='Tenant ID for multi-tenancy.')

    connection = models.ForeignKey(
        ComposioConnection, on_delete=models.CASCADE,
        related_name='events', db_column='connection_id',
        help_text='Connection this event belongs to.'
    )

    event_type = models.CharField(
        max_length=20, choices=ComposioEventTypeEnum.choices, db_index=True,
        help_text='What happened.'
    )
    actor_user_id = models.UUIDField(
        null=True, blank=True,
        help_text='User who caused the event. NULL for system/webhook-driven events.'
    )
    message = models.TextField(null=True, blank=True, help_text='Human-readable summary.')
    payload = models.JSONField(
        null=True, blank=True,
        help_text='Scrubbed context (tool slug, status transition, error code). Never secrets.'
    )
    source_ip = models.GenericIPAddressField(
        null=True, blank=True, help_text='Client IP for user-initiated events.'
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'composio_connection_events'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_cmp_evt_tenant'),
            models.Index(fields=['connection', '-created_at'], name='idx_cmp_evt_conn_recent'),
            models.Index(fields=['tenant_id', 'event_type', '-created_at'], name='idx_cmp_evt_lookup'),
        ]

    def __str__(self):
        return f"{self.connection_id} - {self.event_type}"


class ComposioLinkState(models.Model):
    """
    One-time-use, short-lived nonce binding a Composio hosted-auth redirect
    back to the (tenant, user, connection) that started it.

    Stored in Postgres rather than the Django cache: CACHES is FileBasedCache,
    which is not shared across app instances, and the legacy Google OAuth flow
    already had to add read-back verification logging because of it. The nonce
    is the ONLY authenticator on the public callback endpoint, so it must be
    durable.
    """
    id = models.BigAutoField(primary_key=True)
    state = models.CharField(
        max_length=64, unique=True, db_index=True,
        help_text='Opaque nonce (secrets.token_urlsafe(32)) echoed back on the callback URL.'
    )

    tenant_id = models.UUIDField(db_index=True, help_text='Tenant that started the flow.')
    user_id = models.UUIDField(db_index=True, help_text='User that started the flow.')

    connection = models.ForeignKey(
        ComposioConnection, on_delete=models.CASCADE,
        related_name='link_states', db_column='connection_id',
        help_text='Connection this hosted-auth round trip belongs to.'
    )
    toolkit_slug = models.CharField(
        max_length=100, help_text='Toolkit being connected (for logging and the return redirect).'
    )

    return_to = models.CharField(
        max_length=500, null=True, blank=True,
        help_text='Relative frontend path to return the user to. Validated against '
                  'settings.COMPOSIO_RETURN_TO_ALLOWLIST; never an absolute URL.'
    )
    link_expires_at = models.DateTimeField(
        null=True, blank=True,
        help_text='expires_at returned by Composio for the auth link, when available.'
    )

    expires_at = models.DateTimeField(
        db_index=True,
        help_text='When this nonce stops being accepted (now + COMPOSIO_LINK_TTL_SECONDS).'
    )
    consumed_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When the nonce was redeemed. Non-NULL = spent, replay rejected.'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'composio_link_states'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['expires_at'], name='idx_cmp_state_expiry'),
            models.Index(fields=['tenant_id', 'user_id'], name='idx_cmp_state_tenant_user'),
        ]

    def __str__(self):
        return f"{self.toolkit_slug} - {self.state[:8]}"

    def is_valid(self):
        """Unspent and unexpired."""
        return self.consumed_at is None and timezone.now() < self.expires_at

    def consume(self):
        """Mark spent. Callers MUST hold select_for_update() to make this atomic."""
        self.consumed_at = timezone.now()
        self.save(update_fields=['consumed_at'])


class ComposioToolkit(models.Model):
    """
    Local cache of Composio's toolkit catalogue.

    Refreshed by integrations.tasks.sync_composio_toolkits (Celery beat, daily)
    and by manage.py sync_composio_toolkits. Global, not tenant-scoped: the
    catalogue is identical for everyone. Per-tenant availability is decided by
    ComposioAuthConfig plus is_enabled.
    """
    id = models.BigAutoField(primary_key=True)
    slug = models.CharField(
        max_length=100, unique=True, db_index=True,
        help_text='Composio toolkit slug, uppercase (GMAIL, NOTION, GOOGLEDRIVE, GOOGLECALENDAR).'
    )
    name = models.CharField(max_length=200, help_text='Human-readable toolkit name.')
    description = models.TextField(null=True, blank=True, help_text='Short description from Composio meta.')
    logo_url = models.URLField(
        max_length=500, null=True, blank=True, help_text='Toolkit logo URL from Composio meta.'
    )
    categories = models.JSONField(null=True, blank=True, help_text='Category slugs reported by Composio.')

    auth_schemes = models.JSONField(null=True, blank=True, help_text='All auth schemes the toolkit supports.')
    composio_managed_auth_schemes = models.JSONField(
        null=True, blank=True,
        help_text='Schemes Composio manages the OAuth app for. Non-empty means one-click connect with '
                  'no credentials of ours.'
    )
    no_auth = models.BooleanField(default=False, help_text='Toolkit requires no authentication at all.')

    tools_count = models.IntegerField(default=0, help_text='Number of tools, from Composio meta.')
    triggers_count = models.IntegerField(default=0, help_text='Number of triggers, from Composio meta.')

    is_enabled = models.BooleanField(
        default=False, db_index=True,
        help_text='Whether tenants may connect this toolkit. Default False - we opt toolkits in '
                  'explicitly rather than exposing the whole Composio catalogue.'
    )
    is_featured = models.BooleanField(
        default=False, db_index=True, help_text='Pinned to the top of the catalogue grid.'
    )
    sort_order = models.IntegerField(default=1000, help_text='Manual ordering within the catalogue.')

    metadata = models.JSONField(null=True, blank=True, help_text='Raw non-secret toolkit payload from Composio.')
    last_synced_at = models.DateTimeField(
        null=True, blank=True, help_text='When this row was last refreshed from Composio.'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'composio_toolkits'
        ordering = ['-is_featured', 'sort_order', 'name']
        indexes = [
            models.Index(fields=['slug'], name='idx_cmp_toolkit_slug'),
            models.Index(fields=['is_enabled', 'is_featured'], name='idx_cmp_toolkit_visible'),
        ]

    def __str__(self):
        return f"{self.name} ({self.slug})"

    @property
    def supports_managed_auth(self):
        """True when Composio owns an OAuth app for this toolkit."""
        return bool(self.composio_managed_auth_schemes)
