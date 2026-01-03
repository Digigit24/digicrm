from django.core.management.base import BaseCommand
from integrations.models import Integration, IntegrationTypeEnum


class Command(BaseCommand):
    help = 'Create Google Sheets integration if it does not exist'

    def handle(self, *args, **options):
        # Check existing integrations
        integrations = Integration.objects.all()
        self.stdout.write(f"Total integrations: {integrations.count()}")
        for integration in integrations:
            self.stdout.write(
                f"  - ID: {integration.id}, Name: {integration.name}, "
                f"Type: {integration.type}, Active: {integration.is_active}"
            )

        # Create Google Sheets integration if it doesn't exist
        if not Integration.objects.filter(type=IntegrationTypeEnum.GOOGLE_SHEETS).exists():
            self.stdout.write("\nCreating Google Sheets integration...")
            google_integration = Integration.objects.create(
                name='Google Sheets',
                type=IntegrationTypeEnum.GOOGLE_SHEETS,
                description='Connect Google Sheets to automatically import leads from your forms',
                is_active=True,
                requires_oauth=True,
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f'✓ Created: ID={google_integration.id}, Name={google_integration.name}, '
                    f'Type={google_integration.type}'
                )
            )
        else:
            google_integration = Integration.objects.filter(
                type=IntegrationTypeEnum.GOOGLE_SHEETS
            ).first()
            self.stdout.write(
                self.style.SUCCESS(
                    f'✓ Google Sheets integration already exists: ID={google_integration.id}'
                )
            )

        self.stdout.write("\n" + "="*50)
        self.stdout.write("FINAL INTEGRATION LIST:")
        self.stdout.write("="*50)
        for integration in Integration.objects.all():
            self.stdout.write(
                f"ID: {integration.id} | Name: {integration.name} | "
                f"Type: {integration.type} | Active: {integration.is_active}"
            )
