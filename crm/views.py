from rest_framework import viewsets, filters, status
from rest_framework.decorators import action, renderer_classes
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.renderers import BaseRenderer, JSONRenderer
from django_filters.rest_framework import DjangoFilterBackend
import django_filters
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiParameter, OpenApiExample
from drf_spectacular.openapi import AutoSchema
from django.db.models import Count, Q, Sum
from django.utils import timezone
from django.http import HttpResponse, StreamingHttpResponse
from .models import (
    Lead, LeadStatus, LeadActivity, LeadOrder,
    LeadFieldConfiguration, LeadAttachment,
    LeadGroup, LeadGroupMembership
)
from .serializers import (
    LeadSerializer, LeadListSerializer, LeadStatusSerializer,
    LeadActivitySerializer, LeadOrderSerializer,
    LeadFieldConfigurationSerializer,
    BulkLeadDeleteSerializer, BulkLeadStatusUpdateSerializer,
    LeadAttachmentSerializer,
    LeadGroupSerializer, BulkLeadGroupMembershipSerializer
)
from .zata_client import upload_to_zata, delete_from_zata
from common.mixins import TenantViewSetMixin
from common.permissions import (
    CRMPermissionMixin, HasCRMPermission,
    JWTAuthentication, get_nested_permission, CRMPermissions,
    get_queryset_for_permission
)
import logging
import csv
import io
import json
import requests
from datetime import datetime
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from .user_directory import fetch_tenant_users
try:
    import openpyxl
    EXCEL_SUPPORT = True
except ImportError:
    EXCEL_SUPPORT = False

logger = logging.getLogger(__name__)


class TenantUserListView(APIView):
    """
    GET /api/crm/users/ -- list users for the current tenant.

    Thin proxy to the SuperAdmin (admin.celiyo.com) user directory. Users are
    not stored in the CRM; leads reference them by UUID via ``assigned_to``.
    Used by the MCP ``list_users`` tool so an agent can resolve a person's name
    to the UUID required when assigning leads.

    Query params:
        search    optional name/email filter
        page_size max users to return (default 100)
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        search = request.query_params.get('search')
        try:
            page_size = int(request.query_params.get('page_size', 100))
        except (TypeError, ValueError):
            page_size = 100
        try:
            data = fetch_tenant_users(search=search, page_size=page_size)
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else None
            logger.warning('User directory upstream error: %s', code)
            return Response(
                {'error': 'Failed to fetch users from auth service', 'status': code},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except requests.RequestException as exc:
            logger.error('User directory unreachable: %s', exc)
            return Response(
                {'error': f'Auth service unreachable: {exc}'},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(data)


class CSVRenderer(BaseRenderer):
    """Custom renderer for CSV export"""
    media_type = 'text/csv'
    format = 'csv'

    def render(self, data, accepted_media_type=None, renderer_context=None):
        """Render data as CSV"""
        if isinstance(data, bytes):
            return data
        if isinstance(data, str):
            return data.encode('utf-8')
        return data


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
    Manage CRM pipeline statuses used to organize leads.

    Use this endpoint when an agent needs to inspect or maintain the stages that
    appear in the lead pipeline or kanban board. Typical operations include
    listing active statuses in display order, creating a new stage such as
    "Contacted" or "Qualified", updating colors and ordering, and marking a
    status as won or lost.

    Each status belongs to the authenticated tenant. Agents should use lead
    status IDs from this endpoint when assigning a lead to a pipeline stage.

    Required permissions are based on crm.statuses actions.
    """
    queryset = LeadStatus.objects.all()
    serializer_class = LeadStatusSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [HasCRMPermission]
    permission_resource = 'statuses'
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['is_won', 'is_lost', 'is_active']
    search_fields = ['name']
    ordering_fields = ['order_index', 'name', 'created_at']
    ordering = ['order_index']

    def perform_create(self, serializer):
        """Auto-append order_index to the end of the board when omitted."""
        order_index = serializer.validated_data.get('order_index')
        if order_index is None:
            last = (
                LeadStatus.objects.filter(tenant_id=self.request.tenant_id)
                .order_by('-order_index')
                .values_list('order_index', flat=True)
                .first()
            )
            serializer.save(order_index=(last + 1) if last is not None else 0)
        else:
            serializer.save()


