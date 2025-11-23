from rest_framework import serializers
from .models import (
    Lead, LeadStatus, LeadActivity, LeadOrder,
    LeadCustomField, LeadFieldVisibility
)
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


class LeadCustomFieldSerializer(TenantMixin):
    """Serializer for LeadCustomField model"""

    class Meta:
        model = LeadCustomField
        fields = [
            'id', 'field_name', 'field_label', 'field_type', 'is_required',
            'default_value', 'options', 'placeholder', 'help_text',
            'display_order', 'is_active', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_field_name(self, value):
        """Validate field_name is a valid identifier"""
        if not value.replace('_', '').isalnum():
            raise serializers.ValidationError(
                "Field name must contain only letters, numbers, and underscores"
            )
        if value[0].isdigit():
            raise serializers.ValidationError(
                "Field name cannot start with a number"
            )
        return value.lower()

    def validate(self, data):
        """Validate dropdown/multiselect fields have options"""
        field_type = data.get('field_type')
        options = data.get('options')

        if field_type in ['DROPDOWN', 'MULTISELECT']:
            if not options or not isinstance(options, list) or len(options) == 0:
                raise serializers.ValidationError({
                    'options': 'Dropdown and multiselect fields must have at least one option'
                })

        return data


class LeadFieldVisibilitySerializer(TenantMixin):
    """Serializer for LeadFieldVisibility model"""

    class Meta:
        model = LeadFieldVisibility
        fields = [
            'id', 'field_name', 'is_visible', 'display_order',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_field_name(self, value):
        """Validate field_name is a valid Lead model field"""
        valid_fields = [
            'name', 'phone', 'email', 'company', 'title', 'status',
            'priority', 'value_amount', 'value_currency', 'source',
            'owner_user_id', 'assigned_to', 'last_contacted_at',
            'next_follow_up_at', 'notes', 'address_line1', 'address_line2',
            'city', 'state', 'country', 'postal_code'
        ]

        if value not in valid_fields:
            raise serializers.ValidationError(
                f"'{value}' is not a valid Lead model field. "
                f"Valid fields: {', '.join(valid_fields)}"
            )

        return value