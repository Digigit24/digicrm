from rest_framework import viewsets, filters
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from .models import Lead, LeadStatus, LeadActivity, LeadOrder
from .serializers import (
    LeadSerializer, LeadListSerializer, LeadStatusSerializer,
    LeadActivitySerializer, LeadOrderSerializer
)
from common.mixins import TenantViewSetMixin


@extend_schema_view(
    list=extend_schema(description='List all lead statuses'),
    retrieve=extend_schema(description='Retrieve a specific lead status'),
    create=extend_schema(description='Create a new lead status'),
    update=extend_schema(description='Update a lead status'),
    partial_update=extend_schema(description='Partially update a lead status'),
    destroy=extend_schema(description='Delete a lead status'),
)
class LeadStatusViewSet(TenantViewSetMixin, viewsets.ModelViewSet):
    """
    ViewSet for managing Lead Statuses
    """
    queryset = LeadStatus.objects.all()
    serializer_class = LeadStatusSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['is_won', 'is_lost', 'is_active']
    search_fields = ['name']
    ordering_fields = ['order_index', 'name', 'created_at']
    ordering = ['order_index']


@extend_schema_view(
    list=extend_schema(description='List all leads'),
    retrieve=extend_schema(description='Retrieve a specific lead'),
    create=extend_schema(description='Create a new lead'),
    update=extend_schema(description='Update a lead'),
    partial_update=extend_schema(description='Partially update a lead'),
    destroy=extend_schema(description='Delete a lead'),
)
class LeadViewSet(TenantViewSetMixin, viewsets.ModelViewSet):
    """
    ViewSet for managing Leads with comprehensive filtering
    """
    queryset = Lead.objects.select_related('status').prefetch_related('activities')
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = {
        'status': ['exact'],
        'priority': ['exact'],
        'owner_user_id': ['exact'],
        'created_at': ['gte', 'lte', 'exact'],
        'updated_at': ['gte', 'lte'],
        'next_follow_up_at': ['gte', 'lte', 'isnull'],
        'city': ['exact', 'icontains'],
        'state': ['exact', 'icontains'],
        'country': ['exact', 'icontains'],
    }
    search_fields = ['name', 'phone', 'email', 'company', 'notes']
    ordering_fields = [
        'name', 'created_at', 'updated_at', 'priority',
        'value_amount', 'next_follow_up_at', 'last_contacted_at'
    ]
    ordering = ['-created_at']

    def get_serializer_class(self):
        """Use lighter serializer for list view"""
        if self.action == 'list':
            return LeadListSerializer
        return LeadSerializer


@extend_schema_view(
    list=extend_schema(description='List all lead activities'),
    retrieve=extend_schema(description='Retrieve a specific lead activity'),
    create=extend_schema(description='Create a new lead activity'),
    update=extend_schema(description='Update a lead activity'),
    partial_update=extend_schema(description='Partially update a lead activity'),
    destroy=extend_schema(description='Delete a lead activity'),
)
class LeadActivityViewSet(TenantViewSetMixin, viewsets.ModelViewSet):
    """
    ViewSet for managing Lead Activities
    """
    queryset = LeadActivity.objects.select_related('lead')
    serializer_class = LeadActivitySerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = {
        'lead': ['exact'],
        'type': ['exact'],
        'by_user_id': ['exact'],
        'happened_at': ['gte', 'lte', 'exact'],
    }
    search_fields = ['content']
    ordering_fields = ['happened_at', 'created_at']
    ordering = ['-happened_at']


@extend_schema_view(
    list=extend_schema(description='List all lead orders'),
    retrieve=extend_schema(description='Retrieve a specific lead order'),
    create=extend_schema(description='Create a new lead order'),
    update=extend_schema(description='Update a lead order'),
    partial_update=extend_schema(description='Partially update a lead order'),
    destroy=extend_schema(description='Delete a lead order'),
)
class LeadOrderViewSet(TenantViewSetMixin, viewsets.ModelViewSet):
    """
    ViewSet for managing Lead Orders (Kanban board positioning)
    """
    queryset = LeadOrder.objects.select_related('lead', 'status')
    serializer_class = LeadOrderSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['lead', 'status', 'board_id']
    ordering_fields = ['position', 'updated_at']
    ordering = ['status', 'position']