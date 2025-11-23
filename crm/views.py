from rest_framework import viewsets, filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from django.db.models import Count, Q
from .models import (
    Lead, LeadStatus, LeadActivity, LeadOrder,
    LeadCustomField, LeadFieldVisibility
)
from .serializers import (
    LeadSerializer, LeadListSerializer, LeadStatusSerializer,
    LeadActivitySerializer, LeadOrderSerializer,
    LeadCustomFieldSerializer, LeadFieldVisibilitySerializer
)
from common.mixins import TenantViewSetMixin
from common.permissions import CRMPermissionMixin, HasCRMPermission, get_nested_permission
import logging

logger = logging.getLogger(__name__)


@extend_schema_view(
    list=extend_schema(description='List all lead statuses'),
    retrieve=extend_schema(description='Retrieve a specific lead status'),
    create=extend_schema(description='Create a new lead status'),
    update=extend_schema(description='Update a lead status'),
    partial_update=extend_schema(description='Partially update a lead status'),
    destroy=extend_schema(description='Delete a lead status'),
)
class LeadStatusViewSet(CRMPermissionMixin, TenantViewSetMixin, viewsets.ModelViewSet):
    """
    ViewSet for managing Lead Statuses
    Requires: crm.statuses permissions
    """
    queryset = LeadStatus.objects.all()
    serializer_class = LeadStatusSerializer
    permission_classes = [HasCRMPermission]
    permission_resource = 'statuses'
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
class LeadViewSet(CRMPermissionMixin, TenantViewSetMixin, viewsets.ModelViewSet):
    """
    ViewSet for managing Leads with comprehensive filtering
    Requires: crm.leads permissions
    """
    queryset = Lead.objects.select_related('status').prefetch_related('activities')
    permission_classes = [HasCRMPermission]
    permission_resource = 'leads'
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = {
        'status': ['exact'],
        'priority': ['exact'],
        'owner_user_id': ['exact'],
        'assigned_to': ['exact', 'isnull'],
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

    @extend_schema(
        description='Get leads organized by status for kanban board view',
        responses={200: {
            'type': 'object',
            'properties': {
                'statuses': {
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'id': {'type': 'integer'},
                            'name': {'type': 'string'},
                            'color_hex': {'type': 'string'},
                            'order_index': {'type': 'integer'},
                            'is_won': {'type': 'boolean'},
                            'is_lost': {'type': 'boolean'},
                            'lead_count': {'type': 'integer'},
                            'leads': {
                                'type': 'array',
                                'items': {'$ref': '#/components/schemas/LeadList'}
                            }
                        }
                    }
                }
            }
        }}
    )
    @action(detail=False, methods=['get'])
    def kanban(self, request):
        """
        Get leads organized by status for kanban board view
        Returns all statuses with their associated leads
        Requires: crm.leads.view permission
        """
        try:
            # Check permission for viewing leads
            if not self._has_crm_permission(request, 'crm.leads.view'):
                raise PermissionDenied({
                    "error": "Permission denied",
                    "detail": "You don't have permission to view leads"
                })

            logger.info(f"Kanban view requested by tenant: {request.tenant_id}")

            # Get all active statuses for the tenant, ordered by order_index
            statuses = LeadStatus.objects.filter(
                tenant_id=request.tenant_id,
                is_active=True
            ).order_by('order_index')

            kanban_data = []

            # Get view permission to filter leads accordingly
            view_permission = get_nested_permission(
                getattr(request, 'permissions', {}),
                'crm.leads.view'
            )

            for status in statuses:
                # Get leads for this status
                leads = Lead.objects.filter(
                    tenant_id=request.tenant_id,
                    status=status
                ).select_related('status')

                # Filter leads based on permission scope
                if isinstance(view_permission, str):
                    if view_permission == "own":
                        leads = leads.filter(owner_user_id=request.user_id)
                    # "all" and "team" show all tenant leads

                leads = leads.order_by('-created_at')

                # Serialize the leads
                leads_serializer = LeadListSerializer(leads, many=True)

                # Build status data
                status_data = {
                    'id': status.id,
                    'name': status.name,
                    'color_hex': status.color_hex,
                    'order_index': status.order_index,
                    'is_won': status.is_won,
                    'is_lost': status.is_lost,
                    'lead_count': leads.count(),
                    'leads': leads_serializer.data
                }

                kanban_data.append(status_data)

            logger.info(f"Kanban data prepared for {len(kanban_data)} statuses")

            return Response({
                'statuses': kanban_data
            })

        except PermissionDenied:
            raise
        except Exception as e:
            logger.error(f"Error in kanban view: {str(e)}")
            return Response(
                {'error': 'Failed to fetch kanban data'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


@extend_schema_view(
    list=extend_schema(description='List all lead activities'),
    retrieve=extend_schema(description='Retrieve a specific lead activity'),
    create=extend_schema(description='Create a new lead activity'),
    update=extend_schema(description='Update a lead activity'),
    partial_update=extend_schema(description='Partially update a lead activity'),
    destroy=extend_schema(description='Delete a lead activity'),
)
class LeadActivityViewSet(CRMPermissionMixin, TenantViewSetMixin, viewsets.ModelViewSet):
    """
    ViewSet for managing Lead Activities
    Requires: crm.activities permissions
    """
    queryset = LeadActivity.objects.select_related('lead')
    serializer_class = LeadActivitySerializer
    permission_classes = [HasCRMPermission]
    permission_resource = 'activities'
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

    def get_queryset(self):
        """Override to filter by by_user_id instead of owner_user_id"""
        queryset = TenantViewSetMixin.get_queryset(self)

        if not hasattr(self, 'request') or not self.request:
            return queryset

        # Get view permission
        view_permission_key = f"crm.{self.permission_resource}.view"
        permission_value = get_nested_permission(
            getattr(self.request, 'permissions', {}),
            view_permission_key
        )

        # If no permission found, return empty queryset
        if permission_value is None:
            return queryset.none()

        # Handle boolean permissions
        if isinstance(permission_value, bool):
            return queryset if permission_value else queryset.none()

        # Handle scope-based permissions
        if isinstance(permission_value, str):
            if permission_value == "all":
                return queryset
            elif permission_value == "team":
                return queryset
            elif permission_value == "own":
                # Filter by creator (by_user_id) instead of owner_user_id
                return queryset.filter(by_user_id=self.request.user_id)

        return queryset

    def check_object_permissions(self, request, obj):
        """Override to check activity permissions based on by_user_id instead of owner_user_id"""
        from rest_framework.exceptions import PermissionDenied

        # Call parent's check_permissions (skipping CRMPermissionMixin's check_object_permissions)
        viewsets.ModelViewSet.check_object_permissions(self, request, obj)

        # Activities use by_user_id instead of owner_user_id
        # DRF permission class handles this via has_object_permission
        pass


@extend_schema_view(
    list=extend_schema(description='List all lead orders'),
    retrieve=extend_schema(description='Retrieve a specific lead order'),
    create=extend_schema(description='Create a new lead order'),
    update=extend_schema(description='Update a lead order'),
    partial_update=extend_schema(description='Partially update a lead order'),
    destroy=extend_schema(description='Delete a lead order'),
)
class LeadOrderViewSet(CRMPermissionMixin, TenantViewSetMixin, viewsets.ModelViewSet):
    """
    ViewSet for managing Lead Orders (Kanban board positioning)
    Requires: crm.leads permissions (same as leads since orders control lead positioning)
    """
    queryset = LeadOrder.objects.select_related('lead', 'status')
    serializer_class = LeadOrderSerializer
    permission_classes = [HasCRMPermission]
    permission_resource = 'leads'  # Use leads permissions since this controls lead positioning
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['lead', 'status', 'board_id']
    ordering_fields = ['position', 'updated_at']
    ordering = ['status', 'position']


@extend_schema_view(
    list=extend_schema(description='List all custom fields for leads'),
    retrieve=extend_schema(description='Retrieve a specific custom field'),
    create=extend_schema(description='Create a new custom field'),
    update=extend_schema(description='Update a custom field'),
    partial_update=extend_schema(description='Partially update a custom field'),
    destroy=extend_schema(description='Delete a custom field'),
)
class LeadCustomFieldViewSet(CRMPermissionMixin, TenantViewSetMixin, viewsets.ModelViewSet):
    """
    ViewSet for managing Lead Custom Fields
    Requires: crm.settings permissions (admin-level)
    """
    queryset = LeadCustomField.objects.all()
    serializer_class = LeadCustomFieldSerializer
    permission_classes = [HasCRMPermission]
    permission_resource = 'settings'  # Requires admin settings permission
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['field_type', 'is_required', 'is_active']
    search_fields = ['field_name', 'field_label', 'help_text']
    ordering_fields = ['display_order', 'field_label', 'created_at']
    ordering = ['display_order', 'field_label']


@extend_schema_view(
    list=extend_schema(description='List field visibility settings'),
    retrieve=extend_schema(description='Retrieve a specific field visibility setting'),
    create=extend_schema(description='Create a new field visibility setting'),
    update=extend_schema(description='Update a field visibility setting'),
    partial_update=extend_schema(description='Partially update a field visibility setting'),
    destroy=extend_schema(description='Delete a field visibility setting'),
)
class LeadFieldVisibilityViewSet(CRMPermissionMixin, TenantViewSetMixin, viewsets.ModelViewSet):
    """
    ViewSet for managing Lead Field Visibility
    Requires: crm.settings permissions (admin-level)
    """
    queryset = LeadFieldVisibility.objects.all()
    serializer_class = LeadFieldVisibilitySerializer
    permission_classes = [HasCRMPermission]
    permission_resource = 'settings'  # Requires admin settings permission
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['is_visible']
    search_fields = ['field_name']
    ordering_fields = ['display_order', 'field_name']
    ordering = ['display_order', 'field_name']