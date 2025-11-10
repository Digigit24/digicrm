from django.db import models
from crm.models import Lead


class Meeting(models.Model):
    """Meeting model for scheduling and tracking meetings"""
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    lead = models.ForeignKey(
        Lead,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='meetings',
        db_column='lead_id'
    )
    title = models.TextField()
    location = models.TextField(null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    owner_user_id = models.UUIDField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'meetings'
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_meetings_tenant_id'),
            models.Index(fields=['lead'], name='idx_meetings_lead_id'),
            models.Index(fields=['start_at'], name='idx_meetings_start_at'),
            models.Index(fields=['owner_user_id'], name='idx_meetings_owner_user_id'),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(end_at__gt=models.F('start_at')),
                name='meetings_end_after_start'
            ),
            models.UniqueConstraint(
                fields=['tenant_id', 'title', 'start_at'],
                name='unique_meeting_per_tenant'
            )
        ]

    def __str__(self):
        return f"{self.title} - {self.start_at}"