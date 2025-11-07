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


class LeadStatus(models.Model):
    """Lead Status model for managing pipeline stages"""
    id = models.BigAutoField(primary_key=True)
    name = models.TextField(unique=True)
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
            models.Index(fields=['order_index'], name='idx_lead_statuses_order_index'),
        ]
        constraints = [
            models.CheckConstraint(
                check=~(models.Q(is_won=True) & models.Q(is_lost=True)),
                name='lead_statuses_won_lost_check'
            )
        ]

    def __str__(self):
        return self.name


class Lead(models.Model):
    """Main Lead model for CRM"""
    id = models.BigAutoField(primary_key=True)
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
    owner_user_id = models.UUIDField(null=True, blank=True)
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
            models.Index(fields=['status'], name='idx_leads_status_id'),
            models.Index(fields=['priority'], name='idx_leads_priority'),
            models.Index(fields=['owner_user_id'], name='idx_leads_owner_user_id'),
            models.Index(fields=['phone'], name='idx_leads_phone'),
        ]

    def __str__(self):
        return f"{self.name} - {self.phone}"


class LeadActivity(models.Model):
    """Activity tracking for leads"""
    id = models.BigAutoField(primary_key=True)
    lead = models.ForeignKey(
        Lead,
        on_delete=models.CASCADE,
        related_name='activities',
        db_column='lead_id'
    )
    type = models.CharField(max_length=20, choices=ActivityTypeEnum.choices)
    content = models.TextField(null=True, blank=True)
    happened_at = models.DateTimeField()
    by_user_id = models.UUIDField(null=True, blank=True)
    meta = models.JSONField(null=True, blank=True)
    file_url = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'lead_activities'
        ordering = ['-happened_at']
        indexes = [
            models.Index(fields=['lead'], name='idx_lead_activities_lead_id'),
            models.Index(fields=['type'], name='idx_lead_activities_type'),
            models.Index(fields=['happened_at'], name='idx_lead_activities__at'),
        ]

    def __str__(self):
        return f"{self.lead.name} - {self.type} - {self.happened_at}"


class LeadOrder(models.Model):
    """Order/position of leads within a status (for kanban boards)"""
    id = models.BigAutoField(primary_key=True)
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
            models.Index(fields=['status', 'position'], name='idx_lead_orders_status_id_pos'),
            models.Index(fields=['lead'], name='idx_lead_orders_lead_id'),
        ]

    def __str__(self):
        return f"{self.lead.name} - {self.status.name} - Position: {self.position}"