class LeadFilter(django_filters.FilterSet):
    """
    Custom FilterSet for Lead model.
    Supports both exact and multi-value (comma-separated) filtering
    for status and priority so the React UI can pass multiple selections.
    e.g. ?status__in=1,3,5  ?priority__in=HIGH,MEDIUM
    """
    status__in = django_filters.BaseInFilter(field_name='status', lookup_expr='in')
    priority__in = django_filters.BaseInFilter(field_name='priority', lookup_expr='in')

    class Meta:
        model = Lead
        fields = {
            'status': ['exact'],
            'priority': ['exact'],
            'lead_score': ['exact', 'gte', 'lte', 'isnull'],
            'owner_user_id': ['exact'],
            'assigned_to': ['exact', 'isnull'],
            'created_at': ['gte', 'lte', 'exact'],
            'updated_at': ['gte', 'lte'],
            'next_follow_up_at': ['gte', 'lte', 'isnull'],
            'city': ['exact', 'icontains'],
            'state': ['exact', 'icontains'],
            'country': ['exact', 'icontains'],
            'groups': ['exact'],
        }


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
    Manage CRM leads, contacts, source attribution, and lead qualification data.

    Use this endpoint when an agent needs to create a new lead, search existing
    leads, update contact details, change priority or status, record source
    attribution, or retrieve a full lead profile with its activity timeline.
    This is the primary endpoint for external lead ingestion from websites,
    Meta Lead Ads, Make.com workflows, manual CRM entry, and partner systems.

    For external systems, send source-specific details in the metadata object.
    The metadata.external_lead_id value is treated as an idempotency key during
    normal create requests, so retrying the same external lead returns the
    existing lead instead of creating a duplicate.

    Query parameters support filtering by status, priority, score, owner,
    assignee, created and updated dates, follow-up date, city, state, and
    country. The standard search parameter searches name, phone, email,
    company, and notes.

    Required permissions are based on crm.leads actions.
    """
    queryset = Lead.objects.select_related('status').prefetch_related('activities', 'groups')
    authentication_classes = [JWTAuthentication]
    permission_classes = [HasCRMPermission]
    permission_resource = 'leads'
    # append-note is an edit of the lead's notes, not a create.
    action_permission_map = {'append_note': 'edit'}
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = LeadFilter
    search_fields = ['name', 'phone', 'email', 'company', 'notes']
    ordering_fields = [
        'name', 'created_at', 'updated_at', 'priority', 'lead_score',
        'value_amount', 'next_follow_up_at', 'last_contacted_at'
    ]
    ordering = ['-created_at']

    def get_serializer_class(self):
        """Use lighter serializer for list view"""
        if self.action == 'list':
            return LeadListSerializer
        return LeadSerializer

    def create(self, request, *args, **kwargs):
        """Create a lead, treating metadata.external_lead_id as idempotency key."""
        metadata = request.data.get('metadata') or {}
        external_lead_id = metadata.get('external_lead_id') if isinstance(metadata, dict) else None

        if external_lead_id:
            external_lead_id = str(external_lead_id).strip()
            metadata['external_lead_id'] = external_lead_id
            existing_lead = Lead.objects.filter(
                tenant_id=request.tenant_id,
                metadata__external_lead_id=str(external_lead_id)
            ).first()

            if existing_lead:
                serializer = self.get_serializer(existing_lead)
                return Response(serializer.data, status=status.HTTP_200_OK)

        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        """Auto-set tenant_id and owner_user_id from the JWT when omitted."""
        tenant_id = getattr(self.request, 'tenant_id', None)
        owner_user_id = serializer.validated_data.get('owner_user_id') or getattr(self.request, 'user_id', None)

        if not tenant_id:
            raise ValidationError({'tenant_id': 'Tenant ID is required'})
        if not owner_user_id:
            raise ValidationError({'owner_user_id': 'Owner user ID is required'})

        serializer.save(tenant_id=tenant_id, owner_user_id=owner_user_id)

    @action(detail=True, methods=['post'], url_path='append-note')
    def append_note(self, request, pk=None):
        """Atomically append a timestamped block to Lead.notes (no clobber).

        Read-modify-write under a row lock so concurrent appends/edits are not
        lost. Tenant + object-level RBAC (crm.leads.edit) are enforced via
        get_object(). Keeps Lead.notes as the human page body.
        """
        from django.db import transaction

        text = (request.data or {}).get('text') if isinstance(request.data, dict) else None
        if not text or not str(text).strip():
            return Response({'error': 'text is required'}, status=status.HTTP_400_BAD_REQUEST)

        # Permission + tenant check on the target lead (raises 403/404 as needed).
        lead = self.get_object()

        stamp = timezone.now().strftime('%Y-%m-%d %H:%M')
        author = getattr(request, 'user_id', None)
        header = f"— {stamp}" + (f" · {author}" if author else "")
        block = f"{header}\n{str(text).strip()}"

        with transaction.atomic():
            # Re-fetch with a row lock to avoid lost updates (tenant already
            # validated by get_object above).
            locked = Lead.objects.select_for_update().get(pk=lead.pk)
            locked.notes = f"{locked.notes}\n\n{block}" if (locked.notes or '').strip() else block
            locked.save(update_fields=['notes', 'updated_at'])

        serializer = self.get_serializer(locked)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='sales-dashboard')
    def sales_dashboard(self, request):
        """Compact dashboard scoped by crm.leads.view permission."""
        if not self._has_crm_permission(request, CRMPermissions.CRM_LEADS_VIEW):
            raise PermissionDenied({'detail': 'Permission not granted for this module.'})

        leads = self.get_queryset()
        now = timezone.now()
        today = now.date()

        open_tasks = []
        upcoming_meetings = []
        recent_activities = []
        try:
            from tasks.models import Task
            from tasks.serializers import TaskListSerializer
            open_tasks_qs = Task.objects.filter(
                tenant_id=request.tenant_id,
                lead__in=leads,
            ).exclude(status__in=['DONE', 'CANCELLED']).order_by('due_date', '-created_at')[:5]
            open_tasks = TaskListSerializer(open_tasks_qs, many=True).data
        except Exception as exc:
            logger.warning("sales_dashboard task summary failed: %s", exc)

        try:
            from meetings.models import Meeting
            from meetings.serializers import MeetingListSerializer
            upcoming_meetings_qs = Meeting.objects.filter(
                tenant_id=request.tenant_id,
                lead__in=leads,
                start_at__gte=now,
            ).order_by('start_at')[:5]
            upcoming_meetings = MeetingListSerializer(upcoming_meetings_qs, many=True).data
        except Exception as exc:
            logger.warning("sales_dashboard meeting summary failed: %s", exc)

        try:
            recent_activities_qs = LeadActivity.objects.filter(
                tenant_id=request.tenant_id,
                lead__in=leads,
            ).select_related('lead').order_by('-happened_at')[:8]
            recent_activities = LeadActivitySerializer(recent_activities_qs, many=True).data
        except Exception as exc:
            logger.warning("sales_dashboard activity summary failed: %s", exc)

        status_breakdown = list(
            leads.values('status', 'status__name', 'status__color_hex')
            .annotate(count=Count('id'))
            .order_by('status__order_index', 'status__name')
        )
        priority_breakdown = list(
            leads.values('priority')
            .annotate(count=Count('id'))
            .order_by('priority')
        )

        recent_leads = LeadListSerializer(
            leads.order_by('-created_at')[:5],
            many=True,
        ).data

        return Response({
            'scope': get_nested_permission(getattr(request, 'permissions', {}), CRMPermissions.CRM_LEADS_VIEW) or 'all',
            'totals': {
                'leads': leads.count(),
                'high_priority': leads.filter(priority='HIGH').count(),
                'followups_due': leads.filter(next_follow_up_at__date__lte=today).count(),
                'estimated_value': leads.aggregate(total=Sum('value_amount')).get('total') or 0,
            },
            'status_breakdown': status_breakdown,
            'priority_breakdown': priority_breakdown,
            'recent_leads': recent_leads,
            'open_tasks': open_tasks,
            'upcoming_meetings': upcoming_meetings,
            'recent_activities': recent_activities,
        })

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
            if not self._has_crm_permission(request, CRMPermissions.CRM_LEADS_VIEW):
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

    @extend_schema(
        description='Export leads to CSV or JSON format',
        parameters=[
            OpenApiParameter(
                name='format',
                type=str,
                location=OpenApiParameter.QUERY,
                description='Export format: csv or json',
                enum=['csv', 'json'],
                default='csv'
            ),
        ],
        responses={200: {
            'type': 'string',
            'format': 'binary',
            'description': 'CSV file or JSON data containing leads'
        }}
    )
    @action(detail=False, methods=['get'], renderer_classes=[JSONRenderer, CSVRenderer])
    def export(self, request):
        """
        Export leads to CSV or JSON format
        Returns all leads that the user has permission to view
        Requires: crm.leads.view permission

        Query Parameters:
        - format: 'csv' or 'json' (default: 'csv')

        Accessible at: /api/crm/leads/export/
        """
        try:
            # Check permission for viewing leads
            if not self._has_crm_permission(request, CRMPermissions.CRM_LEADS_VIEW):
                raise PermissionDenied({
                    "error": "Permission denied",
                    "detail": "You don't have permission to export leads"
                })

            logger.info(f"Export leads requested by tenant: {request.tenant_id}")

            # Get export format (default to csv)
            export_format = request.query_params.get('format', 'csv').lower()

            logger.info(f"Export format requested: {export_format}")

            if export_format not in ['csv', 'json']:
                return Response(
                    {'error': 'Invalid format. Use "csv" or "json"'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Get leads with permission filtering
            leads = Lead.objects.filter(
                tenant_id=request.tenant_id
            ).select_related('status')

            # Filter leads based on permission scope
            view_permission = get_nested_permission(
                getattr(request, 'permissions', {}),
                'crm.leads.view'
            )

            if isinstance(view_permission, str):
                if view_permission == "own":
                    leads = leads.filter(owner_user_id=request.user_id)
                # "all" and "team" show all tenant leads

            leads = leads.order_by('-created_at')

            if export_format == 'json':
                # JSON export
                serializer = LeadSerializer(leads, many=True)
                return Response({
                    'count': leads.count(),
                    'exported_at': datetime.now().isoformat(),
                    'leads': serializer.data
                })

            else:
                # CSV export (with import-friendly headers)
                # Create CSV in memory
                output = io.StringIO()
                # Use QUOTE_NONNUMERIC to preserve phone numbers with + sign
                writer = csv.writer(output, quoting=csv.QUOTE_NONNUMERIC)

                # Write header (using import-friendly lowercase with underscores)
                # Only include fields that can be re-imported
                headers = [
                    'name', 'phone', 'email', 'company', 'title',
                    'priority', 'value_amount', 'value_currency',
                    'source', 'notes', 'address_line1', 'address_line2',
                    'city', 'state', 'country', 'postal_code'
                ]
                writer.writerow(headers)

                # Write data
                for lead in leads:
                    writer.writerow([
                        lead.name,
                        lead.phone,  # Properly quoted to preserve + sign
                        lead.email or '',
                        lead.company or '',
                        lead.title or '',
                        lead.priority,
                        lead.value_amount or '',
                        lead.value_currency or '',
                        lead.source or '',
                        lead.notes or '',
                        lead.address_line1 or '',
                        lead.address_line2 or '',
                        lead.city or '',
                        lead.state or '',
                        lead.country or '',
                        lead.postal_code or '',
                    ])

                logger.info(f"Exported {leads.count()} leads in CSV format")

                # Return as Response with CSV content
                csv_content = output.getvalue()
                output.close()

                response = HttpResponse(csv_content, content_type='text/csv')
                response['Content-Disposition'] = f'attachment; filename="leads_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv"'
                return response

        except PermissionDenied:
            raise
        except Exception as e:
            logger.error(f"Error in export view: {str(e)}")
            return Response(
                {'error': f'Failed to export leads: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @extend_schema(
        description='Import leads from CSV, Excel, or JSON format with duplicate phone number check',
        request={
            'multipart/form-data': {
                'type': 'object',
                'properties': {
                    'file': {
                        'type': 'string',
                        'format': 'binary',
                        'description': 'CSV or Excel (.xlsx) file to import'
                    }
                }
            },
            'application/json': {
                'type': 'object',
                'properties': {
                    'leads': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'name': {'type': 'string'},
                                'phone': {'type': 'string'},
                                'email': {'type': 'string'},
                                'company': {'type': 'string'},
                                'title': {'type': 'string'},
                                'priority': {'type': 'string', 'enum': ['LOW', 'MEDIUM', 'HIGH']},
                                'value_amount': {'type': 'number'},
                                'value_currency': {'type': 'string'},
                                'source': {'type': 'string'},
                                'notes': {'type': 'string'},
                                'city': {'type': 'string'},
                                'state': {'type': 'string'},
                                'country': {'type': 'string'},
                                'postal_code': {'type': 'string'},
                            },
                            'required': ['name', 'phone']
                        }
                    }
                },
                'required': ['leads']
            }
        },
        responses={200: {
            'type': 'object',
            'properties': {
                'success_count': {'type': 'integer', 'description': 'Number of leads successfully imported'},
                'failed_count': {'type': 'integer', 'description': 'Number of leads that failed to import'},
                'total_count': {'type': 'integer', 'description': 'Total number of leads processed'},
                'failures': {
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'row': {'type': 'integer'},
                            'phone': {'type': 'string'},
                            'name': {'type': 'string'},
                            'reason': {'type': 'string'}
                        }
                    }
                }
            }
        }},
        examples=[
            OpenApiExample(
                'JSON Import Example',
                value={
                    'leads': [
                        {
                            'name': 'John Doe',
                            'phone': '+1234567890',
                            'email': 'john@example.com',
                            'company': 'Acme Corp',
                            'priority': 'HIGH',
                            'value_amount': 5000.00,
                            'source': 'Website'
                        }
                    ]
                },
                request_only=True
            )
        ]
    )
    @action(detail=False, methods=['post'])
    def import_leads(self, request):
        """
        Import leads from CSV, Excel (.xlsx), or JSON data
        Validates duplicate phone numbers and returns success/failure counts
        Requires: crm.leads.create permission

        Supports three input formats:
        1. CSV file upload (multipart/form-data with 'file' field)
        2. Excel file upload (.xlsx) (multipart/form-data with 'file' field)
        3. JSON data with 'leads' array

        Required fields: name, phone

        Duplicate phone numbers (within same tenant) will be rejected

        Accessible at: /api/crm/leads/import_leads/
        """
        try:
            # Check permission for creating leads
            if not self._has_crm_permission(request, CRMPermissions.CRM_LEADS_CREATE):
                raise PermissionDenied({
                    "error": "Permission denied",
                    "detail": "You don't have permission to import leads"
                })

            logger.info(f"Import leads requested by tenant: {request.tenant_id}")

            leads_data = []

            # Check if file upload (CSV or Excel)
            if 'file' in request.FILES:
                uploaded_file = request.FILES['file']
                filename = uploaded_file.name.lower()

                # Check file type and process accordingly
                if filename.endswith('.csv'):
                    # Read CSV file
                    try:
                        decoded_file = uploaded_file.read().decode('utf-8')
                        io_string = io.StringIO(decoded_file)
                        reader = csv.DictReader(io_string)

                        for row in reader:
                            leads_data.append(row)

                    except Exception as e:
                        return Response(
                            {'error': f'Failed to parse CSV file: {str(e)}'},
                            status=status.HTTP_400_BAD_REQUEST
                        )

                elif filename.endswith('.xlsx') or filename.endswith('.xls'):
                    # Read Excel file
                    if not EXCEL_SUPPORT:
                        return Response(
                            {'error': 'Excel support not available. Please install openpyxl: pip install openpyxl'},
                            status=status.HTTP_400_BAD_REQUEST
                        )

                    try:
                        # Load workbook
                        workbook = openpyxl.load_workbook(uploaded_file, read_only=True)
                        sheet = workbook.active

                        # Get headers from first row
                        headers = []
                        for cell in sheet[1]:
                            headers.append(cell.value)

                        # Read data rows
                        for row in sheet.iter_rows(min_row=2, values_only=True):
                            row_dict = {}
                            for idx, value in enumerate(row):
                                if idx < len(headers) and headers[idx]:
                                    # Convert value to string if not None
                                    row_dict[headers[idx]] = str(value) if value is not None else ''

                            # Only add non-empty rows
                            if any(row_dict.values()):
                                leads_data.append(row_dict)

                        workbook.close()

                    except Exception as e:
                        return Response(
                            {'error': f'Failed to parse Excel file: {str(e)}'},
                            status=status.HTTP_400_BAD_REQUEST
                        )

                else:
                    return Response(
                        {'error': 'Invalid file type. Please upload a CSV (.csv) or Excel (.xlsx) file'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            # Check if JSON data
            elif 'leads' in request.data:
                leads_data = request.data.get('leads', [])

                if not isinstance(leads_data, list):
                    return Response(
                        {'error': 'Invalid data format. "leads" must be an array'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            else:
                return Response(
                    {'error': 'No data provided. Send either a CSV/Excel file or JSON data with "leads" array'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Process leads
            success_count = 0
            failed_count = 0
            failures = []

            # Get existing phone numbers in this tenant
            existing_phones = set(
                Lead.objects.filter(tenant_id=request.tenant_id)
                .values_list('phone', flat=True)
            )

            # Track phone numbers in this import batch to avoid duplicates within the batch
            batch_phones = set()
            batch_external_lead_ids = set()

            for idx, lead_data in enumerate(leads_data, start=1):
                try:
                    # Get phone and name (handle both lowercase and capitalized headers)
                    phone_raw = lead_data.get('phone', lead_data.get('Phone', ''))
                    name_raw = lead_data.get('name', lead_data.get('Name', ''))

                    # Clean phone and name - strip whitespace and tabs
                    phone = str(phone_raw).strip() if phone_raw else ''
                    name = str(name_raw).strip() if name_raw else ''

                    # Validate required fields
                    if not phone:
                        failures.append({
                            'row': idx,
                            'phone': phone,
                            'name': name,
                            'reason': 'Phone number is required'
                        })
                        failed_count += 1
                        continue

                    if not name:
                        failures.append({
                            'row': idx,
                            'phone': phone,
                            'name': name,
                            'reason': 'Name is required'
                        })
                        failed_count += 1
                        continue

                    # Check for duplicate phone number in database
                    if phone in existing_phones:
                        failures.append({
                            'row': idx,
                            'phone': phone,
                            'name': name,
                            'reason': 'Phone number already exists in database'
                        })
                        failed_count += 1
                        continue

                    # Check for duplicate phone number in current batch
                    if phone in batch_phones:
                        failures.append({
                            'row': idx,
                            'phone': phone,
                            'name': name,
                            'reason': 'Duplicate phone number in import file'
                        })
                        failed_count += 1
                        continue

                    # Prepare lead metadata and idempotency key
                    metadata = lead_data.get('metadata', lead_data.get('Metadata', None))
                    if isinstance(metadata, str):
                        metadata = metadata.strip()
                        if metadata:
                            try:
                                metadata = json.loads(metadata)
                            except json.JSONDecodeError:
                                metadata = {'raw_metadata': metadata}
                        else:
                            metadata = None
                    elif not isinstance(metadata, dict):
                        metadata = None

                    external_lead_id = None
                    if metadata:
                        external_lead_id = metadata.get('external_lead_id')
                        if external_lead_id:
                            external_lead_id = str(external_lead_id).strip()
                            metadata['external_lead_id'] = external_lead_id

                    if external_lead_id and Lead.objects.filter(
                        tenant_id=request.tenant_id,
                        metadata__external_lead_id=external_lead_id
                    ).exists():
                        failures.append({
                            'row': idx,
                            'phone': phone,
                            'name': name,
                            'reason': 'External lead ID already exists in database'
                        })
                        failed_count += 1
                        continue

                    if external_lead_id and external_lead_id in batch_external_lead_ids:
                        failures.append({
                            'row': idx,
                            'phone': phone,
                            'name': name,
                            'reason': 'Duplicate external lead ID in import file'
                        })
                        failed_count += 1
                        continue

                    # Prepare lead data
                    # Handle CSV field name variations (lowercase, capitalized, with spaces)
                    lead_dict = {
                        'name': name,
                        'phone': phone,
                        'email': lead_data.get('email', lead_data.get('Email', '')).strip() or None,
                        'company': lead_data.get('company', lead_data.get('Company', '')).strip() or None,
                        'title': lead_data.get('title', lead_data.get('Title', '')).strip() or None,
                        'priority': (lead_data.get('priority') or lead_data.get('Priority') or 'MEDIUM').strip() or 'MEDIUM',
                        'source': lead_data.get('source', lead_data.get('Source', '')).strip() or None,
                        'notes': lead_data.get('notes', lead_data.get('Notes', '')).strip() or None,
                        'address_line1': (lead_data.get('address_line1') or lead_data.get('Address Line 1') or lead_data.get('address line1') or '').strip() or None,
                        'address_line2': (lead_data.get('address_line2') or lead_data.get('Address Line 2') or lead_data.get('address line2') or '').strip() or None,
                        'city': lead_data.get('city', lead_data.get('City', '')).strip() or None,
                        'state': lead_data.get('state', lead_data.get('State', '')).strip() or None,
                        'country': lead_data.get('country', lead_data.get('Country', '')).strip() or None,
                        'postal_code': (lead_data.get('postal_code') or lead_data.get('Postal Code') or lead_data.get('postal code') or '').strip() or None,
                        'owner_user_id': request.user_id,
                        'tenant_id': request.tenant_id,
                        'metadata': metadata,
                    }

                    # Handle value_amount (supports: value_amount, Value Amount, value amount)
                    value_amount = (lead_data.get('value_amount') or
                                   lead_data.get('Value Amount') or
                                   lead_data.get('value amount') or
                                   lead_data.get('value_amount') or '')
                    if value_amount:
                        try:
                            value_amount_str = str(value_amount).strip()
                            if value_amount_str:
                                lead_dict['value_amount'] = float(value_amount_str)
                        except (ValueError, TypeError):
                            pass

                    # Handle value_currency (supports: value_currency, Value Currency, value currency)
                    value_currency = (lead_data.get('value_currency') or
                                     lead_data.get('Value Currency') or
                                     lead_data.get('value currency') or '')
                    if value_currency:
                        currency_str = str(value_currency).strip()
                        if currency_str:
                            lead_dict['value_currency'] = currency_str

                    # Create the lead
                    Lead.objects.create(**lead_dict)

                    # Add to batch phones set
                    batch_phones.add(phone)
                    existing_phones.add(phone)  # Also add to existing to prevent duplicates in same batch
                    if external_lead_id:
                        batch_external_lead_ids.add(external_lead_id)

                    success_count += 1

                except Exception as e:
                    logger.error(f"Error importing lead at row {idx}: {str(e)}")
                    failures.append({
                        'row': idx,
                        'phone': lead_data.get('phone', ''),
                        'name': lead_data.get('name', ''),
                        'reason': str(e)
                    })
                    failed_count += 1

            total_count = success_count + failed_count

            logger.info(f"Import completed: {success_count} success, {failed_count} failed out of {total_count}")

            return Response({
                'success_count': success_count,
                'failed_count': failed_count,
                'total_count': total_count,
                'failures': failures
            })

        except PermissionDenied:
            raise
        except Exception as e:
            logger.error(f"Error in import_leads view: {str(e)}")
            return Response(
                {'error': f'Failed to import leads: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @extend_schema(
        description='Bulk delete multiple leads',
        request=BulkLeadDeleteSerializer,
        responses={200: {
            'type': 'object',
            'properties': {
                'deleted_count': {'type': 'integer', 'description': 'Number of leads deleted'},
                'message': {'type': 'string'}
            }
        }}
    )
    @action(detail=False, methods=['post'], url_path='bulk-delete')
    def bulk_delete(self, request):
        """
        Bulk delete multiple leads by IDs.
        Requires: crm.leads.delete permission

        Request body:
        {
            "lead_ids": [1, 2, 3, ...]
        }

        Accessible at: POST /api/crm/leads/bulk-delete/
        """
        try:
            # Check permission for deleting leads
            if not self._has_crm_permission(request, CRMPermissions.CRM_LEADS_DELETE):
                raise PermissionDenied({
                    "error": "Permission denied",
                    "detail": "You don't have permission to delete leads"
                })

            # Validate request data
            serializer = BulkLeadDeleteSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            lead_ids = serializer.validated_data['lead_ids']

            logger.info(f"Bulk delete requested for {len(lead_ids)} leads by tenant: {request.tenant_id}")

            # Get leads that belong to this tenant
            leads_to_delete = Lead.objects.filter(
                tenant_id=request.tenant_id,
                id__in=lead_ids
            )

            # Check permission scope for "own" permission
            delete_permission = get_nested_permission(
                getattr(request, 'permissions', {}),
                'crm.leads.delete'
            )

            if isinstance(delete_permission, str) and delete_permission == "own":
                leads_to_delete = leads_to_delete.filter(owner_user_id=request.user_id)

            deleted_count = leads_to_delete.count()
            leads_to_delete.delete()

            logger.info(f"Bulk deleted {deleted_count} leads for tenant: {request.tenant_id}")

            return Response({
                'deleted_count': deleted_count,
                'message': f'Successfully deleted {deleted_count} leads'
            })

        except PermissionDenied:
            raise
        except Exception as e:
            logger.error(f"Error in bulk_delete view: {str(e)}")
            return Response(
                {'error': f'Failed to bulk delete leads: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @extend_schema(
        description='Bulk update status for multiple leads',
        request=BulkLeadStatusUpdateSerializer,
        responses={200: {
            'type': 'object',
            'properties': {
                'updated_count': {'type': 'integer', 'description': 'Number of leads updated'},
                'message': {'type': 'string'}
            }
        }}
    )
    @action(detail=False, methods=['post'], url_path='bulk-status-update')
    def bulk_status_update(self, request):
        """
        Bulk update status for multiple leads.
        Requires: crm.leads.edit permission

        Request body:
        {
            "lead_ids": [1, 2, 3, ...],
            "status_id": 5  // or null to clear status
        }

        Accessible at: POST /api/crm/leads/bulk-status-update/
        """
        try:
            # Check permission for updating leads
            if not self._has_crm_permission(request, CRMPermissions.CRM_LEADS_EDIT):
                raise PermissionDenied({
                    "error": "Permission denied",
                    "detail": "You don't have permission to update leads"
                })

            # Validate request data
            serializer = BulkLeadStatusUpdateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            lead_ids = serializer.validated_data['lead_ids']
            status_id = serializer.validated_data['status_id']

            logger.info(f"Bulk status update requested for {len(lead_ids)} leads by tenant: {request.tenant_id}")

            # Validate status exists (if not null)
            if status_id is not None:
                if not LeadStatus.objects.filter(
                    tenant_id=request.tenant_id,
                    id=status_id
                ).exists():
                    return Response(
                        {'error': f'Status with ID {status_id} not found'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            # Get leads that belong to this tenant
            leads_to_update = Lead.objects.filter(
                tenant_id=request.tenant_id,
                id__in=lead_ids
            )

            # Check permission scope for "own" permission
            update_permission = get_nested_permission(
                getattr(request, 'permissions', {}),
                CRMPermissions.CRM_LEADS_EDIT
            )

            if isinstance(update_permission, str) and update_permission == "own":
                leads_to_update = leads_to_update.filter(owner_user_id=request.user_id)

            updated_count = leads_to_update.update(status_id=status_id)

            logger.info(f"Bulk updated status for {updated_count} leads to status_id={status_id} for tenant: {request.tenant_id}")

            return Response({
                'updated_count': updated_count,
                'message': f'Successfully updated status for {updated_count} leads'
            })

        except PermissionDenied:
            raise
        except Exception as e:
            logger.error(f"Error in bulk_status_update view: {str(e)}")
            return Response(
                {'error': f'Failed to bulk update lead status: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


    @extend_schema(
        description='List or upload attachments for a specific lead',
        request={
            'multipart/form-data': {
                'type': 'object',
                'properties': {
                    'file': {'type': 'string', 'format': 'binary'}
                },
                'required': ['file']
            }
        },
        responses={
            200: LeadAttachmentSerializer(many=True),
            201: LeadAttachmentSerializer,
        },
        parameters=[
            OpenApiParameter('X-Zata-Bucket', str, OpenApiParameter.HEADER, description='Zata workspace bucket name'),
            OpenApiParameter('X-Zata-Folder-ID', str, OpenApiParameter.HEADER, description='Target Zata folder UUID'),
        ]
    )
    @action(detail=True, methods=['get', 'post'], url_path='attachments', url_name='attachments')
    def attachments(self, request, pk=None):
        """
        GET  /api/crm/leads/<pk>/attachments/ — list attachments for a lead
        POST /api/crm/leads/<pk>/attachments/ — upload a file to Zata and record the attachment
        """
        lead = self.get_object()

        if request.method == 'GET':
            if not self._has_crm_permission(request, CRMPermissions.CRM_LEADS_VIEW):
                raise PermissionDenied({'error': 'Permission denied', 'detail': "You don't have permission to view attachments"})

            qs = LeadAttachment.objects.filter(lead=lead, tenant_id=request.tenant_id)
            serializer = LeadAttachmentSerializer(qs, many=True)
            return Response(serializer.data)

        # POST — upload
        if not self._has_crm_permission(request, CRMPermissions.CRM_LEADS_CREATE):
            raise PermissionDenied({'error': 'Permission denied', 'detail': "You don't have permission to upload attachments"})

        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            return Response({'error': 'No file provided. Send the file under the "file" field.'}, status=status.HTTP_400_BAD_REQUEST)

        folder_id = request.META.get('HTTP_X_ZATA_FOLDER_ID')
        if not folder_id:
            return Response({'error': 'X-Zata-Folder-ID header is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            zata_result = upload_to_zata(uploaded_file, folder_id, uploaded_file.name)
        except Exception as e:
            logger.error(f"Zata upload failed for lead {lead.id}: {e}")
            return Response({'error': f'Zata upload failed: {str(e)}'}, status=status.HTTP_502_BAD_GATEWAY)

        attachment = LeadAttachment.objects.create(
            tenant_id=request.tenant_id,
            lead=lead,
            file_name=uploaded_file.name,
            file_size=uploaded_file.size,
            mime_type=uploaded_file.content_type or 'application/octet-stream',
            zata_video_id=zata_result.get('id'),
            download_url=zata_result.get('download_url') or '',
            uploaded_by=getattr(request, 'user_id', None),
        )

        serializer = LeadAttachmentSerializer(attachment)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        description='Delete a specific attachment from a lead',
        responses={204: None}
    )
    @action(
        detail=True,
        methods=['delete'],
        url_path=r'attachments/(?P<attachment_id>[^/.]+)',
        url_name='attachment-detail'
    )
    def delete_attachment(self, request, pk=None, attachment_id=None):
        """
        DELETE /api/crm/leads/<pk>/attachments/<attachment_id>/
        Removes the DB record and optionally deletes the file from Zata.
        """
        if not self._has_crm_permission(request, CRMPermissions.CRM_LEADS_DELETE):
            raise PermissionDenied({'error': 'Permission denied', 'detail': "You don't have permission to delete attachments"})

        lead = self.get_object()
        try:
            attachment = LeadAttachment.objects.get(id=attachment_id, lead=lead, tenant_id=request.tenant_id)
        except LeadAttachment.DoesNotExist:
            return Response({'error': 'Attachment not found.'}, status=status.HTTP_404_NOT_FOUND)

        if attachment.zata_video_id:
            try:
                delete_from_zata(str(attachment.zata_video_id))
            except Exception as e:
                logger.warning(f"Zata delete failed for attachment {attachment.id} (video {attachment.zata_video_id}): {e}")

        attachment.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


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
    Manage the activity timeline for leads.

    Use this endpoint when an agent needs to record or inspect interactions
    connected to a lead, such as calls, emails, meetings, notes, SMS messages,
    and other follow-up events. Activities help explain what has happened with
    a lead over time and who performed each interaction.

    Agents should create an activity after important communication or status
    changes when the user wants a timeline note. Activities are tenant-scoped
    and can be filtered by lead, type, user, and date range.

    Required permissions are based on crm.activities actions.
    """
    queryset = LeadActivity.objects.select_related('lead')
    serializer_class = LeadActivitySerializer
    authentication_classes = [JWTAuthentication]
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

    def perform_create(self, serializer):
        """Set required server-managed fields from the request.

        ``by_user_id`` is bound to the authenticated JWT user (read-only in the
        serializer, so it can't be spoofed) and ``happened_at`` defaults to now
        when the caller omits it. This lets both the frontend and the AI tool
        create activities without passing identity/time, while staying
        tenant/user-correct (tenant_id is set by the serializer's TenantMixin).
        """
        from django.utils import timezone
        extra = {'by_user_id': getattr(self.request, 'user_id', None)}
        if not serializer.validated_data.get('happened_at'):
            extra['happened_at'] = timezone.now()
        serializer.save(**extra)

    def get_queryset(self):
        """For own scope, show all activities belonging to the user's leads."""
        queryset = TenantViewSetMixin.get_queryset(self)

        if not hasattr(self, 'request') or not self.request:
            return queryset

        # Super admins bypass all permission checks
        if getattr(self.request, 'is_super_admin', False):
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
                return queryset.filter(lead__owner_user_id=self.request.user_id)

        return queryset



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
    Manage lead positions within kanban pipeline columns.

    Use this endpoint when an agent or UI needs to preserve the order of leads
    inside a pipeline status column. It links a lead to a status and stores a
    decimal position value that controls where the lead appears on the board.

    This endpoint is for board layout and ordering. To change the business
    status of a lead, update the lead itself or use the bulk status endpoint.

    Required permissions follow crm.leads because ordering changes affect how
    leads are managed in the pipeline.
    """
    queryset = LeadOrder.objects.select_related('lead', 'status')
    serializer_class = LeadOrderSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [HasCRMPermission]
    permission_resource = 'leads'  # Use leads permissions since this controls lead positioning
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['lead', 'status', 'board_id']
    ordering_fields = ['position', 'updated_at']
    ordering = ['status', 'position']


class LeadFieldConfigurationViewSet(CRMPermissionMixin, TenantViewSetMixin, viewsets.ModelViewSet):
    """
    Manage the lead field schema used by forms, imports, and CRM displays.

    Use this endpoint when an agent needs to understand which lead fields are
    available for a tenant, which fields are visible or required, and how custom
    fields should be represented in Lead.metadata. It covers both standard lead
    fields, such as name, phone, email, source, and notes, and custom fields
    configured by the tenant.

    Agents should call the field_schema action before building dynamic forms or
    mapping external data into tenant-specific custom fields. Standard fields
    map directly to Lead model fields. Custom fields are stored in the lead
    metadata JSON object using field_name as the metadata key.

    Required permissions are based on crm.settings actions.
    """
    queryset = LeadFieldConfiguration.objects.all()
    serializer_class = LeadFieldConfigurationSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [HasCRMPermission]
    permission_resource = 'settings'  # Requires admin settings permission
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['field_type', 'is_required', 'is_active', 'is_visible', 'is_standard']
    search_fields = ['field_name', 'field_label', 'help_text']
    ordering_fields = ['display_order', 'field_label', 'created_at']
    ordering = ['display_order', 'field_label']

    def list(self, request, *args, **kwargs):
        """
        List all field configurations for the tenant.
        Auto-populates default standard field configurations if none exist.
        """
        try:
            # Import here to avoid circular imports
            from .utils import ensure_default_field_configurations

            # Ensure default field configurations exist for this tenant
            created_count, existing_count = ensure_default_field_configurations(request.tenant_id)

            if created_count > 0:
                logger.info(
                    f"Auto-created {created_count} default field configurations for tenant {request.tenant_id}"
                )

            # Continue with normal list behavior
            return super().list(request, *args, **kwargs)

        except Exception as e:
            logger.error(f"Error in field configuration list: {str(e)}")
            # Fall back to normal list behavior even if auto-population fails
            return super().list(request, *args, **kwargs)

    @extend_schema(
        description='Get field schema organized by standard and custom fields',
        responses={200: {
            'type': 'object',
            'properties': {
                'standard_fields': {
                    'type': 'array',
                    'items': {'$ref': '#/components/schemas/LeadFieldConfiguration'}
                },
                'custom_fields': {
                    'type': 'array',
                    'items': {'$ref': '#/components/schemas/LeadFieldConfiguration'}
                }
            }
        }}
    )
    @action(detail=False, methods=['get'])
    def field_schema(self, request):
        """
        Get field schema organized by standard and custom fields.
        Returns a structured response with both standard and custom fields separated.
        Auto-populates default standard field configurations if none exist.
        Requires: crm.settings.view permission

        Accessible at: /api/crm/field-configurations/field_schema/
        """
        try:
            # Check permission
            if not self._has_crm_permission(request, CRMPermissions.CRM_SETTINGS_VIEW):
                raise PermissionDenied({
                    "error": "Permission denied",
                    "detail": "You don't have permission to view field configurations"
                })

            logger.info(f"Field schema requested by tenant: {request.tenant_id}")

            # Import here to avoid circular imports
            from .utils import ensure_default_field_configurations

            # Ensure default field configurations exist for this tenant
            created_count, existing_count = ensure_default_field_configurations(request.tenant_id)

            if created_count > 0:
                logger.info(
                    f"Auto-created {created_count} default field configurations for tenant {request.tenant_id}"
                )

            # Get all field configurations for the tenant
            all_fields = LeadFieldConfiguration.objects.filter(
                tenant_id=request.tenant_id,
                is_active=True
            ).order_by('display_order', 'field_label')

            # Separate standard and custom fields
            standard_fields = all_fields.filter(is_standard=True)
            custom_fields = all_fields.filter(is_standard=False)

            # Serialize
            standard_serializer = LeadFieldConfigurationSerializer(standard_fields, many=True)
            custom_serializer = LeadFieldConfigurationSerializer(custom_fields, many=True)

            return Response({
                'standard_fields': standard_serializer.data,
                'custom_fields': custom_serializer.data
            })

        except PermissionDenied:
            raise
        except Exception as e:
            logger.error(f"Error in field_schema view: {str(e)}")
            return Response(
                {'error': 'Failed to fetch field schema'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


@extend_schema_view(
    list=extend_schema(description='List all lead groups for the tenant'),
    retrieve=extend_schema(description='Retrieve a specific lead group'),
    create=extend_schema(description='Create a new lead group'),
    update=extend_schema(description='Update a lead group'),
    partial_update=extend_schema(description='Partially update a lead group'),
    destroy=extend_schema(description='Delete a lead group'),
)
class LeadGroupViewSet(CRMPermissionMixin, TenantViewSetMixin, viewsets.ModelViewSet):
    """
    Manage CRM lead groups (lists).

    Lead groups let you organise leads into named collections such as VIP Clients,
    Cold Leads, or Follow-Up Queue. Each group belongs to the authenticated tenant.

    Use the add_leads and remove_leads actions to manage group membership in bulk.
    Use the group_leads action to list all leads that belong to a specific group.

    Required permissions are based on crm.leads actions.
    """
    serializer_class = LeadGroupSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [HasCRMPermission]
    permission_resource = 'leads'
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'description']
    ordering_fields = ['name', 'created_at', 'updated_at']
    ordering = ['name']

    def get_queryset(self):
        return LeadGroup.objects.filter(
            tenant_id=self.request.tenant_id
        ).annotate(lead_count=Count('memberships'))

    def perform_create(self, serializer):
        serializer.save(
            tenant_id=self.request.tenant_id,
            created_by=self.request.user_id
        )

    @extend_schema(
        description='Add leads to this group in bulk',
        request=BulkLeadGroupMembershipSerializer,
        responses={200: {'type': 'object', 'properties': {
            'added': {'type': 'integer'},
            'already_in_group': {'type': 'integer'},
            'not_found': {'type': 'integer'},
        }}}
    )
    @action(detail=True, methods=['post'], url_path='add-leads')
    def add_leads(self, request, pk=None):
        """Bulk-add leads to this group. Silently skips leads already in the group."""
        group = self.get_object()
        serializer = BulkLeadGroupMembershipSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        lead_ids = serializer.validated_data['lead_ids']
        leads = Lead.objects.filter(id__in=lead_ids, tenant_id=request.tenant_id)
        # Enforce the user's crm.leads.view scope: own/team-scoped users may only
        # add leads they are allowed to see.
        leads = get_queryset_for_permission(leads, request, CRMPermissions.CRM_LEADS_VIEW)
        found_ids = set(leads.values_list('id', flat=True))
        not_found = len(lead_ids) - len(found_ids)

        added = 0
        already_in = 0
        for lead in leads:
            membership, created = LeadGroupMembership.objects.get_or_create(
                group=group,
                lead=lead,
                defaults={'added_by': request.user_id}
            )
            if created:
                added += 1
            else:
                already_in += 1

        return Response({
            'added': added,
            'already_in_group': already_in,
            'not_found': not_found,
        })

    @extend_schema(
        description='Remove leads from this group in bulk',
        request=BulkLeadGroupMembershipSerializer,
        responses={200: {'type': 'object', 'properties': {
            'removed': {'type': 'integer'},
        }}}
    )
    @action(detail=True, methods=['post'], url_path='remove-leads')
    def remove_leads(self, request, pk=None):
        """Bulk-remove leads from this group."""
        group = self.get_object()
        serializer = BulkLeadGroupMembershipSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        lead_ids = serializer.validated_data['lead_ids']
        # Enforce the user's crm.leads.view scope before removing memberships.
        accessible_leads = get_queryset_for_permission(
            Lead.objects.filter(id__in=lead_ids, tenant_id=request.tenant_id),
            request,
            CRMPermissions.CRM_LEADS_VIEW,
        )
        accessible_lead_ids = set(accessible_leads.values_list('id', flat=True))
        deleted_count, _ = LeadGroupMembership.objects.filter(
            group=group,
            lead_id__in=accessible_lead_ids
        ).delete()

        return Response({'removed': deleted_count})

    @extend_schema(
        description='List all leads belonging to this group',
        responses={200: LeadListSerializer(many=True)}
    )
    @action(detail=True, methods=['get'], url_path='leads')
    def group_leads(self, request, pk=None):
        """Return paginated list of leads in this group."""
        group = self.get_object()
        leads_qs = Lead.objects.filter(
            tenant_id=request.tenant_id,
            groups=group
        ).select_related('status').prefetch_related('groups')

        view_permission = get_nested_permission(
            getattr(request, 'permissions', {}),
            'crm.leads.view',
        )
        if view_permission == 'own':
            leads_qs = leads_qs.filter(owner_user_id=request.user_id)

        page = self.paginate_queryset(leads_qs)
        if page is not None:
            serializer = LeadListSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = LeadListSerializer(leads_qs, many=True)
        return Response(serializer.data)
