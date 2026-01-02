"""
Django signals for integrations app.

Can be used for:
- Automatic cleanup when connections are deleted
- Logging workflow changes
- Triggering notifications on execution failures
"""

from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver
import logging

logger = logging.getLogger(__name__)

# Placeholder for future signal handlers
# Example:
# @receiver(post_save, sender=Connection)
# def connection_created(sender, instance, created, **kwargs):
#     if created:
#         logger.info(f"New connection created: {instance.id}")
