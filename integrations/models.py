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


class IntegrationTypeEnum(models.TextChoices):
    """Available integration types"""
    GOOGLE_SHEETS = 'GOOGLE_SHEETS', 'Google Sheets'
    WEBHOOK = 'WEBHOOK', 'Webhook'
    ZAPIER = 'ZAPIER', 'Zapier'
    API = 'API', 'Generic API'
    EMAIL = 'EMAIL', 'Email'


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
