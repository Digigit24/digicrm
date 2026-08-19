"""
Create or update a TeleCMI calling profile from the command line.

Why this exists
---------------
Calling profiles carry a SIP password, and until the admin UI ships there is no
way to enter one. This command is the unblock path: it gets a tenant onto a
working softphone in one invocation, and doubles as the recovery tool when a
password needs rotating out of hours.

The password is **prompted for**, never accepted as a flag. Anything passed in
argv lands in shell history, in `ps` output, and in any shell-recording tooling
on the box; a TeleCMI SIP secret should be in none of those. `--stdin-password`
exists for scripted use and reads one line from stdin, which has the same
property.

Usage
-----
    # Create (or update) the tenant's default calling profile.
    python manage.py set_calling_profile \\
        --tenant 7f240057-... --extension 5002_33338188 \\
        --label "Sales line" --caller-id 918000000000 --default

    # Rotate just the password on an existing profile.
    python manage.py set_calling_profile --tenant 7f240057-... \\
        --extension 5002_33338188

    # Change the label/caller ID without touching the password.
    python manage.py set_calling_profile --tenant 7f240057-... \\
        --extension 5002_33338188 --caller-id 918000000001 --keep-password

    # Point users at it in the same breath.
    python manage.py set_calling_profile --tenant 7f240057-... \\
        --extension 5002_33338188 --assign ee5840af-... --assign 1c2d3e4f-...

    # See what a tenant already has.
    python manage.py set_calling_profile --tenant 7f240057-... --list

Verification is on by default: the extension and password are logged into
TeleCMI and the result stored on the row. `--no-verify` skips the call for an
offline box; the profile is then saved flagged as unverified rather than
silently trusted.
"""
import getpass
import sys
import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from telephony.models import TeleCMICallingProfile, TeleCMIProfileAssignment
from telephony.services.crypto import encrypt_profile_password


