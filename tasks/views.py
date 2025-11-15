from rest_framework import viewsets, filters
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from .models import Task
from .serializers import TaskSerializer, TaskListSerializer
from common.mixins import TenantViewSetMixin


@extend_schema_view(
    list=extend_schema(description='List all tasks'),
    retrieve=extend_schema(description='Retrieve a specific task'),
    create=extend_schema(description='Create a new task'),
    update=extend_schema(description='Update a task'),
    partial_update=extend_schema(description='Partially update a task'),
    destroy=extend_schema(description='Delete a task'),
)
class TaskViewSet(TenantViewSetMixin, viewsets.ModelViewSet):
    """
    ViewSet for managing Tasks
    """
    queryset = Task.objects.select_related('lead')
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = {
        'lead': ['exact'],
        'status': ['exact'],
        'priority': ['exact'],
        'assignee_user_id': ['exact'],
        'reporter_user_id': ['exact'],
        'due_date': ['gte', 'lte', 'exact', 'isnull'],
        'completed_at': ['gte', 'lte', 'isnull'],
        'created_at': ['gte', 'lte'],
    }
    search_fields = ['title', 'description']
    ordering_fields = [
        'due_date', 'created_at', 'updated_at', 'completed_at',
        'priority', 'status'
    ]
    ordering = ['-created_at']

    def get_serializer_class(self):
        """Use lighter serializer for list view"""
        if self.action == 'list':
            return TaskListSerializer
        return TaskSerializer