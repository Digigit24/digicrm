"""
Create the platform-wide, Composio-managed auth configs for our toolkits.

    python manage.py bootstrap_composio_auth_configs
    python manage.py bootstrap_composio_auth_configs --toolkits GMAIL NOTION

Idempotent: an existing ComposioAuthConfig for a toolkit is left alone. Each
created row is a Composio-managed auth config, meaning Composio owns the OAuth
app and we never hold a client secret for it.

Run AFTER ``sync_composio_toolkits`` so the catalogue is populated and the
managed-auth check can run against real data.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from integrations.models import ComposioAuthConfig
from integrations.services.composio_client import ComposioError, ComposioNotConfigured
from integrations.services.composio_sync import ensure_auth_config


class Command(BaseCommand):
    help = 'Idempotently create the platform-wide Composio auth configs for the priority toolkits'

    def add_arguments(self, parser):
        parser.add_argument(
            '--toolkits', nargs='*', default=None, metavar='SLUG',
            help='Toolkit slugs. Defaults to settings.COMPOSIO_PRIORITY_TOOLKITS.',
        )

    def handle(self, *args, **options):
        slugs = [s.upper() for s in (options['toolkits']
                                     or getattr(settings, 'COMPOSIO_PRIORITY_TOOLKITS', []))]
        if not slugs:
            raise CommandError('No toolkits given and COMPOSIO_PRIORITY_TOOLKITS is empty')

        failures = 0
        for slug in slugs:
            existing = ComposioAuthConfig.objects.filter(
                toolkit_slug=slug, tenant_id__isnull=True
            ).first()
            if existing:
                self.stdout.write(self.style.SUCCESS(
                    f'{slug}: already configured ({existing.auth_config_id})'
                ))
                continue
            try:
                config = ensure_auth_config(slug)
            except ComposioNotConfigured as exc:
                raise CommandError(str(exc))
            except ComposioError as exc:
                failures += 1
                self.stdout.write(self.style.ERROR(f'{slug}: {exc}'))
                continue
            self.stdout.write(self.style.SUCCESS(
                f'{slug}: created {config.auth_config_id}'
            ))

        if failures:
            raise CommandError(f'{failures} toolkit(s) failed; see errors above')