class Command(BaseCommand):
    help = 'Create or update a TeleCMI calling profile (prompts for the password).'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', required=True, help='Tenant UUID.')
        parser.add_argument(
            '--extension',
            help='TeleCMI extension, e.g. 5002_33338188. Identifies the profile.',
        )
        parser.add_argument('--label', help='Human name, e.g. "Sales line".')
        parser.add_argument('--caller-id', help='PSTN number this profile presents.')
        parser.add_argument(
            '--default', action='store_true',
            help='Make this the tenant default (demotes any current default).',
        )
        parser.add_argument(
            '--inactive', action='store_true',
            help='Store the profile but keep it out of softphone resolution.',
        )
        parser.add_argument(
            '--keep-password', action='store_true',
            help='Update an existing profile without touching its password.',
        )
        parser.add_argument(
            '--stdin-password', action='store_true',
            help='Read the password from stdin instead of prompting (for scripts).',
        )
        parser.add_argument(
            '--no-verify', action='store_true',
            help='Skip the TeleCMI login check. The profile is flagged unverified.',
        )
        parser.add_argument(
            '--assign', action='append', default=[], metavar='USER_UUID',
            help='Assign this CRM user to the profile. Repeatable.',
        )
        parser.add_argument(
            '--list', action='store_true',
            help='List the tenant\'s calling profiles and exit.',
        )

    def handle(self, *args, **options):
        tenant_id = self._uuid(options['tenant'], '--tenant')

        if options['list']:
            self._list(tenant_id)
            return

        extension = (options.get('extension') or '').strip()
        if not extension:
            raise CommandError('--extension is required (or use --list).')

        profile = TeleCMICallingProfile.objects.filter(
            tenant_id=tenant_id, telecmi_user_id=extension
        ).first()
        creating = profile is None

        if creating and not options.get('label'):
            raise CommandError('--label is required when creating a new profile.')
        if creating and options['keep_password']:
            raise CommandError('--keep-password cannot be used when creating a profile.')

        password = None
        if not options['keep_password']:
            password = self._read_password(options['stdin_password'], creating)
            if not password:
                if creating:
                    raise CommandError('A password is required to create a profile.')
                raise CommandError(
                    'No password entered. Use --keep-password to leave the '
                    'stored one alone.'
                )

        assign_user_ids = [self._uuid(v, '--assign') for v in options['assign']]

        with transaction.atomic():
            if creating:
                profile = TeleCMICallingProfile(
                    tenant_id=tenant_id, telecmi_user_id=extension
                )

            if options.get('label'):
                profile.label = options['label']
            if options.get('caller_id') is not None and options['caller_id'] != '':
                if profile.caller_id != options['caller_id']:
                    # Force a fresh push to TeleCMI on the next session.
                    profile.caller_id_pushed_value = None
                profile.caller_id = options['caller_id']
            profile.is_active = not options['inactive']

            if password:
                encrypted, dek_wrapped = encrypt_profile_password(
                    password,
                    profile=None if creating else profile,
                    tenant_id=tenant_id,
                )
                profile.password_encrypted = encrypted
                profile.dek_wrapped = dek_wrapped
                # Minted from the old password — worthless now.
                profile.cached_token = None
                profile.token_obtained_at = None

            if options['default']:
                (TeleCMICallingProfile.objects
                 .filter(tenant_id=tenant_id, is_default=True)
                 .exclude(pk=profile.pk)
                 .update(is_default=False))
                profile.is_default = True

            profile.save()

            for user_id in assign_user_ids:
                TeleCMIProfileAssignment.objects.update_or_create(
                    tenant_id=tenant_id, user_id=user_id,
                    defaults={'profile': profile},
                )

        self.stdout.write(self.style.SUCCESS(
            '{} calling profile "{}" ({}) for tenant {}.'.format(
                'Created' if creating else 'Updated',
                profile.label, profile.telecmi_user_id, tenant_id,
            )
        ))
        if profile.is_default:
            self.stdout.write('  This is the tenant default profile.')
        if not profile.is_active:
            self.stdout.write(self.style.WARNING('  Profile is INACTIVE.'))
        for user_id in assign_user_ids:
            self.stdout.write('  Assigned user {}.'.format(user_id))

        self._verify(profile, password, skip=options['no_verify'])

    # ── helpers ──────────────────────────────────────────────

    def _verify(self, profile, password, skip):
        from telephony.serializers import verify_profile

        if skip:
            profile.verified_at = None
            profile.verify_error = 'Saved with --no-verify; not checked against TeleCMI.'
            profile.save(update_fields=['verified_at', 'verify_error', 'updated_at'])
            self.stdout.write(self.style.WARNING(
                '  Not verified (--no-verify).'
            ))
            return

        ok, error = verify_profile(profile, password, save=True)
        if ok:
            self.stdout.write(self.style.SUCCESS('  TeleCMI accepted this extension.'))
        else:
            self.stdout.write(self.style.WARNING('  {}'.format(error)))

    def _read_password(self, from_stdin, creating):
        if from_stdin:
            return sys.stdin.readline().rstrip('\n')

        prompt = (
            'TeleCMI password for this extension: ' if creating
            else 'New TeleCMI password (blank to abort): '
        )
        first = getpass.getpass(prompt)
        if not first:
            return ''
        second = getpass.getpass('Confirm password: ')
        if first != second:
            raise CommandError('Passwords did not match.')
        return first

    def _list(self, tenant_id):
        profiles = TeleCMICallingProfile.objects.filter(tenant_id=tenant_id)
        if not profiles:
            self.stdout.write(self.style.WARNING(
                'No calling profiles for tenant {}.'.format(tenant_id)
            ))
            return

        assignments = {}
        for row in TeleCMIProfileAssignment.objects.filter(tenant_id=tenant_id):
            assignments.setdefault(row.profile_id, []).append(str(row.user_id))

        self.stdout.write(self.style.MIGRATE_HEADING(
            'Calling profiles for tenant {}'.format(tenant_id)
        ))
        for profile in profiles:
            flags = []
            if profile.is_default:
                flags.append('DEFAULT')
            if not profile.is_active:
                flags.append('INACTIVE')
            if not profile.password_encrypted:
                flags.append('NO PASSWORD')
            if profile.verify_error:
                flags.append('UNVERIFIED')
            suffix = '  [{}]'.format(', '.join(flags)) if flags else ''
            self.stdout.write('  #{} {} — ext {} — caller id {}{}'.format(
                profile.id, profile.label, profile.telecmi_user_id,
                profile.caller_id or '(none)', suffix,
            ))
            if profile.verify_error:
                self.stdout.write('      {}'.format(profile.verify_error))
            for user_id in assignments.get(profile.id, []):
                self.stdout.write('      user {}'.format(user_id))

    def _uuid(self, value, flag):
        try:
            return uuid.UUID(str(value))
        except (ValueError, AttributeError, TypeError):
            raise CommandError('{} must be a UUID, got {!r}'.format(flag, value))
