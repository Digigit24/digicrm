"""
Diagnose (and repair) TeleCMI credential encryption for a tenant.

Background
----------
TeleCMI credentials use envelope encryption (telephony/services/crypto.py):

  * each tenant has its own Fernet data key (DEK), stored on the credential row
    in wrapped form — never in plaintext, never typed by a human;
  * one master key (KEK) from `TELECMI_MASTER_KEY` wraps every tenant's DEK.

So `TELECMI_MASTER_KEY` must be identical in every deployment that shares a
database. When it is not — or when it is unset, in which case the code falls
back to `SECRET_KEY` — secrets saved by one environment are unreadable by the
others, and the recording endpoint reports
"TeleCMI credentials cannot be decrypted".

Rows written before envelope encryption have an empty `dek_wrapped` and still
decrypt through the legacy shared key. They upgrade in place the next time the
secret is saved; this command reports them as LEGACY.

Usage
-----
    # Which key is this environment using, and can it read what's stored?
    python manage.py telecmi_doctor

    # One tenant only
    python manage.py telecmi_doctor --tenant <uuid>

    # Generate a master key to put in every environment's .env
    python manage.py telecmi_doctor --new-master-key

    # Re-encrypt a tenant's secret with the current master key
    python manage.py telecmi_doctor --tenant <uuid> --set-secret <app-secret>

    # Move every legacy row onto envelope encryption (needs the OLD SECRET_KEY
    # still configured so the current secrets can be read first)
    python manage.py telecmi_doctor --upgrade-legacy
"""
from cryptography.fernet import Fernet
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from integrations.utils.encryption import EncryptionError
from telephony.models import TeleCMICredential
from telephony.services.crypto import decrypt_secret, encrypt_secret, needs_upgrade


class Command(BaseCommand):
    help = 'Diagnose and repair TeleCMI credential encryption.'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', help='Limit to a single tenant UUID.')
        parser.add_argument(
            '--new-master-key',
            action='store_true',
            help='Print a freshly generated TELECMI_MASTER_KEY and exit.',
        )
        parser.add_argument(
            '--set-secret',
            help='Re-encrypt this tenant credential with the given plaintext app '
                 'secret. Requires --tenant.',
        )
        parser.add_argument(
            '--upgrade-legacy',
            action='store_true',
            help='Re-encrypt every readable legacy row with per-tenant keys.',
        )

    def handle(self, *args, **options):
        if options['new_master_key']:
            self.stdout.write(
                self.style.SUCCESS(
                    f'TELECMI_MASTER_KEY={Fernet.generate_key().decode()}'
                )
            )
            self.stdout.write(
                '\nPut this identical value in every environment that shares '
                'this database, then restart.\n'
            )
            return

        self._report_config()

        if options['set_secret']:
            if not options['tenant']:
                raise CommandError('--set-secret requires --tenant')
            self._set_secret(options['tenant'], options['set_secret'])

        if options['upgrade_legacy']:
            self._upgrade_legacy()

        self._report_rows(options.get('tenant'))

    # ──────────────────────────────────────────────────────────

    def _report_config(self):
        self.stdout.write(self.style.MIGRATE_HEADING('Master key'))
        if getattr(settings, 'TELECMI_MASTER_KEY', None):
            self.stdout.write(self.style.SUCCESS('  TELECMI_MASTER_KEY is set.'))
        elif getattr(settings, 'INTEGRATION_ENCRYPTION_KEY', None):
            self.stdout.write(
                self.style.WARNING(
                    '  Using INTEGRATION_ENCRYPTION_KEY as the master key. '
                    'Works, but set TELECMI_MASTER_KEY explicitly.'
                )
            )
        else:
            self.stdout.write(
                self.style.ERROR(
                    '  NOT SET — falling back to SECRET_KEY. Any environment on '
                    'this database with a different SECRET_KEY will fail to read '
                    'TeleCMI secrets. Run --new-master-key and set it everywhere.'
                )
            )

    def _set_secret(self, tenant_id, secret):
        try:
            credential = TeleCMICredential.objects.get(tenant_id=tenant_id)
        except TeleCMICredential.DoesNotExist:
            raise CommandError(f'No TeleCMI credential row for tenant {tenant_id}')

        # Pass the existing wrapper only if it can still be unwrapped; otherwise
        # mint a fresh key rather than failing on an unusable one.
        existing = credential.dek_wrapped
        try:
            encrypted, dek_wrapped = encrypt_secret(secret, existing)
        except EncryptionError:
            self.stdout.write(
                self.style.WARNING(
                    '  Existing tenant key is unreadable with this master key — '
                    'minting a new one.'
                )
            )
            encrypted, dek_wrapped = encrypt_secret(secret)

        credential.secret_encrypted = encrypted
        credential.dek_wrapped = dek_wrapped
        credential.save(update_fields=['secret_encrypted', 'dek_wrapped', 'updated_at'])
        self.stdout.write(
            self.style.SUCCESS(f'\nRe-encrypted secret for tenant {tenant_id}.')
        )

    def _upgrade_legacy(self):
        self.stdout.write(self.style.MIGRATE_HEADING('\nUpgrading legacy rows'))
        upgraded = failed = 0
        for credential in TeleCMICredential.objects.all():
            if not needs_upgrade(credential):
                continue
            try:
                plaintext = decrypt_secret(credential)
            except EncryptionError as exc:
                self.stdout.write(
                    self.style.ERROR(f'  {credential.tenant_id}: unreadable ({exc})')
                )
                failed += 1
                continue
            encrypted, dek_wrapped = encrypt_secret(plaintext)
            credential.secret_encrypted = encrypted
            credential.dek_wrapped = dek_wrapped
            credential.save(
                update_fields=['secret_encrypted', 'dek_wrapped', 'updated_at']
            )
            self.stdout.write(self.style.SUCCESS(f'  {credential.tenant_id}: upgraded'))
            upgraded += 1
        self.stdout.write(f'  {upgraded} upgraded, {failed} still need a re-entered secret.')

    def _report_rows(self, tenant_id=None):
        qs = TeleCMICredential.objects.all()
        if tenant_id:
            qs = qs.filter(tenant_id=tenant_id)

        self.stdout.write(self.style.MIGRATE_HEADING('\nStored credentials'))
        if not qs.exists():
            self.stdout.write(self.style.WARNING('  (none)'))
            return

        for credential in qs.order_by('tenant_id'):
            label = f'  {credential.tenant_id}  app_id={credential.app_id}'
            scheme = 'legacy' if needs_upgrade(credential) else 'envelope'
            if not credential.secret_encrypted:
                self.stdout.write(self.style.WARNING(f'{label}  EMPTY secret'))
                continue
            try:
                plaintext = decrypt_secret(credential)
            except EncryptionError as exc:
                self.stdout.write(
                    self.style.ERROR(f'{label}  [{scheme}]  UNREADABLE — {exc}')
                )
                continue
            masked = (
                plaintext[:3] + '…' + plaintext[-2:] if len(plaintext) > 6 else '(short)'
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f'{label}  [{scheme}]  ok  active={credential.is_active}  secret={masked}'
                )
            )
