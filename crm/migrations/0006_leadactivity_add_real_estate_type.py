from django.db import migrations, models


class Migration(migrations.Migration):
    """Add the REAL_ESTATE choice to LeadActivity.type (choices metadata only, no schema change)."""

    dependencies = [
        ("crm", "0005_lead_search_indexes"),
    ]

    operations = [
        migrations.AlterField(
            model_name="leadactivity",
            name="type",
            field=models.CharField(
                max_length=20,
                choices=[
                    ("CALL", "Call"),
                    ("EMAIL", "Email"),
                    ("MEETING", "Meeting"),
                    ("NOTE", "Note"),
                    ("SMS", "SMS"),
                    ("REAL_ESTATE", "Real Estate"),
                    ("OTHER", "Other"),
                ],
            ),
        ),
    ]
