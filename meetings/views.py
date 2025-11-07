from rest_framework import viewsets, filters
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from .models import Meeting
from .serializers import MeetingSerializer, MeetingListSerializer


@extend_schema_view(
    list=extend_schema(description='List all meetings'),
    retrieve=extend_schema(description='Retrieve a specific meeting'),
    create=extend_schema(description='Create a new meeting'),
    update=extend_schema(description='Update a meeting'),
    partial_update=extend_schema(description='Partially update a meeting'),
    destroy=extend_schema(description='Delete a meeting'),
)
class MeetingViewSet(viewsets.ModelViewSet):
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