from rest_framework import viewsets, filters
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from .models import Payment
from .serializers import PaymentSerializer, PaymentListSerializer


@extend_schema_view(
    list=extend_schema(description='List all payments'),
    retrieve=extend_schema(description='Retrieve a specific payment'),
    create=extend_schema(description='Create a new payment'),
    update=extend_schema(description='Update a payment'),
    partial_update=extend_schema(description='Partially update a payment'),
    destroy=extend_schema(description='Delete a payment'),
)
class PaymentViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Payments
    """
    queryset = Payment.objects.select_related('lead')
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = {
        'lead': ['exact'],
        'type': ['exact'],
        'status': ['exact'],
        'date': ['gte', 'lte', 'exact'],
        'amount': ['gte', 'lte', 'exact'],
        'currency': ['exact'],
        'created_at': ['gte', 'lte'],
    }
    search_fields = ['reference_no', 'method', 'notes', 'lead__name']
    ordering_fields = ['date', 'amount', 'created_at']
    ordering = ['-date']

    def get_serializer_class(self):
        """Use lighter serializer for list view"""
        if self.action == 'list':
            return PaymentListSerializer
        return PaymentSerializer