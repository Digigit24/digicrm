from rest_framework import viewsets, filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from django.db.models import Q
from datetime import datetime, date, timedelta
from .models import Meeting
from .serializers import MeetingSerializer, MeetingListSerializer
from common.mixins import TenantViewSetMixin
import logging

logger = logging.getLogger(__name__)


@extend_schema_view(
    list=extend_schema(description='List all meetings'),
    retrieve=extend_schema(description='Retrieve a specific meeting'),
    create=extend_schema(description='Create a new meeting'),
    update=extend_schema(description='Update a meeting'),
    partial_update=extend_schema(description='Partially update a meeting'),
    destroy=extend_schema(description='Delete a meeting'),
)
class MeetingViewSet(TenantViewSetMixin, viewsets.ModelViewSet):
    """
    ViewSet for managing Meetings
    """
    queryset = Meeting.objects.select_related('lead')
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = {
        'lead': ['exact'],
        'start_at': ['gte', 'lte', 'exact'],
        'end_at': ['gte', 'lte', 'exact'],
        'created_at': ['gte', 'lte'],
    }
    search_fields = ['title', 'location', 'description', 'notes']
    ordering_fields = ['start_at', 'end_at', 'created_at', 'title']
    ordering = ['start_at']

    def get_serializer_class(self):
        """Use lighter serializer for list view"""
        if self.action == 'list':
            return MeetingListSerializer
        return MeetingSerializer

    @extend_schema(
        description='Get meetings organized by date for calendar view',
        parameters=[
            {
                'name': 'start_date',
                'in': 'query',
                'description': 'Start date for calendar view (YYYY-MM-DD)',
                'required': False,
                'schema': {'type': 'string', 'format': 'date'}
            },
            {
                'name': 'end_date',
                'in': 'query',
                'description': 'End date for calendar view (YYYY-MM-DD)',
                'required': False,
                'schema': {'type': 'string', 'format': 'date'}
            },
            {
                'name': 'month',
                'in': 'query',
                'description': 'Month for calendar view (YYYY-MM)',
                'required': False,
                'schema': {'type': 'string', 'pattern': r'^\d{4}-\d{2}$'}
            }
        ],
        responses={200: {
            'type': 'object',
            'properties': {
                'calendar_data': {
                    'type': 'object',
                    'additionalProperties': {
                        'type': 'array',
                        'items': {'$ref': '#/components/schemas/MeetingList'}
                    }
                },
                'total_meetings': {'type': 'integer'},
                'date_range': {
                    'type': 'object',
                    'properties': {
                        'start_date': {'type': 'string', 'format': 'date'},
                        'end_date': {'type': 'string', 'format': 'date'}
                    }
                }
            }
        }}
    )
    @action(detail=False, methods=['get'])
    def calendar(self, request):
        """
        Get meetings organized by date for calendar view
        Supports filtering by date range or specific month
        """
        try:
            logger.info(f"Calendar view requested by tenant: {request.tenant_id}")
            
            # Parse query parameters
            start_date_param = request.query_params.get('start_date')
            end_date_param = request.query_params.get('end_date')
            month_param = request.query_params.get('month')
            
            # Determine date range
            if month_param:
                # Parse month parameter (YYYY-MM)
                try:
                    year, month = map(int, month_param.split('-'))
                    start_date = date(year, month, 1)
                    # Get last day of month
                    if month == 12:
                        end_date = date(year + 1, 1, 1)
                    else:
                        end_date = date(year, month + 1, 1)
                except ValueError:
                    return Response(
                        {'error': 'Invalid month format. Use YYYY-MM'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            elif start_date_param and end_date_param:
                # Parse individual dates
                try:
                    start_date = datetime.strptime(start_date_param, '%Y-%m-%d').date()
                    end_date = datetime.strptime(end_date_param, '%Y-%m-%d').date()
                except ValueError:
                    return Response(
                        {'error': 'Invalid date format. Use YYYY-MM-DD'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            else:
                # Default to current month
                today = date.today()
                start_date = date(today.year, today.month, 1)
                if today.month == 12:
                    end_date = date(today.year + 1, 1, 1)
                else:
                    end_date = date(today.year, today.month + 1, 1)
            
            # Query meetings within date range
            meetings = Meeting.objects.filter(
                tenant_id=request.tenant_id,
                start_at__date__gte=start_date,
                start_at__date__lt=end_date
            ).select_related('lead').order_by('start_at')
            
            # Group meetings by date
            calendar_data = {}
            for meeting in meetings:
                meeting_date = meeting.start_at.date().isoformat()
                if meeting_date not in calendar_data:
                    calendar_data[meeting_date] = []
                
                # Serialize meeting data
                meeting_serializer = MeetingListSerializer(meeting)
                calendar_data[meeting_date].append(meeting_serializer.data)
            
            logger.info(f"Calendar data prepared for {meetings.count()} meetings across {len(calendar_data)} dates")
            
            return Response({
                'calendar_data': calendar_data,
                'total_meetings': meetings.count(),
                'date_range': {
                    'start_date': start_date.isoformat(),
                    'end_date': (end_date - timedelta(days=1)).isoformat()
                }
            })
            
        except Exception as e:
            logger.error(f"Error in calendar view: {str(e)}")
            return Response(
                {'error': 'Failed to fetch calendar data'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )