#!/usr/bin/env python
"""
Script to create Google Sheets integration
Run this with: python create_google_integration.py
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'digicrm.settings')
django.setup()

from integrations.models import Integration, IntegrationTypeEnum

# Check existing integrations
integrations = Integration.objects.all()
print(f"Total integrations: {integrations.count()}")
for integration in integrations:
    print(f"  - ID: {integration.id}, Name: {integration.name}, Type: {integration.type}, Active: {integration.is_active}")

# Create Google Sheets integration if it doesn't exist
if not Integration.objects.filter(type=IntegrationTypeEnum.GOOGLE_SHEETS).exists():
    print("\nCreating Google Sheets integration...")
    google_integration = Integration.objects.create(
        name='Google Sheets',
        type=IntegrationTypeEnum.GOOGLE_SHEETS,
        description='Connect Google Sheets to automatically import leads from your forms',
        is_active=True,
        requires_oauth=True,
    )
    print(f"✓ Created: ID={google_integration.id}, Name={google_integration.name}, Type={google_integration.type}")
else:
    google_integration = Integration.objects.filter(type=IntegrationTypeEnum.GOOGLE_SHEETS).first()
    print(f"\n✓ Google Sheets integration already exists: ID={google_integration.id}")

print("\n" + "="*50)
print("FINAL INTEGRATION LIST:")
print("="*50)
for integration in Integration.objects.all():
    print(f"ID: {integration.id} | Name: {integration.name} | Type: {integration.type} | Active: {integration.is_active}")
