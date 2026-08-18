from django.apps import AppConfig


class SchedulingAppConfig(AppConfig):
    """Calendar / scheduling app.

    NOTE: the Python package is deliberately named ``scheduling`` and NOT
    ``calendar`` -- a top-level ``calendar`` package shadows the stdlib
    ``calendar`` module that Django itself imports (``django.utils.dateformat``,
    ``django.utils.timezone``), which breaks the project at import time.
    The URL prefix is still ``/api/calendar/``.
    """

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'scheduling'
    verbose_name = 'Calendar & Scheduling'
