from rest_framework import serializers
from .models import (
    Lead, LeadStatus, LeadActivity, LeadOrder,
    LeadFieldConfiguration
)
from common.mixins import TenantMixin


class LeadStatusSerializer(TenantMixin):
    """
    Serialize CRM pipeline status records.

    Agents use this schema to understand and manage the named stages that leads
    move through on the CRM board, such as new, contacted, qualified, won, or lost.
    """
    
    class Meta:
        model = LeadStatus
        fields = [
            'id', 'name', 'order_index', 'color_hex', 'is_won',
            'is_lost', 'is_active', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {
            'id': {'help_text': 'Unique numeric identifier for this lead status. Read-only.'},
            'name': {'help_text': 'Display name of the pipeline status, for example New, Contacted, Qualified, Closed Won, or Closed Lost.'},
            'order_index': {'help_text': 'Integer sort order used to arrange statuses on the pipeline board. Lower numbers appear earlier.'},
            'color_hex': {'help_text': 'Optional hex color used to display this status in the CRM UI, for example #22C55E.'},
            'is_won': {'help_text': 'Set to true when this status represents a successfully won lead or deal.'},
            'is_lost': {'help_text': 'Set to true when this status represents a lost or disqualified lead.'},
            'is_active': {'help_text': 'Set to false to hide or retire this status without deleting historical records.'},
            'created_at': {'help_text': 'Timestamp when this status was created, in ISO 8601 date-time format. Read-only.'},
            'updated_at': {'help_text': 'Timestamp when this status was last updated, in ISO 8601 date-time format. Read-only.'},
        }


class LeadActivitySerializer(TenantMixin):
    """
    Serialize timeline activity records attached to leads.

    Agents use this schema to log calls, emails, meetings, notes, SMS messages,
    and other interactions that explain what happened with a lead.
    """
    
    class Meta:
        model = LeadActivity
        fields = [
            'id', 'lead', 'type', 'content', 'happened_at',
            'by_user_id', 'meta', 'file_url', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']
        extra_kwargs = {
            'id': {'help_text': 'Unique numeric identifier for this activity. Read-only.'},
            'lead': {'help_text': 'Numeric ID of the lead this activity belongs to.'},
            'type': {'help_text': 'Activity type. Valid values are CALL, EMAIL, MEETING, NOTE, SMS, or OTHER.'},
            'content': {'help_text': 'Human-readable activity details, such as call summary, email note, or meeting outcome.'},
            'happened_at': {'help_text': 'When the activity occurred, in ISO 8601 date-time format.'},
            'by_user_id': {'help_text': 'UUID of the user or integration that performed or recorded this activity.'},
            'meta': {'help_text': 'Optional JSON object for provider-specific activity metadata, such as message IDs or call details.'},
            'file_url': {'help_text': 'Optional URL for a related attachment, recording, document, or file.'},
            'created_at': {'help_text': 'Timestamp when this activity record was created, in ISO 8601 date-time format. Read-only.'},
        }


class LeadOrderSerializer(TenantMixin):
    """
    Serialize lead ordering records used by kanban-style boards.

    Agents use this schema only when they need to place a lead within a status
    column or preserve a custom board order.
    """
    
    class Meta:
        model = LeadOrder
        fields = ['id', 'lead', 'status', 'position', 'board_id', 'updated_at']
        read_only_fields = ['id', 'updated_at']
        extra_kwargs = {
            'id': {'help_text': 'Unique numeric identifier for this lead order record. Read-only.'},
            'lead': {'help_text': 'Numeric ID of the lead being positioned on the board.'},
            'status': {'help_text': 'Numeric ID of the pipeline status column where the lead appears.'},
            'position': {'help_text': 'Decimal ordering value used to sort the lead within its status column.'},
            'board_id': {'help_text': 'Optional numeric board identifier when the tenant uses multiple boards.'},
            'updated_at': {'help_text': 'Timestamp when this ordering record was last updated, in ISO 8601 date-time format. Read-only.'},
        }


class LeadSerializer(TenantMixin):
    """
    Serialize complete CRM lead records.

    Agents use this schema to create, inspect, update, and enrich leads from
    websites, Meta Lead Ads, manual entry, imports, and other external sources.
    """
    status_name = serializers.CharField(
        source='status.name',
        read_only=True,
        help_text='Display name of the linked pipeline status. Read-only.'
    )
    activities = LeadActivitySerializer(
        many=True,
        read_only=True,
        help_text='Chronological activity records attached to this lead. Read-only in the lead payload.'
    )

    class Meta:
        model = Lead
        fields = [
            'id', 'name', 'phone', 'email', 'company', 'title',
            'status', 'status_name', 'priority', 'lead_score', 'value_amount', 'value_currency',
            'source', 'owner_user_id', 'assigned_to', 'metadata', 'last_contacted_at',
            'next_follow_up_at', 'notes', 'address_line1', 'address_line2', 'city',
            'state', 'country', 'postal_code', 'created_at', 'updated_at', 'activities'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {
            'id': {'help_text': 'Unique numeric identifier for this lead. Read-only.'},
            'name': {'help_text': 'Full name or display name of the lead. Use Unnamed only when no name is available.'},
            'phone': {'help_text': 'Primary phone number for the lead, preferably in international format such as +91XXXXXXXXXX.'},
            'email': {'help_text': 'Primary email address for the lead, if available.'},
            'company': {'help_text': 'Company, organization, or business name associated with the lead.'},
            'title': {'help_text': 'Job title, role, or designation of the lead contact.'},
            'status': {'help_text': 'Numeric ID of the current pipeline status for this lead. Null means no status is assigned.'},
            'priority': {'help_text': 'Lead priority. Valid values are LOW, MEDIUM, or HIGH.'},
            'lead_score': {'help_text': 'Numeric lead score from 0 to 100 used to rank lead quality or urgency.'},
            'value_amount': {'help_text': 'Estimated monetary value of the lead or potential deal, using decimal notation.'},
            'value_currency': {'help_text': 'Currency code or label for value_amount, for example INR, USD, or EUR.'},
            'source': {'help_text': 'Human-readable source of the lead, such as Meta Lead Ads, Website, Referral, WhatsApp, or Manual Entry.'},
            'owner_user_id': {
                'required': False,
                'help_text': 'UUID of the lead owner. If omitted during create, the CRM uses the authenticated JWT user_id.'
            },
            'assigned_to': {'help_text': 'Optional UUID of the user currently assigned to work this lead.'},
            'metadata': {'help_text': 'Optional JSON object for source-specific details. Use metadata.external_lead_id as an idempotency key for external systems.'},
            'last_contacted_at': {'help_text': 'Most recent contact timestamp in ISO 8601 date-time format, or null if never contacted.'},
            'next_follow_up_at': {'help_text': 'Planned next follow-up timestamp in ISO 8601 date-time format, or null if none is scheduled.'},
            'notes': {'help_text': 'Free-text notes about the lead, requirements, context, or conversation summary.'},
            'address_line1': {'help_text': 'First line of the lead address, such as street address or building name.'},
            'address_line2': {'help_text': 'Second line of the lead address, such as apartment, suite, area, or landmark.'},
            'city': {'help_text': 'City or town associated with the lead address.'},
            'state': {'help_text': 'State, province, or region associated with the lead address.'},
            'country': {'help_text': 'Country associated with the lead address.'},
            'postal_code': {'help_text': 'Postal or ZIP code associated with the lead address.'},
            'created_at': {'help_text': 'Timestamp when this lead was created, in ISO 8601 date-time format. Read-only.'},
            'updated_at': {'help_text': 'Timestamp when this lead was last updated, in ISO 8601 date-time format. Read-only.'},
        }

    def validate_lead_score(self, value):
        """Validate lead_score is between 0 and 100"""
        if value is not None and (value < 0 or value > 100):
            raise serializers.ValidationError(
                "Lead score must be between 0 and 100"
            )
        return value


class LeadListSerializer(TenantMixin):
    """
    Serialize compact lead records for list, search, and board views.

    Agents use this schema when browsing many leads where the full activity
    timeline is not needed.
    """
    status_name = serializers.CharField(
        source='status.name',
        read_only=True,
        help_text='Display name of the linked pipeline status. Read-only.'
    )

    class Meta:
        model = Lead
        fields = [
            'id', 'name', 'phone', 'email', 'company', 'status',
            'status_name', 'priority', 'lead_score', 'value_amount', 'value_currency',
            'owner_user_id', 'assigned_to', 'metadata', 'next_follow_up_at',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {
            'id': {'help_text': 'Unique numeric identifier for this lead. Read-only.'},
            'name': {'help_text': 'Full name or display name of the lead.'},
            'phone': {'help_text': 'Primary phone number for the lead, preferably in international format such as +91XXXXXXXXXX.'},
            'email': {'help_text': 'Primary email address for the lead, if available.'},
            'company': {'help_text': 'Company, organization, or business name associated with the lead.'},
            'status': {'help_text': 'Numeric ID of the current pipeline status for this lead. Null means no status is assigned.'},
            'priority': {'help_text': 'Lead priority. Valid values are LOW, MEDIUM, or HIGH.'},
            'lead_score': {'help_text': 'Numeric lead score from 0 to 100 used to rank lead quality or urgency.'},
            'value_amount': {'help_text': 'Estimated monetary value of the lead or potential deal, using decimal notation.'},
            'value_currency': {'help_text': 'Currency code or label for value_amount, for example INR, USD, or EUR.'},
            'owner_user_id': {'help_text': 'UUID of the lead owner.'},
            'assigned_to': {'help_text': 'Optional UUID of the user currently assigned to work this lead.'},
            'metadata': {'help_text': 'Optional JSON object for source-specific details, including external IDs and attribution data.'},
            'next_follow_up_at': {'help_text': 'Planned next follow-up timestamp in ISO 8601 date-time format, or null if none is scheduled.'},
            'created_at': {'help_text': 'Timestamp when this lead was created, in ISO 8601 date-time format. Read-only.'},
            'updated_at': {'help_text': 'Timestamp when this lead was last updated, in ISO 8601 date-time format. Read-only.'},
        }


class BulkLeadDeleteSerializer(serializers.Serializer):
    """
    Validate requests that delete multiple leads in one operation.

    Agents should use this only after explicit user confirmation because it
    permanently removes multiple lead records.
    """
    lead_ids = serializers.ListField(
        child=serializers.IntegerField(),
        min_length=1,
        help_text="List of numeric lead IDs to delete. At least one ID is required."
    )


class BulkLeadStatusUpdateSerializer(serializers.Serializer):
    """
    Validate requests that move multiple leads to the same pipeline status.

    Agents use this when a user wants to update several selected leads at once.
    """
    lead_ids = serializers.ListField(
        child=serializers.IntegerField(),
        min_length=1,
        help_text="List of numeric lead IDs whose status should be changed. At least one ID is required."
    )
    status_id = serializers.IntegerField(
        allow_null=True,
        help_text="Numeric status ID to assign to all selected leads. Use null to clear the status."
    )


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
        extra_kwargs = {
            'id': {'help_text': 'Unique numeric identifier for this field configuration. Read-only.'},
            'field_name': {'help_text': 'Machine-readable field key. Use lowercase letters, numbers, and underscores only; it must be unique per tenant.'},
            'field_label': {'help_text': 'Human-readable label shown to users for this field.'},
            'is_standard': {'help_text': 'True when this configuration describes a built-in Lead model field; false for custom metadata fields.'},
            'field_type': {'help_text': 'Data type for a custom field. Valid values include TEXT, NUMBER, EMAIL, PHONE, DATE, DATETIME, DROPDOWN, MULTISELECT, CHECKBOX, URL, TEXTAREA, DECIMAL, and CURRENCY.'},
            'is_visible': {'help_text': 'Whether this field should be visible in lead forms and lead detail screens.'},
            'is_required': {'help_text': 'Whether users must provide this field when creating or editing a lead.'},
            'is_active': {'help_text': 'Whether this field configuration is active. Set false to retire the field without deleting it.'},
            'default_value': {'help_text': 'Optional default value applied when a lead does not provide this field.'},
            'options': {'help_text': 'Array of allowed option strings for DROPDOWN or MULTISELECT custom fields.'},
            'placeholder': {'help_text': 'Optional placeholder text displayed in forms before the user enters a value.'},
            'help_text': {'help_text': 'Instructional text shown to users to explain what this field means.'},
            'display_order': {'help_text': 'Integer sort order used to arrange fields in the CRM UI. Lower numbers appear earlier.'},
            'validation_rules': {'help_text': 'Optional JSON object describing validation rules such as min, max, pattern, or length constraints.'},
            'created_at': {'help_text': 'Timestamp when this field configuration was created, in ISO 8601 date-time format. Read-only.'},
            'updated_at': {'help_text': 'Timestamp when this field configuration was last updated, in ISO 8601 date-time format. Read-only.'},
        }

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
