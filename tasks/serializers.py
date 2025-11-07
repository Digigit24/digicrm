from rest_framework import serializers
from .models import Task


class TaskSerializer(serializers.ModelSerializer):
    """Serializer for Task model"""
    lead_name = serializers.CharField(source='lead.name', read_only=True)
    
    class Meta:
        model = Task
        fields = [
            'id', 'lead', 'lead_name', 'title', 'description', 'status',
            'priority', 'due_date', 'assignee_user_id', 'reporter_user_id',
            'checklist', 'attachments_count', 'created_at', 'updated_at',
            'completed_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'completed_at']


class TaskListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing tasks"""
    lead_name = serializers.CharField(source='lead.name', read_only=True)
    
    class Meta:
        model = Task
        fields = [
            'id', 'lead', 'lead_name', 'title', 'status', 'priority',
            'due_date', 'assignee_user_id', 'created_at', 'completed_at'
        ]
        read_only_fields = ['id', 'created_at', 'completed_at']