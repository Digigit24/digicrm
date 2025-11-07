from rest_framework import serializers
from .models import Payment


class PaymentSerializer(serializers.ModelSerializer):
    """Serializer for Payment model"""
    lead_name = serializers.CharField(source='lead.name', read_only=True)
    
    class Meta:
        model = Payment
        fields = [
            'id', 'lead', 'lead_name', 'type', 'amount', 'currency',
            'method', 'reference_no', 'notes', 'date', 'status',
            'attachment_url', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class PaymentListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing payments"""
    lead_name = serializers.CharField(source='lead.name', read_only=True)
    
    class Meta:
        model = Payment
        fields = [
            'id', 'lead', 'lead_name', 'type', 'amount', 'currency',
            'date', 'status', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']