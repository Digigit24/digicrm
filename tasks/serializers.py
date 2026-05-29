from rest_framework import serializers
from .models import Task
from common.mixins import TenantMixin


class TaskSerializer(TenantMixin):
    """
    Serialize lead-related task records.

    Agents use this schema to create and manage follow-up work, reminders,
    assignments, and checklist items connected to CRM leads.
    """
    lead_name = serializers.CharField(
        source='lead.name',
        read_only=True,
        help_text='Display name of the linked lead. Read-only.'
    )
    owner_user_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text='UUID of the task owner. If omitted during create, the CRM uses the authenticated JWT user_id.'
    )

    class Meta:
        model = Task
        fields = [
            'id', 'lead', 'lead_name', 'title', 'description', 'status',
            'priority', 'due_date', 'assignee_user_id', 'reporter_user_id',
            'owner_user_id', 'checklist', 'attachments_count', 'created_at',
            'updated_at', 'completed_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'completed_at']
        extra_kwargs = {
            'id': {'help_text': 'Unique numeric identifier for this task. Read-only.'},
            'lead': {'help_text': 'Numeric ID of the lead this task is related to.'},
            'title': {'help_text': 'Short task title describing the work to be done.'},
            'description': {'help_text': 'Optional detailed task description, instructions, or context.'},
            'status': {'help_text': 'Task status. Valid values are TODO, IN_PROGRESS, DONE, or CANCELLED.'},
            'priority': {'help_text': 'Task priority. Valid values are LOW, MEDIUM, or HIGH.'},
            'due_date': {'help_text': 'Optional due date and time in ISO 8601 date-time format.'},
            'assignee_user_id': {'help_text': 'Optional UUID of the user assigned to complete this task.'},
            'reporter_user_id': {'help_text': 'Optional UUID of the user who requested or reported this task.'},
            'checklist': {'help_text': 'Optional JSON checklist data for subtasks or completion steps.'},
            'attachments_count': {'help_text': 'Number of attachments associated with this task.'},
            'created_at': {'help_text': 'Timestamp when this task was created, in ISO 8601 date-time format. Read-only.'},
            'updated_at': {'help_text': 'Timestamp when this task was last updated, in ISO 8601 date-time format. Read-only.'},
            'completed_at': {'help_text': 'Timestamp when this task was completed, in ISO 8601 date-time format. Read-only.'},
        }


class TaskListSerializer(TenantMixin):
    """
    Serialize compact task records for task lists and dashboards.

    Agents use this schema when browsing many tasks without needing full
    checklist or description details.
    """
    lead_name = serializers.CharField(
        source='lead.name',
        read_only=True,
        help_text='Display name of the linked lead. Read-only.'
    )
    
    class Meta:
        model = Task
        fields = [
            'id', 'lead', 'lead_name', 'title', 'status', 'priority',
            'due_date', 'assignee_user_id', 'created_at', 'completed_at'
        ]
        read_only_fields = ['id', 'created_at', 'completed_at']
        extra_kwargs = {
            'id': {'help_text': 'Unique numeric identifier for this task. Read-only.'},
            'lead': {'help_text': 'Numeric ID of the lead this task is related to.'},
            'title': {'help_text': 'Short task title describing the work to be done.'},
            'status': {'help_text': 'Task status. Valid values are TODO, IN_PROGRESS, DONE, or CANCELLED.'},
            'priority': {'help_text': 'Task priority. Valid values are LOW, MEDIUM, or HIGH.'},
            'due_date': {'help_text': 'Optional due date and time in ISO 8601 date-time format.'},
            'assignee_user_id': {'help_text': 'Optional UUID of the user assigned to complete this task.'},
            'created_at': {'help_text': 'Timestamp when this task was created, in ISO 8601 date-time format. Read-only.'},
            'completed_at': {'help_text': 'Timestamp when this task was completed, in ISO 8601 date-time format. Read-only.'},
        }
