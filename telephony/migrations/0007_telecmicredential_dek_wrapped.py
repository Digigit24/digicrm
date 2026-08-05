from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Add per-tenant envelope-encryption key storage to TeleCMI credentials.

    Nullable/blank by design: existing rows keep an empty value and continue to
    decrypt through the legacy shared-key path (see telephony.services.crypto),
    then upgrade in place the next time their secret is saved. No data
    migration, no downtime, no risk of orphaning a secret mid-deploy.
    """

    dependencies = [
        ('telephony', '0006_telecmicampaign_source_group'),
    ]

    operations = [
        migrations.AddField(
            model_name='telecmicredential',
            name='dek_wrapped',
            field=models.TextField(
                blank=True,
                default='',
                help_text=(
                    "This tenant's data-encryption key, itself encrypted with "
                    'TELECMI_MASTER_KEY. Empty means the row still uses the '
                    'legacy shared key.'
                ),
            ),
        ),
    ]
