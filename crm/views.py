from rest_framework import viewsets, filters, status
from rest_framework.decorators import action, renderer_classes
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied
from rest_framework.renderers import BaseRenderer, JSONRenderer
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiParameter, OpenApiExample
from drf_spectacular.openapi import AutoSchema
from django.db.models import Count, Q
from django.http import HttpResponse, StreamingHttpResponse
from .models import (
    Lead, LeadStatus, LeadActivity, LeadOrder,
    LeadFieldConfiguration
)
from .serializers import (
    LeadSerializer, LeadListSerializer, LeadStatusSerializer,
    LeadActivitySerializer, LeadOrderSerializer,
    LeadFieldConfigurationSerializer,
    BulkLeadDeleteSerializer, BulkLeadStatusUpdateSerializer
)
from common.mixins import TenantViewSetMixin
from common.permissions import (
    CRMPermissionMixin, HasCRMPermission,
    JWTAuthentication, get_nested_permission
)
import logging
import csv
import io
from datetime import datetime
try:
    import openpyxl
    EXCEL_SUPPORT = True
except ImportError:
    EXCEL_SUPPORT = False

logger = logging.getLogger(__name__)


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
    ViewSet for managing Lead Statuses
    Requires: crm.statuses permissions
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
    authentication_classes = [JWTAuthentication]
    permission_classes = [HasCRMPermission]
    permission_resource = 'leads'
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = {
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
    }
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

    @action(detail=False, methods=['get'])
    def debug_count(self, request):
        """
        Temporary diagnostic endpoint to debug empty leads issue.
        GET /api/crm/leads/debug_count/
        """
        from django.db import connection as db_connection
        tenant_id = getattr(request, 'tenant_id', None)
        total_leads = Lead.objects.count()
        tenant_leads = Lead.objects.filter(tenant_id=tenant_id).count() if tenant_id else 0

        # Get distinct tenant_ids in the leads table
        distinct_tenants = list(
            Lead.objects.values_list('tenant_id', flat=True).distinct()[:10]
        )

        # Get the 3 most recent leads (any tenant)
        recent_leads = list(
            Lead.objects.order_by('-created_at').values(
                'id', 'name', 'tenant_id', 'created_at', 'source'
            )[:3]
        )
        # Serialize for JSON
        for lead in recent_leads:
            lead['tenant_id'] = str(lead['tenant_id'])
            lead['created_at'] = lead['created_at'].isoformat() if lead['created_at'] else None

        return Response({
            'request_tenant_id': str(tenant_id) if tenant_id else None,
            'request_tenant_id_type': type(tenant_id).__name__,
            'total_leads_all_tenants': total_leads,
            'leads_for_request_tenant': tenant_leads,
            'distinct_tenant_ids_in_db': [str(t) for t in distinct_tenants],
            'recent_leads': recent_leads,
            'db_engine': db_connection.vendor,
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
            if not self._has_crm_permission(request, 'crm.leads.view'):
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
            if not self._has_crm_permission(request, 'crm.leads.create'):
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

            for idx, lead_data in enumerate(leads_data, start=1):
                try:
                    # Get phone and name (handle both lowercase and capitalized headers)
                    phone_raw = lead_data.get('phone', lead_data.get('Phone', ''))
                    name_raw = lead_data.get('name', lead_data.get('Name', ''))

                    # Clean phone and name - strip whitespace and tabs
                    phone = phone_raw.strip() if phone_raw else ''
                    name = name_raw.strip() if name_raw else ''

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
            if not self._has_crm_permission(request, 'crm.leads.delete'):
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
        Requires: crm.leads.update permission

        Request body:
        {
            "lead_ids": [1, 2, 3, ...],
            "status_id": 5  // or null to clear status
        }

        Accessible at: POST /api/crm/leads/bulk-status-update/
        """
        try:
            # Check permission for updating leads
            if not self._has_crm_permission(request, 'crm.leads.update'):
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
                'crm.leads.update'
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

    def get_queryset(self):
        """Override to filter by by_user_id instead of owner_user_id"""
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
    authentication_classes = [JWTAuthentication]
    permission_classes = [HasCRMPermission]
    permission_resource = 'leads'  # Use leads permissions since this controls lead positioning
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['lead', 'status', 'board_id']
    ordering_fields = ['position', 'updated_at']
    ordering = ['status', 'position']


class LeadFieldConfigurationViewSet(CRMPermissionMixin, TenantViewSetMixin, viewsets.ModelViewSet):
    """
    ViewSet for managing Lead Field Configurations.
    Handles both standard Lead model fields and custom fields in a unified API.

    Standard fields: Pre-defined Lead model fields (visibility, display order)
    Custom fields: Dynamic fields stored in Lead.metadata JSON

    Requires: crm.settings permissions (admin-level)

    Operations:
    - list: List all lead field configurations
    - retrieve: Retrieve a specific lead field configuration
    - create: Create a new lead field configuration
    - update: Update a lead field configuration
    - partial_update: Partially update a lead field configuration
    - destroy: Delete a lead field configuration
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
            if not self._has_crm_permission(request, 'crm.settings.view'):
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


def debug_lead_count(request):
    """
    Temporary standalone debug endpoint (no auth required).
    Diagnoses why leads API returns empty results.
    GET /api/crm/debug/
    REMOVE after diagnosis is complete.
    """
    from django.http import JsonResponse
    from django.db import connection as db_connection

    try:
        total_leads = Lead.objects.count()

        # Get distinct tenant_ids in the leads table
        distinct_tenants = list(
            Lead.objects.values_list('tenant_id', flat=True).distinct()[:10]
        )

        # Get request tenant_id (may be None if no auth)
        request_tenant_id = getattr(request, 'tenant_id', None)
        tenant_leads = Lead.objects.filter(tenant_id=request_tenant_id).count() if request_tenant_id else 0

        # Get the 3 most recent leads (any tenant)
        recent_leads = list(
            Lead.objects.order_by('-created_at').values(
                'id', 'name', 'tenant_id', 'created_at', 'source'
            )[:3]
        )
        for lead in recent_leads:
            lead['tenant_id'] = str(lead['tenant_id'])
            lead['created_at'] = lead['created_at'].isoformat() if lead['created_at'] else None

        # Check the actual column type in the database
        with db_connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'crm_lead' AND column_name = 'tenant_id'"
            )
            column_info = cursor.fetchone()

        return JsonResponse({
            'request_tenant_id': str(request_tenant_id) if request_tenant_id else None,
            'request_tenant_id_type': type(request_tenant_id).__name__,
            'total_leads_all_tenants': total_leads,
            'leads_for_request_tenant': tenant_leads,
            'distinct_tenant_ids_in_db': [str(t) for t in distinct_tenants],
            'recent_leads': recent_leads,
            'db_engine': db_connection.vendor,
            'tenant_id_column_info': list(column_info) if column_info else None,
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)