from django.db import models
from crm.models import Lead, PriorityEnum


class TaskStatusEnum(models.TextChoices):
    TODO = 'TODO', 'To Do'
    IN_PROGRESS = 'IN_PROGRESS', 'In Progress'
    DONE = 'DONE', 'Done'
    CANCELLED = 'CANCELLED', 'Cancelled'


class Task(models.Model):
    """Task model for managing lead-related tasks"""
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    lead = models.ForeignKey(
        Lead,
        on_delete=models.CASCADE,
        related_name='tasks',
        db_column='lead_id'
    )
    title = models.TextField()
    description = models.TextField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=TaskStatusEnum.choices,
        default=TaskStatusEnum.TODO
    )
    priority = models.CharField(
        max_length=10,
        choices=PriorityEnum.choices,
        default=PriorityEnum.MEDIUM
    )
    due_date = models.DateTimeField(null=True, blank=True)
    assignee_user_id = models.UUIDField(db_index=True, null=True, blank=True)
    reporter_user_id = models.UUIDField(db_index=True, null=True, blank=True)
    owner_user_id = models.UUIDField(db_index=True)
    checklist = models.JSONField(null=True, blank=True)
    attachments_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'tasks'
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_tasks_tenant_id'),
            models.Index(fields=['lead'], name='idx_tasks_lead_id'),
            models.Index(fields=['status'], name='idx_tasks_status'),
            models.Index(fields=['priority'], name='idx_tasks_priority'),
            models.Index(fields=['assignee_user_id'], name='idx_tasks_assignee'),
            models.Index(fields=['reporter_user_id'], name='idx_tasks_reporter'),
            models.Index(fields=['owner_user_id'], name='idx_tasks_owner_user_id'),
        ]

    def __str__(self):
        return f"{self.title} - {self.lead.name}"

    def save(self, *args, **kwargs):
        """Auto-set completed_at when status changes to DONE"""
        if self.status == TaskStatusEnum.DONE and not self.completed_at:
            from django.utils import timezone
            self.completed_at = timezone.now()
        elif self.status != TaskStatusEnum.DONE:
            self.completed_at = None
        super().save(*args, **kwargs)