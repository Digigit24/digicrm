# Generated manually to match telephony/models.py — adds CallLog attribution/
# analytics fields, the three new indexes, and the TeleCMICampaign model.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('telephony', '0004_add_recording_file'),
    ]

    operations = [
        # ── CallLog: new fields ──────────────────────────────────────────
        migrations.AddField(
            model_name='calllog',
            name='call_leg',
            field=models.CharField(blank=True, help_text='"a" or "b" for outbound legs', max_length=1, null=True),
        ),
        migrations.AddField(
            model_name='calllog',
            name='telecmi_call_id',
            field=models.CharField(blank=True, db_index=True, help_text='Links Leg A and Leg B of outbound calls', max_length=64, null=True),
        ),
        migrations.AddField(
            model_name='calllog',
            name='conversation_uuid',
            field=models.CharField(blank=True, db_index=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name='calllog',
            name='ivr_name',
            field=models.CharField(blank=True, max_length=128, null=True),
        ),
        migrations.AddField(
            model_name='calllog',
            name='team_name',
            field=models.CharField(blank=True, max_length=128, null=True),
        ),
        migrations.AddField(
            model_name='calllog',
            name='is_voicemail',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='calllog',
            name='voicemail_filename',
            field=models.CharField(blank=True, max_length=256, null=True),
        ),
        migrations.AddField(
            model_name='calllog',
            name='wait_seconds',
            field=models.IntegerField(blank=True, help_text='Ring wait time for missed calls', null=True),
        ),
        migrations.AddField(
            model_name='calllog',
            name='hangup_reason',
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
        migrations.AddField(
            model_name='calllog',
            name='call_outcome',
            field=models.CharField(blank=True, help_text='Agent disposition: interested/not_interested/follow_up/callback/converted/dnd', max_length=32, null=True),
        ),
        migrations.AddField(
            model_name='calllog',
            name='call_outcome_note',
            field=models.CharField(blank=True, max_length=512, null=True),
        ),
        migrations.AddField(
            model_name='calllog',
            name='call_outcome_set_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        # Update help_text on the pre-existing agent_user_id field to document
        # that it deliberately carries no FK constraint (see models.py).
        migrations.AlterField(
            model_name='calllog',
            name='agent_user_id',
            field=models.UUIDField(
                blank=True, db_index=True, null=True,
                help_text='CRM user UUID who handled this call (no FK constraint — '
                           'user records live outside this app, same pattern as '
                           'sent_by_user_id / created_by_id elsewhere in this app)',
            ),
        ),

        # ── CallLog: new indexes ─────────────────────────────────────────
        migrations.AddIndex(
            model_name='calllog',
            index=models.Index(fields=['tenant_id', 'agent_user_id', 'call_time'], name='idx_tel_calls_agent_time'),
        ),
        migrations.AddIndex(
            model_name='calllog',
            index=models.Index(fields=['telecmi_call_id', 'call_leg'], name='idx_tel_calls_legs'),
        ),
        migrations.AddIndex(
            model_name='calllog',
            index=models.Index(fields=['conversation_uuid'], name='idx_tel_calls_conv'),
        ),

        # ── New model: TeleCMICampaign ───────────────────────────────────
        migrations.CreateModel(
            name='TeleCMICampaign',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tenant_id', models.UUIDField(db_index=True)),
                ('telecmi_campaign_id', models.CharField(blank=True, help_text='UUID from TeleCMI after creation', max_length=64, null=True)),
                ('name', models.CharField(max_length=255)),
                ('is_active', models.BooleanField(default=False)),
                ('timezone', models.CharField(default='Asia/Kolkata', max_length=64)),
                ('start_date', models.DateField()),
                ('end_date', models.DateField()),
                ('start_time', models.TimeField()),
                ('end_time', models.TimeField()),
                ('call_interval', models.IntegerField(choices=[(10, '10s'), (20, '20s'), (30, '30s'), (40, '40s'), (50, '50s'), (60, '1 min'), (120, '2 min')], default=30)),
                ('ring_rule', models.CharField(choices=[('ring-all', 'Ring All'), ('round-robin', 'Round Robin')], default='round-robin', max_length=20)),
                ('agent_user_ids', models.JSONField(default=list, help_text='CRM user UUIDs assigned to this campaign')),
                ('lead_count', models.IntegerField(default=0)),
                ('leads_called', models.IntegerField(default=0)),
                ('telecmi_lead_list_name', models.CharField(blank=True, max_length=255, null=True)),
                ('notes', models.TextField(blank=True)),
                ('created_by_id', models.UUIDField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'telephony_campaigns',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='telecmicampaign',
            index=models.Index(fields=['tenant_id'], name='idx_tel_camp_tenant'),
        ),
        migrations.AddIndex(
            model_name='telecmicampaign',
            index=models.Index(fields=['tenant_id', 'is_active'], name='idx_tel_camp_active'),
        ),
    ]
