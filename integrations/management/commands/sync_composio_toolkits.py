"""
Refresh the local Composio toolkit catalogue cache.

    python manage.py sync_composio_toolkits
    python manage.py sync_composio_toolkits --enable GMAIL NOTION GOOGLEDRIVE GOOGLECALENDAR

Composio has hundreds of toolkits; fetching them on every catalogue page load
would be slow and burn rate limit. This command (and its Celery beat twin,
``integrations.tasks.sync_composio_toolkits``) keeps ``composio_toolkits`` in
step. The operator-owned flags is_enabled / is_featured / sort_order are never
overwritten by the sync itself - use ``--enable`` to opt toolkits in.
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from integrations.models import ComposioToolkit
from integrations.services.composio_client import ComposioError, ComposioNotConfigured
from integrations.services.composio_sync import sync_toolkit_catalogue


class Command(BaseCommand):
    help = 'Sync the Composio toolkit catalogue into the local ComposioToolkit cache'

    def add_arguments(self, parser):
        parser.add_argument(
            '--enable', nargs='*', default=None, metavar='SLUG',
            help='Toolkit slugs to mark is_enabled=True and is_featured=True after syncing.',
        )
        parser.add_argument(
            '--skip-fetch', action='store_true',
            help='Do not call Composio; only apply --enable to already cached rows.',
        )

    def handle(self, *args, **options):
        if not options['skip_fetch']:
            try:
                touched = sync_toolkit_catalogue()
            except ComposioNotConfigured as exc:
                raise CommandError(str(exc))
            except ComposioError as exc:
                raise CommandError(f'Composio sync failed: {exc}')
            self.stdout.write(self.style.SUCCESS(f'Synced {touched} toolkit rows'))

        slugs = [s.upper() for s in (options['enable'] or [])]
        if slugs:
            missing = set(slugs) - set(
                ComposioToolkit.objects.filter(slug__in=slugs).values_list('slug', flat=True)
            )
            if missing:
                self.stdout.write(self.style.WARNING(
                    f'Not in the catalogue, skipped: {", ".join(sorted(missing))}'
                ))
            updated = ComposioToolkit.objects.filter(slug__in=slugs).update(
                is_enabled=True, is_featured=True, updated_at=timezone.now()
            )
            self.stdout.write(self.style.SUCCESS(f'Enabled + featured {updated} toolkits'))

        total = ComposioToolkit.objects.count()
        enabled = ComposioToolkit.objects.filter(is_enabled=True).count()
        self.stdout.write(f'Catalogue: {total} toolkits cached, {enabled} enabled for tenants')
