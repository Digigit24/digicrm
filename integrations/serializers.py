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
    """Serializer for Integration model"""

    class Meta:
        model = Integration
        fields = [
            'id', 'name', 'type', 'description', 'icon_url',
            'is_active', 'requires_oauth', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class ConnectionListSerializer(serializers.ModelSerializer):
    """Serializer for listing connections"""
    integration_name = serializers.CharField(source='integration.name', read_only=True)
    integration_type = serializers.CharField(source='integration.type', read_only=True)
    is_token_expired = serializers.SerializerMethodField()

    class Meta:
        model = Connection
        fields = [
            'id', 'name', 'status', 'integration', 'integration_name',
            'integration_type', 'connected_at', 'last_used_at',
            'is_token_expired', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'status', 'connected_at', 'last_used_at', 'created_at', 'updated_at']

    def get_is_token_expired(self, obj):
        """Check if token is expired"""
        return obj.is_token_expired()


class ConnectionDetailSerializer(serializers.ModelSerializer):
    """Detailed serializer for Connection model"""
    integration = IntegrationSerializer(read_only=True)
    is_token_expired = serializers.SerializerMethodField()
    workflows_count = serializers.SerializerMethodField()

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

    def get_is_token_expired(self, obj):
        """Check if token is expired"""
        return obj.is_token_expired()

    def get_workflows_count(self, obj):
        """Get count of workflows using this connection"""
        return obj.workflows.filter(is_deleted=False).count()


class WorkflowMappingSerializer(serializers.ModelSerializer):
    """Serializer for WorkflowMapping model"""

    class Meta:
        model = WorkflowMapping
        fields = [
            'id', 'source_field', 'destination_field', 'transformation',
            'default_value', 'is_required', 'validation_rules',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class WorkflowActionSerializer(serializers.ModelSerializer):
    """Serializer for WorkflowAction model"""
    field_mappings = WorkflowMappingSerializer(many=True, read_only=True)

    class Meta:
        model = WorkflowAction
        fields = [
            'id', 'action_type', 'order', 'action_config', 'conditions',
            'retry_on_failure', 'max_retries', 'field_mappings',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class WorkflowTriggerSerializer(serializers.ModelSerializer):
    """Serializer for WorkflowTrigger model"""
    should_poll = serializers.SerializerMethodField()

    class Meta:
        model = WorkflowTrigger
        fields = [
            'id', 'trigger_type', 'trigger_config', 'poll_interval_minutes',
            'last_checked_at', 'last_processed_record', 'should_poll',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'last_checked_at', 'last_processed_record', 'created_at', 'updated_at']

    def get_should_poll(self, obj):
        """Check if trigger should be polled"""
        return obj.should_poll()


class WorkflowListSerializer(serializers.ModelSerializer):
    """Serializer for listing workflows"""
    connection_name = serializers.CharField(source='connection.name', read_only=True)
    has_trigger = serializers.SerializerMethodField()
    actions_count = serializers.SerializerMethodField()

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

    def get_has_trigger(self, obj):
        """Check if workflow has a trigger"""
        return hasattr(obj, 'trigger')

    def get_actions_count(self, obj):
        """Get count of actions"""
        return obj.actions.count()


class WorkflowDetailSerializer(serializers.ModelSerializer):
    """Detailed serializer for Workflow model"""
    connection = ConnectionListSerializer(read_only=True)
    trigger = WorkflowTriggerSerializer(read_only=True)
    actions = WorkflowActionSerializer(many=True, read_only=True)
    success_rate = serializers.SerializerMethodField()

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

    def get_success_rate(self, obj):
        """Calculate success rate percentage"""
        if obj.total_executions == 0:
            return 0

        return round((obj.successful_executions / obj.total_executions) * 100, 2)


class WorkflowCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating workflows"""
    connection_id = serializers.IntegerField(write_only=True)

    class Meta:
        model = Workflow
        fields = ['id', 'name', 'description', 'connection_id', 'is_active']
        read_only_fields = ['id']

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
    """Serializer for creating workflow triggers"""

    class Meta:
        model = WorkflowTrigger
        fields = [
            'trigger_type', 'trigger_config', 'poll_interval_minutes'
        ]

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
    """Serializer for creating workflow actions"""

    class Meta:
        model = WorkflowAction
        fields = [
            'action_type', 'order', 'action_config', 'conditions',
            'retry_on_failure', 'max_retries'
        ]

    def validate_action_config(self, value):
        """Validate action configuration"""
        if not isinstance(value, dict):
            raise serializers.ValidationError("action_config must be a dictionary")

        return value


class WorkflowMappingCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating field mappings"""

    class Meta:
        model = WorkflowMapping
        fields = [
            'source_field', 'destination_field', 'transformation',
            'default_value', 'is_required', 'validation_rules'
        ]


class ExecutionLogSerializer(serializers.ModelSerializer):
    """Serializer for ExecutionLog model"""
    workflow_name = serializers.CharField(source='workflow.name', read_only=True)
    duration_seconds = serializers.SerializerMethodField()

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

    def get_duration_seconds(self, obj):
        """Get duration in seconds"""
        if obj.duration_ms:
            return round(obj.duration_ms / 1000, 2)
        return None


class ExecutionLogListSerializer(serializers.ModelSerializer):
    """Simplified serializer for listing execution logs"""
    workflow_name = serializers.CharField(source='workflow.name', read_only=True)

    class Meta:
        model = ExecutionLog
        fields = [
            'id', 'workflow', 'workflow_name', 'execution_id', 'status',
            'started_at', 'completed_at', 'duration_ms', 'retry_count',
            'error_message'
        ]
        read_only_fields = ['id']


# API Request/Response Serializers

class OAuthInitiateSerializer(serializers.Serializer):
    """Serializer for OAuth initiation request"""
    integration_id = serializers.IntegerField()
    redirect_uri = serializers.URLField(required=False)


class OAuthCallbackSerializer(serializers.Serializer):
    """Serializer for OAuth callback"""
    code = serializers.CharField()
    state = serializers.CharField()
    integration_id = serializers.IntegerField()
    connection_name = serializers.CharField(max_length=200, required=False)


class SpreadsheetListSerializer(serializers.Serializer):
    """Serializer for spreadsheet list response"""
    id = serializers.CharField()
    name = serializers.CharField()
    created_time = serializers.DateTimeField(source='createdTime', required=False)
    modified_time = serializers.DateTimeField(source='modifiedTime', required=False)
    web_view_link = serializers.URLField(source='webViewLink', required=False)


class SheetListSerializer(serializers.Serializer):
    """Serializer for sheet list response"""
    sheet_id = serializers.IntegerField()
    title = serializers.CharField()
    index = serializers.IntegerField()
    row_count = serializers.IntegerField()
    column_count = serializers.IntegerField()


class TestWorkflowSerializer(serializers.Serializer):
    """Serializer for testing a workflow manually"""
    trigger_data = serializers.JSONField(required=False, help_text="Optional test data to use")
    reset_last_processed = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Reset last_processed_record before test to read all rows"
    )


class WorkflowStatsSerializer(serializers.Serializer):
    """Serializer for workflow statistics"""
    total_workflows = serializers.IntegerField()
    active_workflows = serializers.IntegerField()
    total_executions = serializers.IntegerField()
    successful_executions = serializers.IntegerField()
    failed_executions = serializers.IntegerField()
    success_rate = serializers.FloatField()
