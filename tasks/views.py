from rest_framework import viewsets, filters
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from .models import Task
from .serializers import TaskSerializer, TaskListSerializer
from common.mixins import TenantViewSetMixin
from common.permissions import (
    CRMPermissionMixin, HasCRMPermission, JWTAuthentication
)
import logging

logger = logging.getLogger(__name__)


@extend_schema_view(
    list=extend_schema(description='List all tasks'),
    retrieve=extend_schema(description='Retrieve a specific task'),
    create=extend_schema(description='Create a new task'),
    update=extend_schema(description='Update a task'),
    partial_update=extend_schema(description='Partially update a task'),
    destroy=extend_schema(description='Delete a task'),
)
class TaskViewSet(CRMPermissionMixin, TenantViewSetMixin, viewsets.ModelViewSet):
    """
    ViewSet for managing Tasks
    Requires: crm.tasks permissions
    """
    queryset = Task.objects.select_related('lead')
    serializer_class = TaskSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [HasCRMPermission]
    permission_resource = 'tasks'
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

    def perform_create(self, serializer):
        """Auto-set owner_user_id to current user if not provided"""
        # Set owner_user_id to current user if not provided in request data
        owner_user_id = serializer.validated_data.get('owner_user_id')
        if not owner_user_id:
            owner_user_id = self.request.user_id
            logger.debug(f"Auto-setting owner_user_id to {owner_user_id}")

        # Call parent which sets tenant_id, and also pass owner_user_id
        super().perform_create(serializer)

        # Update owner_user_id if it wasn't set
        if not serializer.validated_data.get('owner_user_id'):
            serializer.instance.owner_user_id = owner_user_id
            serializer.instance.save()