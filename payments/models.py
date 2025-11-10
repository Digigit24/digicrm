from django.db import models
from crm.models import Lead


class PaymentTypeEnum(models.TextChoices):
    INVOICE = 'INVOICE', 'Invoice'
    REFUND = 'REFUND', 'Refund'
    ADVANCE = 'ADVANCE', 'Advance'
    OTHER = 'OTHER', 'Other'


class PaymentStatusEnum(models.TextChoices):
    PENDING = 'PENDING', 'Pending'
    CLEARED = 'CLEARED', 'Cleared'
    FAILED = 'FAILED', 'Failed'
    CANCELLED = 'CANCELLED', 'Cancelled'


class Payment(models.Model):
    """Payment model for tracking financial transactions"""
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    lead = models.ForeignKey(
        Lead,
        on_delete=models.CASCADE,
        related_name='payments',
        db_column='lead_id'
    )
    type = models.CharField(max_length=20, choices=PaymentTypeEnum.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.TextField(default='INR')
    method = models.TextField(null=True, blank=True)
    reference_no = models.TextField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    date = models.DateTimeField()
    status = models.CharField(
        max_length=20,
        choices=PaymentStatusEnum.choices,
        default=PaymentStatusEnum.CLEARED
    )
    attachment_url = models.TextField(null=True, blank=True)
    owner_user_id = models.UUIDField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'payments'
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_payments_tenant_id'),
            models.Index(fields=['lead'], name='idx_payments_lead_id'),
            models.Index(fields=['type'], name='idx_payments_type'),
            models.Index(fields=['status'], name='idx_payments_status'),
            models.Index(fields=['date'], name='idx_payments_date'),
            models.Index(fields=['owner_user_id'], name='idx_payments_owner_user_id'),
        ]

    def __str__(self):
        return f"{self.lead.name} - {self.type} - {self.amount} {self.currency}"