"""
Django REST Framework Serializers for Integration System

Provides serializers for all integration models with validation
and nested representations where needed.
"""

from rest_framework import serializers
from django.utils import timezone

from integrations.models import (
    Integration, Connection, Workflow, WorkflowTrigger,
    WorkflowAction, WorkflowMapping, ExecutionLog,
    DuplicateDetectionCache,
    ConnectionStatusEnum, ExecutionStatusEnum
)


class IntegrationSerializer(serializers.ModelSerializer):
    """
    Serialize available integration provider definitions.

    Agents use this schema to discover which external systems, such as Google
    Sheets or webhooks, can be connected to the CRM.
    """

    class Meta:
        model = Integration
        fields = [
            'id', 'name', 'type', 'description', 'icon_url',
            'is_active', 'requires_oauth', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {
            'id': {'help_text': 'Unique numeric identifier for this integration provider. Read-only.'},
            'name': {'help_text': 'Human-readable provider name, such as Google Sheets.'},
            'type': {'help_text': 'Integration provider type. Valid values include GOOGLE_SHEETS, WEBHOOK, ZAPIER, API, and EMAIL.'},
            'description': {'help_text': 'Plain-language description of what this integration provider does.'},
            'icon_url': {'help_text': 'Optional URL for the provider logo or icon.'},
            'is_active': {'help_text': 'Whether this provider is currently available for tenant connections.'},
            'requires_oauth': {'help_text': 'Whether this provider requires an OAuth authorization flow before use.'},
            'created_at': {'help_text': 'Timestamp when this provider definition was created, in ISO 8601 date-time format. Read-only.'},
            'updated_at': {'help_text': 'Timestamp when this provider definition was last updated, in ISO 8601 date-time format. Read-only.'},
        }


class ConnectionListSerializer(serializers.ModelSerializer):
    """
    Serialize compact integration connection records.

    Agents use this schema to list connected accounts without exposing encrypted
    tokens or full connection metadata.
    """
    integration_name = serializers.CharField(
        source='integration.name',
        read_only=True,
        help_text='Human-readable name of the connected integration provider. Read-only.'
    )
    integration_type = serializers.CharField(
        source='integration.type',
        read_only=True,
        help_text='Provider type for the connected integration. Read-only.'
    )
    is_token_expired = serializers.SerializerMethodField(
        help_text='True when the stored access token has expired or should be refreshed. Read-only.'
    )

    class Meta:
        model = Connection
        fields = [
            'id', 'name', 'status', 'integration', 'integration_name',
            'integration_type', 'connected_at', 'last_used_at',
            'is_token_expired', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'status', 'connected_at', 'last_used_at', 'created_at', 'updated_at']
        extra_kwargs = {
            'id': {'help_text': 'Unique numeric identifier for this connection. Read-only.'},
            'name': {'help_text': 'User-friendly connection name, such as Marketing Leads Sheet.'},
            'status': {'help_text': 'Connection status. Valid values are CONNECTED, DISCONNECTED, EXPIRED, or ERROR. Read-only.'},
            'integration': {'help_text': 'Numeric ID of the integration provider this connection uses.'},
            'connected_at': {'help_text': 'Timestamp when this connection was established, in ISO 8601 date-time format. Read-only.'},
            'last_used_at': {'help_text': 'Timestamp when this connection was last used, in ISO 8601 date-time format. Read-only.'},
            'created_at': {'help_text': 'Timestamp when this connection was created, in ISO 8601 date-time format. Read-only.'},
            'updated_at': {'help_text': 'Timestamp when this connection was last updated, in ISO 8601 date-time format. Read-only.'},
        }

    def get_is_token_expired(self, obj) -> bool:
        """Check if token is expired"""
        return obj.is_token_expired()


class ConnectionDetailSerializer(serializers.ModelSerializer):
    """
    Serialize detailed integration connection records.

    Agents use this schema to inspect connection health, token expiry, provider
    details, and workflow usage for a tenant-owned integration connection.
    """
    integration = IntegrationSerializer(read_only=True)
    is_token_expired = serializers.SerializerMethodField(
        help_text='True when the stored access token has expired or should be refreshed. Read-only.'
    )
    workflows_count = serializers.SerializerMethodField(
        help_text='Number of non-deleted workflows currently using this connection. Read-only.'
    )

    class Meta:
        model = Connection
        fields = [
            'id', 'tenant_id', 'user_id', 'integration', 'name', 'status',
            'connection_data', 'token_expires_at', 'last_error', 'last_error_at',
            'connected_at', 'last_used_at', 'is_token_expired', 'workflows_count',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'tenant_id', 'user_id', 'status', 'token_expires_at',
            'last_error', 'last_error_at', 'connected_at', 'last_used_at',
            'created_at', 'updated_at'
        ]
        extra_kwargs = {
            'id': {'help_text': 'Unique numeric identifier for this connection. Read-only.'},
            'tenant_id': {'help_text': 'UUID of the tenant that owns this connection. Read-only.'},
            'user_id': {'help_text': 'UUID of the user who owns or created this connection. Read-only.'},
            'name': {'help_text': 'User-friendly connection name, such as Marketing Leads Sheet.'},
            'status': {'help_text': 'Connection status. Valid values are CONNECTED, DISCONNECTED, EXPIRED, or ERROR. Read-only.'},
            'connection_data': {'help_text': 'Optional JSON metadata about the connected account, such as account email or selected sheet IDs.'},
            'token_expires_at': {'help_text': 'Timestamp when the access token expires, in ISO 8601 date-time format. Read-only.'},
            'last_error': {'help_text': 'Most recent connection error message, if any. Read-only.'},
            'last_error_at': {'help_text': 'Timestamp when the most recent error occurred, in ISO 8601 date-time format. Read-only.'},
            'connected_at': {'help_text': 'Timestamp when this connection was established, in ISO 8601 date-time format. Read-only.'},
            'last_used_at': {'help_text': 'Timestamp when this connection was last used, in ISO 8601 date-time format. Read-only.'},
            'created_at': {'help_text': 'Timestamp when this connection was created, in ISO 8601 date-time format. Read-only.'},
            'updated_at': {'help_text': 'Timestamp when this connection was last updated, in ISO 8601 date-time format. Read-only.'},
        }

    def get_is_token_expired(self, obj) -> bool:
        """Check if token is expired"""
        return obj.is_token_expired()

    def get_workflows_count(self, obj) -> int:
        """Get count of workflows using this connection"""
        return obj.workflows.filter(is_deleted=False).count()


class WorkflowMappingSerializer(serializers.ModelSerializer):
    """
    Serialize field mappings between source data and CRM destination fields.

    Agents use mappings to understand how incoming columns or JSON keys are
    transformed into lead, task, or workflow action fields.
    """

    class Meta:
        model = WorkflowMapping
        fields = [
            'id', 'source_field', 'destination_field', 'transformation',
            'default_value', 'is_required', 'validation_rules',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {
            'id': {'help_text': 'Unique numeric identifier for this field mapping. Read-only.'},
            'source_field': {'help_text': 'Source field name from the trigger data, such as a spreadsheet column name.'},
            'destination_field': {'help_text': 'Destination CRM field name that receives the source value, such as lead.name or lead.email.'},
            'transformation': {'help_text': 'Optional JSON object describing transformations such as trim, lowercase, formatting, or parsing rules.'},
            'default_value': {'help_text': 'Optional fallback value used when the source field is empty or missing.'},
            'is_required': {'help_text': 'Whether the mapping must produce a value for the workflow action to proceed.'},
            'validation_rules': {'help_text': 'Optional JSON validation rules such as regex, min length, max length, or allowed values.'},
            'created_at': {'help_text': 'Timestamp when this mapping was created, in ISO 8601 date-time format. Read-only.'},
            'updated_at': {'help_text': 'Timestamp when this mapping was last updated, in ISO 8601 date-time format. Read-only.'},
        }


class WorkflowActionSerializer(serializers.ModelSerializer):
    """
    Serialize workflow actions executed after a trigger fires.

    Agents use actions to understand what the automation does, such as creating
    a lead or sending a webhook, and in what order each action runs.
    """
    field_mappings = WorkflowMappingSerializer(
        many=True,
        read_only=True,
        help_text='Field mappings used by this action to transform trigger data into destination fields. Read-only.'
    )

    class Meta:
        model = WorkflowAction
        fields = [
            'id', 'action_type', 'order', 'action_config', 'conditions',
            'retry_on_failure', 'max_retries', 'field_mappings',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {
            'id': {'help_text': 'Unique numeric identifier for this workflow action. Read-only.'},
            'action_type': {'help_text': 'Action type to execute. Valid values include CREATE_LEAD, UPDATE_LEAD, CREATE_TASK, SEND_EMAIL, and WEBHOOK.'},
            'order': {'help_text': 'Integer execution order. Lower numbers run earlier.'},
            'action_config': {'help_text': 'JSON configuration for this action, such as target object, default values, or endpoint details.'},
            'conditions': {'help_text': 'Optional JSON conditions that must be satisfied before this action runs.'},
            'retry_on_failure': {'help_text': 'Whether this action should be retried if it fails.'},
            'max_retries': {'help_text': 'Maximum number of retry attempts for this action.'},
            'created_at': {'help_text': 'Timestamp when this action was created, in ISO 8601 date-time format. Read-only.'},
            'updated_at': {'help_text': 'Timestamp when this action was last updated, in ISO 8601 date-time format. Read-only.'},
        }


class WorkflowTriggerSerializer(serializers.ModelSerializer):
    """
    Serialize workflow trigger configuration and polling state.

    Agents use triggers to understand what starts an automation, such as a new
    Google Sheets row, an updated row, a webhook event, or a manual run.
    """
    should_poll = serializers.SerializerMethodField(
        help_text='True when this trigger is due to be checked based on its polling interval. Read-only.'
    )

    class Meta:
        model = WorkflowTrigger
        fields = [
            'id', 'trigger_type', 'trigger_config', 'poll_interval_minutes',
            'last_checked_at', 'last_processed_record', 'should_poll',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'last_checked_at', 'last_processed_record', 'created_at', 'updated_at']
        extra_kwargs = {
            'id': {'help_text': 'Unique numeric identifier for this trigger. Read-only.'},
            'trigger_type': {'help_text': 'Trigger type. Valid values include NEW_ROW, UPDATED_ROW, WEBHOOK_RECEIVED, SCHEDULE, and MANUAL.'},
            'trigger_config': {'help_text': 'JSON configuration for the trigger, such as spreadsheet_id and sheet_name for Google Sheets.'},
            'poll_interval_minutes': {'help_text': 'Number of minutes between polling checks for polling-based triggers.'},
            'last_checked_at': {'help_text': 'Timestamp when this trigger was last checked, in ISO 8601 date-time format. Read-only.'},
            'last_processed_record': {'help_text': 'JSON metadata for the last processed source record, such as row number or timestamp. Read-only.'},
            'created_at': {'help_text': 'Timestamp when this trigger was created, in ISO 8601 date-time format. Read-only.'},
            'updated_at': {'help_text': 'Timestamp when this trigger was last updated, in ISO 8601 date-time format. Read-only.'},
        }

    def get_should_poll(self, obj) -> bool:
        """Check if trigger should be polled"""
        return obj.should_poll()


class WorkflowListSerializer(serializers.ModelSerializer):
    """
    Serialize compact workflow records for automation lists.

    Agents use this schema to browse workflows, check active state, and inspect
    recent execution health without loading full trigger and action details.
    """
    connection_name = serializers.CharField(
        source='connection.name',
        read_only=True,
        help_text='Name of the connection used by this workflow. Read-only.'
    )
    has_trigger = serializers.SerializerMethodField(
        help_text='True when this workflow has a trigger configured. Read-only.'
    )
    actions_count = serializers.SerializerMethodField(
        help_text='Number of actions configured on this workflow. Read-only.'
    )

    class Meta:
        model = Workflow
        fields = [
            'id', 'name', 'description', 'connection', 'connection_name',
            'is_active', 'last_executed_at', 'last_execution_status',
            'total_executions', 'successful_executions', 'failed_executions',
            'has_trigger', 'actions_count', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'last_executed_at', 'last_execution_status',
            'total_executions', 'successful_executions', 'failed_executions',
            'created_at', 'updated_at'
        ]
        extra_kwargs = {
            'id': {'help_text': 'Unique numeric identifier for this workflow. Read-only.'},
            'name': {'help_text': 'Human-readable workflow name.'},
            'description': {'help_text': 'Optional description of what this workflow automates.'},
            'connection': {'help_text': 'Numeric ID of the connection this workflow uses.'},
            'is_active': {'help_text': 'Whether this workflow is active and allowed to run.'},
            'last_executed_at': {'help_text': 'Timestamp when this workflow last ran, in ISO 8601 date-time format. Read-only.'},
            'last_execution_status': {'help_text': 'Status of the most recent execution. Read-only.'},
            'total_executions': {'help_text': 'Total number of workflow executions. Read-only.'},
            'successful_executions': {'help_text': 'Number of successful workflow executions. Read-only.'},
            'failed_executions': {'help_text': 'Number of failed workflow executions. Read-only.'},
            'created_at': {'help_text': 'Timestamp when this workflow was created, in ISO 8601 date-time format. Read-only.'},
            'updated_at': {'help_text': 'Timestamp when this workflow was last updated, in ISO 8601 date-time format. Read-only.'},
        }

    def get_has_trigger(self, obj) -> bool:
        """Check if workflow has a trigger"""
        return hasattr(obj, 'trigger')

    def get_actions_count(self, obj) -> int:
        """Get count of actions"""
        return obj.actions.count()


class WorkflowDetailSerializer(serializers.ModelSerializer):
    """
    Serialize full workflow configuration and execution summary.

    Agents use this schema when they need complete automation context including
    the connection, trigger, actions, success rate, and execution counters.
    """
    connection = ConnectionListSerializer(read_only=True)
    trigger = WorkflowTriggerSerializer(read_only=True)
    actions = WorkflowActionSerializer(
        many=True,
        read_only=True,
        help_text='Ordered list of actions configured for this workflow. Read-only.'
    )
    success_rate = serializers.SerializerMethodField(
        help_text='Successful executions divided by total executions as a percentage. Read-only.'
    )

    class Meta:
        model = Workflow
        fields = [
            'id', 'tenant_id', 'user_id', 'name', 'description',
            'connection', 'is_active', 'trigger', 'actions',
            'last_executed_at', 'last_execution_status',
            'total_executions', 'successful_executions', 'failed_executions',
            'success_rate', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'tenant_id', 'user_id', 'last_executed_at',
            'last_execution_status', 'total_executions',
            'successful_executions', 'failed_executions',
            'created_at', 'updated_at'
        ]
        extra_kwargs = {
            'id': {'help_text': 'Unique numeric identifier for this workflow. Read-only.'},
            'tenant_id': {'help_text': 'UUID of the tenant that owns this workflow. Read-only.'},
            'user_id': {'help_text': 'UUID of the user who created this workflow. Read-only.'},
            'name': {'help_text': 'Human-readable workflow name.'},
            'description': {'help_text': 'Optional description of what this workflow automates.'},
            'is_active': {'help_text': 'Whether this workflow is active and allowed to run.'},
            'last_executed_at': {'help_text': 'Timestamp when this workflow last ran, in ISO 8601 date-time format. Read-only.'},
            'last_execution_status': {'help_text': 'Status of the most recent execution. Read-only.'},
            'total_executions': {'help_text': 'Total number of workflow executions. Read-only.'},
            'successful_executions': {'help_text': 'Number of successful workflow executions. Read-only.'},
            'failed_executions': {'help_text': 'Number of failed workflow executions. Read-only.'},
            'created_at': {'help_text': 'Timestamp when this workflow was created, in ISO 8601 date-time format. Read-only.'},
            'updated_at': {'help_text': 'Timestamp when this workflow was last updated, in ISO 8601 date-time format. Read-only.'},
        }

    def get_success_rate(self, obj) -> float:
        """Calculate success rate percentage"""
        if obj.total_executions == 0:
            return 0

        return round((obj.successful_executions / obj.total_executions) * 100, 2)


class WorkflowCreateSerializer(serializers.ModelSerializer):
    """
    Validate requests that create automation workflows.

    Agents use this schema when a user wants to define a new automation attached
    to an existing connected account.
    """
    connection_id = serializers.IntegerField(
        write_only=True,
        help_text='Numeric ID of a connected integration account that the workflow should use.'
    )

    class Meta:
        model = Workflow
        fields = ['id', 'name', 'description', 'connection_id', 'is_active']
        read_only_fields = ['id']
        extra_kwargs = {
            'id': {'help_text': 'Unique numeric identifier for the created workflow. Read-only.'},
            'name': {'help_text': 'Human-readable workflow name.'},
            'description': {'help_text': 'Optional description of what this workflow automates.'},
            'is_active': {'help_text': 'Whether the workflow should be active immediately after creation.'},
        }

    def validate_connection_id(self, value):
        """Validate that connection exists and belongs to tenant"""
        request = self.context.get('request')
        if not request:
            raise serializers.ValidationError("Request context required")

        tenant_id = getattr(request, 'tenant_id', None)
        user_id = getattr(request, 'user_id', None)

        try:
            connection = Connection.objects.get(
                id=value,
                tenant_id=tenant_id
            )

            # Check connection status
            if connection.status != ConnectionStatusEnum.CONNECTED:
                raise serializers.ValidationError(
                    f"Connection is {connection.status}. Please reconnect."
                )

            return value

        except Connection.DoesNotExist:
            raise serializers.ValidationError("Connection not found")

    def create(self, validated_data):
        """Create workflow with tenant and user from request"""
        request = self.context.get('request')
        connection_id = validated_data.pop('connection_id')

        workflow = Workflow.objects.create(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            connection_id=connection_id,
            **validated_data
        )

        return workflow


class WorkflowTriggerCreateSerializer(serializers.ModelSerializer):
    """
    Validate requests that create workflow triggers.

    Agents use this schema to define what starts a workflow, such as a new row
    in a Google Sheet.
    """

    class Meta:
        model = WorkflowTrigger
        fields = [
            'trigger_type', 'trigger_config', 'poll_interval_minutes'
        ]
        extra_kwargs = {
            'trigger_type': {'help_text': 'Trigger type. Valid values include NEW_ROW, UPDATED_ROW, WEBHOOK_RECEIVED, SCHEDULE, and MANUAL.'},
            'trigger_config': {'help_text': 'JSON trigger configuration. For NEW_ROW, include spreadsheet_id and sheet_name.'},
            'poll_interval_minutes': {'help_text': 'Number of minutes between polling checks for polling-based triggers.'},
        }

    def validate_trigger_config(self, value):
        """Validate trigger configuration"""
        if not isinstance(value, dict):
            raise serializers.ValidationError("trigger_config must be a dictionary")

        # Validate based on trigger type
        trigger_type = self.initial_data.get('trigger_type')

        if trigger_type == 'NEW_ROW':
            required_fields = ['spreadsheet_id', 'sheet_name']
            for field in required_fields:
                if field not in value:
                    raise serializers.ValidationError(f"Missing required field: {field}")

        return value


class WorkflowActionCreateSerializer(serializers.ModelSerializer):
    """
    Validate requests that create workflow actions.

    Agents use this schema to add an ordered step that runs after a workflow
    trigger fires.
    """

    class Meta:
        model = WorkflowAction
        fields = [
            'action_type', 'order', 'action_config', 'conditions',
            'retry_on_failure', 'max_retries'
        ]
        extra_kwargs = {
            'action_type': {'help_text': 'Action type to execute. Valid values include CREATE_LEAD, UPDATE_LEAD, CREATE_TASK, SEND_EMAIL, and WEBHOOK.'},
            'order': {'help_text': 'Integer execution order. Lower numbers run earlier.'},
            'action_config': {'help_text': 'JSON configuration for this action, such as target object, default values, or endpoint details.'},
            'conditions': {'help_text': 'Optional JSON conditions that must be satisfied before this action runs.'},
            'retry_on_failure': {'help_text': 'Whether this action should be retried if it fails.'},
            'max_retries': {'help_text': 'Maximum number of retry attempts for this action.'},
        }

    def validate_action_config(self, value):
        """Validate action configuration"""
        if not isinstance(value, dict):
            raise serializers.ValidationError("action_config must be a dictionary")

        return value


class WorkflowMappingCreateSerializer(serializers.ModelSerializer):
    """
    Validate requests that create field mappings for workflow actions.

    Agents use this schema to map source fields, such as spreadsheet columns, to
    CRM destination fields.
    """

    class Meta:
        model = WorkflowMapping
        fields = [
            'source_field', 'destination_field', 'transformation',
            'default_value', 'is_required', 'validation_rules'
        ]
        extra_kwargs = {
            'source_field': {'help_text': 'Source field name from the trigger data, such as a spreadsheet column name.'},
            'destination_field': {'help_text': 'Destination CRM field name that receives the source value, such as lead.name or lead.email.'},
            'transformation': {'help_text': 'Optional JSON object describing transformations such as trim, lowercase, formatting, or parsing rules.'},
            'default_value': {'help_text': 'Optional fallback value used when the source field is empty or missing.'},
            'is_required': {'help_text': 'Whether the mapping must produce a value for the workflow action to proceed.'},
            'validation_rules': {'help_text': 'Optional JSON validation rules such as regex, min length, max length, or allowed values.'},
        }


class ExecutionLogSerializer(serializers.ModelSerializer):
    """
    Serialize detailed workflow execution logs.

    Agents use this schema to debug automation runs, inspect trigger input,
    result output, retry state, and error details.
    """
    workflow_name = serializers.CharField(
        source='workflow.name',
        read_only=True,
        help_text='Name of the workflow that produced this execution log. Read-only.'
    )
    duration_seconds = serializers.SerializerMethodField(
        help_text='Execution duration in seconds, derived from duration_ms. Read-only.'
    )

    class Meta:
        model = ExecutionLog
        fields = [
            'id', 'workflow', 'workflow_name', 'execution_id', 'status',
            'started_at', 'completed_at', 'duration_ms', 'duration_seconds',
            'trigger_data', 'result_data', 'error_message', 'error_traceback',
            'retry_count', 'is_retry', 'parent_execution_id', 'execution_steps',
            'created_at'
        ]
        read_only_fields = ['id', 'created_at']
        extra_kwargs = {
            'id': {'help_text': 'Unique numeric identifier for this execution log. Read-only.'},
            'workflow': {'help_text': 'Numeric ID of the workflow that produced this execution.'},
            'execution_id': {'help_text': 'UUID that uniquely identifies this execution attempt.'},
            'status': {'help_text': 'Execution status. Valid values are PENDING, RUNNING, SUCCESS, FAILED, or RETRYING.'},
            'started_at': {'help_text': 'Timestamp when execution started, in ISO 8601 date-time format.'},
            'completed_at': {'help_text': 'Timestamp when execution completed, in ISO 8601 date-time format, or null if not complete.'},
            'duration_ms': {'help_text': 'Execution duration in milliseconds, or null if not complete.'},
            'trigger_data': {'help_text': 'JSON input data that triggered this execution.'},
            'result_data': {'help_text': 'JSON output data produced by this execution, such as created object IDs.'},
            'error_message': {'help_text': 'Error message if execution failed.'},
            'error_traceback': {'help_text': 'Detailed traceback or diagnostic text for failed executions.'},
            'retry_count': {'help_text': 'Number of retry attempts associated with this execution.'},
            'is_retry': {'help_text': 'Whether this log represents a retry execution.'},
            'parent_execution_id': {'help_text': 'UUID of the original execution when this log is a retry.'},
            'execution_steps': {'help_text': 'JSON array or object containing step-by-step execution details.'},
            'created_at': {'help_text': 'Timestamp when this log record was created, in ISO 8601 date-time format. Read-only.'},
        }

    def get_duration_seconds(self, obj) -> float | None:
        """Get duration in seconds"""
        if obj.duration_ms:
            return round(obj.duration_ms / 1000, 2)
        return None


class ExecutionLogListSerializer(serializers.ModelSerializer):
    """
    Serialize compact workflow execution logs for list views.

    Agents use this schema to scan recent automation runs and identify failures.
    """
    workflow_name = serializers.CharField(
        source='workflow.name',
        read_only=True,
        help_text='Name of the workflow that produced this execution log. Read-only.'
    )

    class Meta:
        model = ExecutionLog
        fields = [
            'id', 'workflow', 'workflow_name', 'execution_id', 'status',
            'started_at', 'completed_at', 'duration_ms', 'retry_count',
            'error_message'
        ]
        read_only_fields = ['id']
        extra_kwargs = {
            'id': {'help_text': 'Unique numeric identifier for this execution log. Read-only.'},
            'workflow': {'help_text': 'Numeric ID of the workflow that produced this execution.'},
            'execution_id': {'help_text': 'UUID that uniquely identifies this execution attempt.'},
            'status': {'help_text': 'Execution status. Valid values are PENDING, RUNNING, SUCCESS, FAILED, or RETRYING.'},
            'started_at': {'help_text': 'Timestamp when execution started, in ISO 8601 date-time format.'},
            'completed_at': {'help_text': 'Timestamp when execution completed, in ISO 8601 date-time format, or null if not complete.'},
            'duration_ms': {'help_text': 'Execution duration in milliseconds, or null if not complete.'},
            'retry_count': {'help_text': 'Number of retry attempts associated with this execution.'},
            'error_message': {'help_text': 'Error message if execution failed.'},
        }


# API Request/Response Serializers

class OAuthInitiateSerializer(serializers.Serializer):
    """Validate requests that start an OAuth authorization flow."""
    integration_id = serializers.IntegerField(
        help_text='Numeric ID of the integration provider to connect.'
    )
    redirect_uri = serializers.URLField(
        required=False,
        help_text='Optional OAuth redirect URI override. Usually omitted so the server default is used.'
    )


class OAuthCallbackSerializer(serializers.Serializer):
    """Validate OAuth callback data after the external provider redirects back."""
    code = serializers.CharField(
        help_text='Authorization code returned by the OAuth provider.'
    )
    state = serializers.CharField(
        help_text='Opaque OAuth state value originally generated by the CRM.'
    )
    integration_id = serializers.IntegerField(
        help_text='Numeric ID of the integration provider being connected.'
    )
    connection_name = serializers.CharField(
        max_length=200,
        required=False,
        help_text='Optional user-friendly name for the new connection.'
    )


class SpreadsheetListSerializer(serializers.Serializer):
    """Serialize Google Sheets spreadsheet choices available to a connection."""
    id = serializers.CharField(help_text='Google spreadsheet ID.')
    name = serializers.CharField(help_text='Spreadsheet display name.')
    created_time = serializers.DateTimeField(
        source='createdTime',
        required=False,
        help_text='Spreadsheet creation timestamp in ISO 8601 date-time format.'
    )
    modified_time = serializers.DateTimeField(
        source='modifiedTime',
        required=False,
        help_text='Spreadsheet last modified timestamp in ISO 8601 date-time format.'
    )
    web_view_link = serializers.URLField(
        source='webViewLink',
        required=False,
        help_text='Google Sheets web URL for opening the spreadsheet.'
    )


class SheetListSerializer(serializers.Serializer):
    """Serialize worksheet tabs inside a Google spreadsheet."""
    sheet_id = serializers.IntegerField(help_text='Numeric Google Sheets tab ID.')
    title = serializers.CharField(help_text='Worksheet tab title.')
    index = serializers.IntegerField(help_text='Zero-based tab order inside the spreadsheet.')
    row_count = serializers.IntegerField(help_text='Number of rows available in the worksheet grid.')
    column_count = serializers.IntegerField(help_text='Number of columns available in the worksheet grid.')


class TestWorkflowSerializer(serializers.Serializer):
    """Validate requests that manually test a workflow."""
    trigger_data = serializers.JSONField(
        required=False,
        help_text="Optional JSON trigger data to execute the workflow with instead of reading from the configured trigger."
    )
    reset_last_processed = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Reset last_processed_record before test to read all rows"
    )
    clear_duplicates = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Clear duplicate detection cache before test to allow re-processing all rows"
    )


class WorkflowStatsSerializer(serializers.Serializer):
    """Serialize tenant-level workflow statistics."""
    total_workflows = serializers.IntegerField(help_text='Total number of non-deleted workflows for the tenant.')
    active_workflows = serializers.IntegerField(help_text='Number of workflows that are currently active.')
    total_executions = serializers.IntegerField(help_text='Total number of workflow executions across all workflows.')
    successful_executions = serializers.IntegerField(help_text='Number of successful workflow executions across all workflows.')
    failed_executions = serializers.IntegerField(help_text='Number of failed workflow executions across all workflows.')
    success_rate = serializers.FloatField(help_text='Successful executions divided by total executions as a percentage.')
