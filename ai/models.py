from django.db import models
import uuid


class AIChatSession(models.Model):
    """AI Copilot chat session - tenant and user scoped."""

    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    user_id = models.UUIDField(db_index=True)
    title = models.CharField(max_length=200, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ai_chat_sessions'
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['tenant_id', 'user_id', '-updated_at']),
            models.Index(fields=['tenant_id', 'user_id', '-created_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['tenant_id', 'user_id', 'id'],
                name='unique_ai_chat_session_per_user'
            ),
        ]

    def __str__(self):
        return f"Session {self.id} - {self.title[:50] if self.title else 'Untitled'}"


class AIChatMessage(models.Model):
    """AI Copilot chat message within a session."""

    ROLE_CHOICES = [
        ('user', 'user'),
        ('assistant', 'assistant'),
        ('tool', 'tool'),
    ]

    id = models.BigAutoField(primary_key=True)
    session = models.ForeignKey(
        AIChatSession,
        on_delete=models.CASCADE,
        related_name='messages',
        db_column='session_id'
    )
    role = models.CharField(max_length=16, choices=ROLE_CHOICES)
    content = models.TextField()
    sequence = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ai_chat_messages'
        ordering = ['sequence']
        indexes = [
            models.Index(fields=['session', 'sequence']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['session', 'sequence'],
                name='unique_sequence_per_session'
            ),
        ]

    def __str__(self):
        return f"{self.session_id} #{self.sequence} ({self.role})"