from rest_framework import serializers
from .models import (
    Lead, LeadStatus, LeadActivity, LeadOrder,
    LeadFieldConfiguration
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


class LeadFieldConfigurationSerializer(TenantMixin):
    """
    Unified serializer for Lead field configurations.
    Handles both standard Lead model fields and custom fields.
    """

    class Meta:
        model = LeadFieldConfiguration
        fields = [
            'id', 'field_name', 'field_label', 'is_standard', 'field_type',
            'is_visible', 'is_required', 'is_active', 'default_value',
            'options', 'placeholder', 'help_text', 'display_order',
            'validation_rules', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_field_name(self, value):
        """Validate field_name is a valid identifier"""
        if not value.replace('_', '').isalnum():
            raise serializers.ValidationError(
                "Field name must contain only letters, numbers, and underscores"
            )
        if value and value[0].isdigit():
            raise serializers.ValidationError(
                "Field name cannot start with a number"
            )
        return value.lower()

    def validate(self, data):
        """Validate field configuration based on type and category"""
        is_standard = data.get('is_standard', False)
        field_type = data.get('field_type')
        field_name = data.get('field_name')
        options = data.get('options')

        # Standard fields validation
        if is_standard:
            valid_standard_fields = [
                'name', 'phone', 'email', 'company', 'title', 'status',
                'priority', 'value_amount', 'value_currency', 'source',
                'owner_user_id', 'assigned_to', 'last_contacted_at',
                'next_follow_up_at', 'notes', 'address_line1', 'address_line2',
                'city', 'state', 'country', 'postal_code'
            ]

            if field_name and field_name not in valid_standard_fields:
                raise serializers.ValidationError({
                    'field_name': f"'{field_name}' is not a valid Lead model field. "
                                f"Valid fields: {', '.join(valid_standard_fields)}"
                })
            
            # Standard fields don't need field_type specified (it's predetermined)
            # But we can allow it for informational purposes

        # Custom fields validation
        else:
            # Custom fields must have a field_type
            if not field_type:
                raise serializers.ValidationError({
                    'field_type': 'Custom fields must have a field_type specified'
                })

            # Dropdown/multiselect fields must have options
            if field_type in ['DROPDOWN', 'MULTISELECT']:
                if not options or not isinstance(options, list) or len(options) == 0:
                    raise serializers.ValidationError({
                        'options': 'Dropdown and multiselect fields must have at least one option'
                    })

        return data

    def to_representation(self, instance):
        """Add computed fields to the representation"""
        representation = super().to_representation(instance)
        
        # Add a category field for easier frontend filtering
        representation['category'] = 'standard' if instance.is_standard else 'custom'
        
        return representation