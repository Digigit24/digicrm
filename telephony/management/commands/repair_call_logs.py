"""Repair malformed TeleCMI recording references and resync call history."""
from django.core.management.base import BaseCommand, CommandError

from telephony.models import CallLog, TeleCMIAgent
from telephony.services.call_log_service import sync_cdr_for_agent


INVALID_REFERENCES = [
    'true', 'false', 'True', 'False', 'TRUE', 'FALSE',
    '1', '0', 'yes', 'no', 'Yes', 'No', 'YES', 'NO',
]


class Command(BaseCommand):
    help = 'Clear invalid recording flags, resync TeleCMI CDRs, and queue Zata archives.'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', help='Tenant UUID; omit to repair every active agent.')
        parser.add_argument('--days', type=int, default=90, help='History window to resync.')
        parser.add_argument('--no-archive', action='store_true', help='Do not queue Zata archives.')

    def handle(self, *args, **options):
        days = options['days']
        if days < 1 or days > 365:
            raise CommandError('--days must be between 1 and 365')

        logs = CallLog.objects.filter(recording_file__in=INVALID_REFERENCES)
        agents = TeleCMIAgent.objects.filter(is_active=True)
        if options.get('tenant'):
            logs = logs.filter(tenant_id=options['tenant'])
            agents = agents.filter(tenant_id=options['tenant'])

        cleared = logs.update(recording_file=None, recording_archive_error='')
        self.stdout.write(f'Cleared {cleared} invalid recording reference(s).')

        totals = {'created': 0, 'updated': 0, 'errors': 0}
        for agent in agents.values('tenant_id', 'user_id'):
            result = sync_cdr_for_agent(
                agent['tenant_id'],
                agent['user_id'],
                hours_back=days * 24,
                queue_archives=not options['no_archive'],
            )
            for key in totals:
                totals[key] += result.get(key, 0)
            self.stdout.write(
                f"{agent['tenant_id']} / {agent['user_id']}: {result.get('status', 'unknown')}"
            )

        self.stdout.write(self.style.SUCCESS(
            f"Repair complete: {totals['created']} created, {totals['updated']} updated, "
            f"{totals['errors']} errors."
        ))
