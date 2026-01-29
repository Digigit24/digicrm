"""
Utility functions for CRM app
"""
from .models import LeadFieldConfiguration, FieldTypeEnum


def get_default_standard_fields():
    """
    Returns a list of default standard field configurations for the Lead model.
    These will be auto-populated when a tenant first accesses the field configurations API.

    Each field includes:
    - field_name: The actual field name in the Lead model
    - field_label: Display label for the UI
    - field_type: Data type (informational for standard fields)
    - is_standard: Always True
    - is_visible: Whether to show in UI by default
    - is_required: Whether the field is required
    - is_active: Whether the field is active
    - display_order: Order in which fields appear
    - placeholder: Placeholder text for input fields
    - help_text: Help text for the field
    """

    standard_fields = [
        # Core identification fields
        {
            'field_name': 'name',
            'field_label': 'Name',
            'field_type': FieldTypeEnum.TEXT,
            'is_standard': True,
            'is_visible': True,
            'is_required': True,
            'is_active': True,
            'display_order': 10,
            'placeholder': 'Enter contact name',
            'help_text': 'Full name of the lead contact'
        },
        {
            'field_name': 'phone',
            'field_label': 'Phone',
            'field_type': FieldTypeEnum.PHONE,
            'is_standard': True,
            'is_visible': True,
            'is_required': True,
            'is_active': True,
            'display_order': 20,
            'placeholder': '+1234567890',
            'help_text': 'Primary phone number'
        },
        {
            'field_name': 'email',
            'field_label': 'Email',
            'field_type': FieldTypeEnum.EMAIL,
            'is_standard': True,
            'is_visible': True,
            'is_required': False,
            'is_active': True,
            'display_order': 30,
            'placeholder': 'email@example.com',
            'help_text': 'Primary email address'
        },

        # Company information
        {
            'field_name': 'company',
            'field_label': 'Company',
            'field_type': FieldTypeEnum.TEXT,
            'is_standard': True,
            'is_visible': True,
            'is_required': False,
            'is_active': True,
            'display_order': 40,
            'placeholder': 'Company name',
            'help_text': 'Company or organization name'
        },
        {
            'field_name': 'title',
            'field_label': 'Title',
            'field_type': FieldTypeEnum.TEXT,
            'is_standard': True,
            'is_visible': True,
            'is_required': False,
            'is_active': True,
            'display_order': 50,
            'placeholder': 'Job title',
            'help_text': 'Job title or position'
        },

        # Lead management
        {
            'field_name': 'status',
            'field_label': 'Status',
            'field_type': FieldTypeEnum.DROPDOWN,
            'is_standard': True,
            'is_visible': True,
            'is_required': False,
            'is_active': True,
            'display_order': 60,
            'placeholder': 'Select status',
            'help_text': 'Current pipeline stage'
        },
        {
            'field_name': 'priority',
            'field_label': 'Priority',
            'field_type': FieldTypeEnum.DROPDOWN,
            'is_standard': True,
            'is_visible': True,
            'is_required': False,
            'is_active': True,
            'display_order': 70,
            'placeholder': 'Select priority',
            'help_text': 'Lead priority level (Low, Medium, High)'
        },

        # Value tracking
        {
            'field_name': 'value_amount',
            'field_label': 'Deal Value',
            'field_type': FieldTypeEnum.CURRENCY,
            'is_standard': True,
            'is_visible': True,
            'is_required': False,
            'is_active': True,
            'display_order': 80,
            'placeholder': '0.00',
            'help_text': 'Estimated deal value'
        },
        {
            'field_name': 'value_currency',
            'field_label': 'Currency',
            'field_type': FieldTypeEnum.TEXT,
            'is_standard': True,
            'is_visible': True,
            'is_required': False,
            'is_active': True,
            'display_order': 90,
            'placeholder': 'USD',
            'help_text': 'Currency code (e.g., USD, EUR, GBP)'
        },
        {
            'field_name': 'source',
            'field_label': 'Source',
            'field_type': FieldTypeEnum.TEXT,
            'is_standard': True,
            'is_visible': True,
            'is_required': False,
            'is_active': True,
            'display_order': 100,
            'placeholder': 'Lead source',
            'help_text': 'How the lead was acquired (e.g., Website, Referral, Advertisement)'
        },

        # Assignment
        {
            'field_name': 'owner_user_id',
            'field_label': 'Owner',
            'field_type': FieldTypeEnum.TEXT,
            'is_standard': True,
            'is_visible': True,
            'is_required': False,
            'is_active': True,
            'display_order': 110,
            'placeholder': 'Select owner',
            'help_text': 'Lead owner (created by)'
        },
        {
            'field_name': 'assigned_to',
            'field_label': 'Assigned To',
            'field_type': FieldTypeEnum.TEXT,
            'is_standard': True,
            'is_visible': True,
            'is_required': False,
            'is_active': True,
            'display_order': 120,
            'placeholder': 'Assign to user',
            'help_text': 'User assigned to follow up on this lead'
        },

        # Timeline
        {
            'field_name': 'last_contacted_at',
            'field_label': 'Last Contacted',
            'field_type': FieldTypeEnum.DATETIME,
            'is_standard': True,
            'is_visible': True,
            'is_required': False,
            'is_active': True,
            'display_order': 130,
            'placeholder': 'Select date and time',
            'help_text': 'When the lead was last contacted'
        },
        {
            'field_name': 'next_follow_up_at',
            'field_label': 'Next Follow Up',
            'field_type': FieldTypeEnum.DATETIME,
            'is_standard': True,
            'is_visible': True,
            'is_required': False,
            'is_active': True,
            'display_order': 140,
            'placeholder': 'Select date and time',
            'help_text': 'When to follow up with this lead'
        },

        # Additional information
        {
            'field_name': 'notes',
            'field_label': 'Notes',
            'field_type': FieldTypeEnum.TEXTAREA,
            'is_standard': True,
            'is_visible': True,
            'is_required': False,
            'is_active': True,
            'display_order': 150,
            'placeholder': 'Add notes...',
            'help_text': 'Internal notes about the lead'
        },

        # Address fields
        {
            'field_name': 'address_line1',
            'field_label': 'Address Line 1',
            'field_type': FieldTypeEnum.TEXT,
            'is_standard': True,
            'is_visible': True,
            'is_required': False,
            'is_active': True,
            'display_order': 160,
            'placeholder': 'Street address',
            'help_text': 'Street address, P.O. box, or company name'
        },
        {
            'field_name': 'address_line2',
            'field_label': 'Address Line 2',
            'field_type': FieldTypeEnum.TEXT,
            'is_standard': True,
            'is_visible': True,
            'is_required': False,
            'is_active': True,
            'display_order': 170,
            'placeholder': 'Apt, suite, unit, building, floor, etc.',
            'help_text': 'Apartment, suite, unit, building, floor, etc.'
        },
        {
            'field_name': 'city',
            'field_label': 'City',
            'field_type': FieldTypeEnum.TEXT,
            'is_standard': True,
            'is_visible': True,
            'is_required': False,
            'is_active': True,
            'display_order': 180,
            'placeholder': 'City',
            'help_text': 'City or locality'
        },
        {
            'field_name': 'state',
            'field_label': 'State/Province',
            'field_type': FieldTypeEnum.TEXT,
            'is_standard': True,
            'is_visible': True,
            'is_required': False,
            'is_active': True,
            'display_order': 190,
            'placeholder': 'State or province',
            'help_text': 'State, province, or region'
        },
        {
            'field_name': 'country',
            'field_label': 'Country',
            'field_type': FieldTypeEnum.TEXT,
            'is_standard': True,
            'is_visible': True,
            'is_required': False,
            'is_active': True,
            'display_order': 200,
            'placeholder': 'Country',
            'help_text': 'Country'
        },
        {
            'field_name': 'postal_code',
            'field_label': 'Postal Code',
            'field_type': FieldTypeEnum.TEXT,
            'is_standard': True,
            'is_visible': True,
            'is_required': False,
            'is_active': True,
            'display_order': 210,
            'placeholder': 'ZIP or postal code',
            'help_text': 'ZIP or postal code'
        },
    ]

    return standard_fields


def ensure_default_field_configurations(tenant_id):
    """
    Ensures that default standard field configurations exist for the given tenant.
    If no field configurations exist, creates them with sensible defaults.

    This function is idempotent - it only creates fields if they don't already exist.

    Args:
        tenant_id: The tenant UUID

    Returns:
        tuple: (created_count, existing_count)
            - created_count: Number of field configurations created
            - existing_count: Number of field configurations that already existed
    """
    # Check if any field configurations already exist for this tenant
    existing_count = LeadFieldConfiguration.objects.filter(tenant_id=tenant_id).count()

    if existing_count > 0:
        # Field configurations already exist, don't create duplicates
        return (0, existing_count)

    # No field configurations exist, create defaults
    standard_fields = get_default_standard_fields()

    # Bulk create all standard field configurations
    field_configs = []
    for field_data in standard_fields:
        field_configs.append(
            LeadFieldConfiguration(
                tenant_id=tenant_id,
                **field_data
            )
        )

    LeadFieldConfiguration.objects.bulk_create(field_configs)

    created_count = len(field_configs)

    return (created_count, 0)
