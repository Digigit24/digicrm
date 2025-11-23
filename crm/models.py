from django.db import models
from django.contrib.auth.models import User


class PriorityEnum(models.TextChoices):
    LOW = 'LOW', 'Low'
    MEDIUM = 'MEDIUM', 'Medium'
    HIGH = 'HIGH', 'High'


class ActivityTypeEnum(models.TextChoices):
    CALL = 'CALL', 'Call'
    EMAIL = 'EMAIL', 'Email'
    MEETING = 'MEETING', 'Meeting'
    NOTE = 'NOTE', 'Note'
    
    SMS = 'SMS', 'SMS'
    OTHER = 'OTHER', 'Other'


class FieldTypeEnum(models.TextChoices):
    TEXT = 'TEXT', 'Text'
    NUMBER = 'NUMBER', 'Number'
    EMAIL = 'EMAIL', 'Email'
    PHONE = 'PHONE', 'Phone'
    DATE = 'DATE', 'Date'
    DATETIME = 'DATETIME', 'Date Time'
    DROPDOWN = 'DROPDOWN', 'Dropdown'
    MULTISELECT = 'MULTISELECT', 'Multi Select'
    CHECKBOX = 'CHECKBOX', 'Checkbox'
    URL = 'URL', 'URL'
    TEXTAREA = 'TEXTAREA', 'Text Area'
    DECIMAL = 'DECIMAL', 'Decimal'
    CURRENCY = 'CURRENCY', 'Currency'


class LeadStatus(models.Model):
    """Lead Status model for managing pipeline stages"""
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    name = models.TextField()
    order_index = models.IntegerField()
    color_hex = models.TextField(null=True, blank=True)
    is_won = models.BooleanField(default=False)
    is_lost = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'lead_statuses'
        ordering = ['order_index']
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_lead_statuses_tenant_id'),
            models.Index(fields=['order_index'], name='idx_lead_statuses_order_index'),
        ]
        constraints = [
            models.CheckConstraint(
                check=~(models.Q(is_won=True) & models.Q(is_lost=True)),
                name='lead_statuses_won_lost_check'
            ),
            models.UniqueConstraint(
                fields=['tenant_id', 'name'],
                name='unique_lead_status_per_tenant'
            )
        ]

    def __str__(self):
        return self.name


class Lead(models.Model):
    """Main Lead model for CRM"""
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    name = models.TextField(default='Unnamed')
    phone = models.TextField()
    email = models.TextField(null=True, blank=True)
    company = models.TextField(null=True, blank=True)
    title = models.TextField(null=True, blank=True)
    status = models.ForeignKey(
        LeadStatus,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='leads',
        db_column='status_id'
    )
    priority = models.CharField(
        max_length=10,
        choices=PriorityEnum.choices,
        default=PriorityEnum.MEDIUM
    )
    value_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    value_currency = models.TextField(null=True, blank=True)
    source = models.TextField(null=True, blank=True)
    owner_user_id = models.UUIDField(db_index=True)
    assigned_to = models.UUIDField(db_index=True, null=True, blank=True)
    metadata = models.JSONField(null=True, blank=True, help_text='Custom fields for storing dynamic key-value pairs')
    last_contacted_at = models.DateTimeField(null=True, blank=True)
    next_follow_up_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    address_line1 = models.TextField(null=True, blank=True)
    address_line2 = models.TextField(null=True, blank=True)
    city = models.TextField(null=True, blank=True)
    state = models.TextField(null=True, blank=True)
    country = models.TextField(null=True, blank=True)
    postal_code = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'leads'
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_leads_tenant_id'),
            models.Index(fields=['status'], name='idx_leads_status_id'),
            models.Index(fields=['priority'], name='idx_leads_priority'),
            models.Index(fields=['owner_user_id'], name='idx_leads_owner_user_id'),
            models.Index(fields=['assigned_to'], name='idx_leads_assigned_to'),
            models.Index(fields=['phone'], name='idx_leads_phone'),
        ]

    def __str__(self):
        return f"{self.name} - {self.phone}"


