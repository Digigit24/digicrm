from django.apps import AppConfig


class CommonConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'common'

    def ready(self):
        """Register drf-spectacular schema extensions for custom auth classes."""
        import common.schema  # noqa: F401
