"""
Migration: Add database indexes on Lead fields used in search & list queries.

Search fields: name, phone, email (icontains / trigram search)
Filter fields: status_id, owner_user_id, priority, created_at, next_follow_up_at
Tenant isolation: tenant_id already has an index via ForeignKey — this adds
a composite (tenant_id, created_at) covering index for the default ordering.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0004_lead_groups"),
    ]

    operations = [
        # Composite covering index for the default list query:
        #   WHERE tenant_id = %s ORDER BY created_at DESC
        migrations.AddIndex(
            model_name="lead",
            index=models.Index(
                fields=["tenant_id", "-created_at"],
                name="lead_tenant_created_idx",
            ),
        ),
        # Search on name (LIKE '%...%') — helps with left-anchored searches
        migrations.AddIndex(
            model_name="lead",
            index=models.Index(
                fields=["tenant_id", "name"],
                name="lead_tenant_name_idx",
            ),
        ),
        # Search on phone
        migrations.AddIndex(
            model_name="lead",
            index=models.Index(
                fields=["tenant_id", "phone"],
                name="lead_tenant_phone_idx",
            ),
        ),
        # Search on email
        migrations.AddIndex(
            model_name="lead",
            index=models.Index(
                fields=["tenant_id", "email"],
                name="lead_tenant_email_idx",
            ),
        ),
        # Filter by status
        migrations.AddIndex(
            model_name="lead",
            index=models.Index(
                fields=["tenant_id", "status_id"],
                name="lead_tenant_status_idx",
            ),
        ),
        # Filter by owner
        migrations.AddIndex(
            model_name="lead",
            index=models.Index(
                fields=["tenant_id", "owner_user_id"],
                name="lead_tenant_owner_idx",
            ),
        ),
        # Follow-up scheduling queries
        migrations.AddIndex(
            model_name="lead",
            index=models.Index(
                fields=["tenant_id", "next_follow_up_at"],
                name="lead_tenant_followup_idx",
            ),
        ),
    ]
