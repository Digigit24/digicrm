from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    """
    Configuration for the Integrations app.
    Handles third-party integrations like Google Sheets, Webhooks, etc.
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'integrations'
    verbose_name = 'Integrations & Automation'

    def ready(self):
        """
        Import signals when the app is ready.
        This ensures all signal handlers are registered.
        """
        try:
            import integrations.signals  # noqa
        except ImportError:
            pass
