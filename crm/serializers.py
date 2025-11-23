from rest_framework import serializers
from .models import Lead, LeadStatus, LeadActivity, LeadOrder
from common.mixins import TenantMixin


class LeadStatusSerializer(TenantMixin):
    """Serializer for LeadStatus model"""
    
    class Meta:
        model = LeadStatus
        fields = [
            'id', 'name', 'order_index', 'color_hex', 'is_won',
            'is_lost', 'is_active', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class LeadActivitySerializer(TenantMixin):
    """Serializer for LeadActivity model"""
    
    class Meta:
        model = LeadActivity
        fields = [
            'id', 'lead', 'type', 'content', 'happened_at',
            'by_user_id', 'meta', 'file_url', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']


class LeadOrderSerializer(TenantMixin):
    """Serializer for LeadOrder model"""
    
    class Meta:
        model = LeadOrder
        fields = ['id', 'lead', 'status', 'position', 'board_id', 'updated_at']
        read_only_fields = ['id', 'updated_at']


class LeadSerializer(TenantMixin):
    """Serializer for Lead model"""
    status_name = serializers.CharField(source='status.name', read_only=True)
    activities = LeadActivitySerializer(many=True, read_only=True)

    class Meta:
        model = Lead
        fields = [
            'id', 'name', 'phone', 'email', 'company', 'title',
            'status', 'status_name', 'priority', 'value_amount', 'value_currency',
            'source', 'owner_user_id', 'assigned_to', 'metadata', 'last_contacted_at',
            'next_follow_up_at', 'notes', 'address_line1', 'address_line2', 'city',
            'state', 'country', 'postal_code', 'created_at', 'updated_at', 'activities'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class LeadListSerializer(TenantMixin):
    """Lightweight serializer for listing leads"""
    status_name = serializers.CharField(source='status.name', read_only=True)

    class Meta:
        model = Lead
        fields = [
            'id', 'name', 'phone', 'email', 'company', 'status',
            'status_name', 'priority', 'value_amount', 'value_currency',
            'owner_user_id', 'assigned_to', 'metadata', 'next_follow_up_at',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']