class LeadActivity(models.Model):
    """Activity tracking for leads"""
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    lead = models.ForeignKey(
        Lead,
        on_delete=models.CASCADE,
        related_name='activities',
        db_column='lead_id'
    )
    type = models.CharField(max_length=20, choices=ActivityTypeEnum.choices)
    content = models.TextField(null=True, blank=True)
    happened_at = models.DateTimeField()
    by_user_id = models.UUIDField(db_index=True)
    meta = models.JSONField(null=True, blank=True)
    file_url = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'lead_activities'
        ordering = ['-happened_at']
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_lead_activities_tenant_id'),
            models.Index(fields=['lead'], name='idx_lead_activities_lead_id'),
            models.Index(fields=['type'], name='idx_lead_activities_type'),
            models.Index(fields=['happened_at'], name='idx_lead_activities__at'),
            models.Index(fields=['by_user_id'], name='idx_lead_activities_by_user_id'),
        ]

    def __str__(self):
        return f"{self.lead.name} - {self.type} - {self.happened_at}"


class LeadOrder(models.Model):
    """Order/position of leads within a status (for kanban boards)"""
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    lead = models.ForeignKey(
        Lead,
        on_delete=models.CASCADE,
        related_name='orders',
        db_column='lead_id'
    )
    status = models.ForeignKey(
        LeadStatus,
        on_delete=models.CASCADE,
        related_name='lead_orders',
        db_column='status_id'
    )
    position = models.DecimalField(max_digits=12, decimal_places=3)
    board_id = models.BigIntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'lead_orders'
        unique_together = [['lead', 'status']]
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_lead_orders_tenant_id'),
            models.Index(fields=['status', 'position'], name='idx_lead_orders_status_id_pos'),
            models.Index(fields=['lead'], name='idx_lead_orders_lead_id'),
        ]

    def __str__(self):
        return f"{self.lead.name} - {self.status.name} - Position: {self.position}"


class LeadFieldConfiguration(models.Model):
    """
    Unified model for managing both standard Lead fields and custom fields.
    Standard fields are pre-defined Lead model fields with visibility/display settings.
    Custom fields are dynamic fields stored in Lead.metadata JSON.
    """
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    
    # Field identification
    field_name = models.TextField(help_text='Field name/key (must be unique per tenant)')
    field_label = models.TextField(help_text='Display label for the field')
    
    # Field classification
    is_standard = models.BooleanField(
        default=False,
        help_text='True if this is a standard Lead model field, False if custom field'
    )
    
    # Field type (only applicable for custom fields)
    field_type = models.CharField(
        max_length=20,
        choices=FieldTypeEnum.choices,
        default=FieldTypeEnum.TEXT,
        null=True,
        blank=True,
        help_text='Data type for custom fields'
    )
    
    # Visibility and requirements
    is_visible = models.BooleanField(default=True, help_text='Whether field is visible in UI')
    is_required = models.BooleanField(default=False, help_text='Whether field is required')
    is_active = models.BooleanField(default=True, help_text='Whether field is active')
    
    # Field configuration
    default_value = models.TextField(null=True, blank=True, help_text='Default value for the field')
    options = models.JSONField(
        null=True,
        blank=True,
        help_text='Options for dropdown/multiselect fields (array of strings)'
    )
    placeholder = models.TextField(null=True, blank=True, help_text='Placeholder text')
    help_text = models.TextField(null=True, blank=True, help_text='Help text for the field')
    
    # Display settings
    display_order = models.IntegerField(default=0, help_text='Order in which field appears')
    
    # Validation rules (JSON for flexibility)
    validation_rules = models.JSONField(
        null=True,
        blank=True,
        help_text='Custom validation rules (min, max, pattern, etc.)'
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'lead_field_configurations'
        ordering = ['display_order', 'field_label']
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_lead_field_config_tenant'),
            models.Index(fields=['tenant_id', 'is_active'], name='idx_lead_field_config_active'),
            models.Index(fields=['tenant_id', 'is_standard'], name='idx_lead_field_config_standard'),
            models.Index(fields=['display_order'], name='idx_lead_field_config_order'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['tenant_id', 'field_name'],
                name='unique_field_config_per_tenant'
            )
        ]

    def __str__(self):
        field_category = "Standard" if self.is_standard else "Custom"
        return f"{field_category}: {self.field_label} ({self.field_name})"