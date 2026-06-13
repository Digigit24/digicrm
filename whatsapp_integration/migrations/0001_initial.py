from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('crm', '0004_lead_groups'),
    ]

    operations = [
        migrations.CreateModel(
            name='WhatsAppVendorConfig',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('tenant_id', models.UUIDField(db_index=True, unique=True)),
                ('vendor_uid', models.CharField(max_length=100, help_text='Laravel vendor _uid')),
                ('api_token', models.TextField(help_text='Laravel vendor API access token')),
                ('api_base_url', models.TextField(default='https://whatsappapi.celiyo.com/api')),
                ('webhook_secret', models.TextField(blank=True, null=True)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'db_table': 'whatsapp_vendor_configs'},
        ),
        migrations.CreateModel(
            name='WhatsAppSequence',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('tenant_id', models.UUIDField(db_index=True)),
                ('name', models.TextField()),
                ('description', models.TextField(blank=True, null=True)),
                ('is_active', models.BooleanField(default=True)),
                ('stop_on_reply', models.BooleanField(default=True)),
                ('created_by', models.UUIDField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'whatsapp_sequences',
                'ordering': ['name'],
            },
        ),
        migrations.AddConstraint(
            model_name='whatsappsequence',
            constraint=models.UniqueConstraint(
                fields=['tenant_id', 'name'],
                name='unique_sequence_name_per_tenant'
            ),
        ),
        migrations.AddIndex(
            model_name='whatsappsequence',
            index=models.Index(fields=['tenant_id'], name='idx_wa_sequences_tenant'),
        ),
        migrations.CreateModel(
            name='WhatsAppSequenceStep',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('sequence', models.ForeignKey(
                    db_column='sequence_id',
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='steps',
                    to='whatsapp_integration.whatsappsequence',
                )),
                ('step_number', models.IntegerField()),
                ('delay_days', models.IntegerField(default=0)),
                ('template_uid', models.CharField(max_length=100)),
                ('template_name', models.TextField(blank=True, null=True)),
                ('template_variable_mapping', models.JSONField(default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'whatsapp_sequence_steps',
                'ordering': ['step_number'],
            },
        ),
        migrations.AlterUniqueTogether(
            name='whatsappsequencestep',
            unique_together={('sequence', 'step_number')},
        ),
        migrations.AddIndex(
            model_name='whatsappsequencestep',
            index=models.Index(fields=['sequence'], name='idx_wa_seq_steps_seq'),
        ),
        migrations.CreateModel(
            name='WhatsAppCampaign',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('tenant_id', models.UUIDField(db_index=True)),
                ('name', models.TextField()),
                ('lead_group', models.ForeignKey(
                    blank=True, null=True,
                    db_column='lead_group_id',
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='whatsapp_campaigns',
                    to='crm.leadgroup',
                )),
                ('template_uid', models.CharField(max_length=100)),
                ('template_name', models.TextField(blank=True, null=True)),
                ('template_components', models.JSONField(default=list)),
                ('status', models.CharField(
                    choices=[
                        ('DRAFT', 'Draft'), ('SCHEDULED', 'Scheduled'),
                        ('RUNNING', 'Running'), ('COMPLETED', 'Completed'), ('FAILED', 'Failed'),
                    ],
                    default='DRAFT', max_length=20,
                )),
                ('scheduled_at', models.DateTimeField(blank=True, null=True)),
                ('launched_at', models.DateTimeField(blank=True, null=True)),
                ('laravel_campaign_uid', models.CharField(blank=True, db_index=True, max_length=100, null=True)),
                ('laravel_group_uid', models.CharField(blank=True, max_length=100, null=True)),
                ('total_contacts', models.IntegerField(default=0)),
                ('created_by', models.UUIDField(db_index=True)),
                ('notes', models.TextField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'whatsapp_campaigns',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='whatsappcampaign',
            index=models.Index(fields=['tenant_id'], name='idx_wa_campaigns_tenant'),
        ),
        migrations.AddIndex(
            model_name='whatsappcampaign',
            index=models.Index(fields=['status'], name='idx_wa_campaigns_status'),
        ),
        migrations.AddIndex(
            model_name='whatsappcampaign',
            index=models.Index(fields=['scheduled_at'], name='idx_wa_campaigns_scheduled'),
        ),
        migrations.CreateModel(
            name='LeadSequenceEnrollment',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('tenant_id', models.UUIDField(db_index=True)),
                ('lead', models.ForeignKey(
                    db_column='lead_id',
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='sequence_enrollments',
                    to='crm.lead',
                )),
                ('sequence', models.ForeignKey(
                    db_column='sequence_id',
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='enrollments',
                    to='whatsapp_integration.whatsappsequence',
                )),
                ('current_step', models.ForeignKey(
                    blank=True, null=True,
                    db_column='current_step_id',
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='active_enrollments',
                    to='whatsapp_integration.whatsappsequencestep',
                )),
                ('status', models.CharField(
                    choices=[
                        ('ACTIVE', 'Active'), ('PAUSED', 'Paused'),
                        ('COMPLETED', 'Completed'), ('OPTED_OUT', 'Opted Out'),
                        ('REPLIED', 'Replied — stopped on reply'),
                    ],
                    default='ACTIVE', max_length=20,
                )),
                ('enrolled_at', models.DateTimeField(auto_now_add=True)),
                ('next_step_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('stopped_reason', models.TextField(blank=True, null=True)),
                ('enrolled_by', models.UUIDField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'lead_sequence_enrollments',
                'ordering': ['-enrolled_at'],
            },
        ),
        migrations.AlterUniqueTogether(
            name='leadsequenceenrollment',
            unique_together={('lead', 'sequence')},
        ),
        migrations.AddIndex(
            model_name='leadsequenceenrollment',
            index=models.Index(fields=['tenant_id'], name='idx_lse_tenant'),
        ),
        migrations.AddIndex(
            model_name='leadsequenceenrollment',
            index=models.Index(fields=['status'], name='idx_lse_status'),
        ),
        migrations.AddIndex(
            model_name='leadsequenceenrollment',
            index=models.Index(fields=['next_step_at'], name='idx_lse_next_step_at'),
        ),
        migrations.AddIndex(
            model_name='leadsequenceenrollment',
            index=models.Index(fields=['lead'], name='idx_lse_lead'),
        ),
        migrations.CreateModel(
            name='AgentActionLog',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('tenant_id', models.UUIDField(db_index=True)),
                ('action_type', models.CharField(
                    choices=[
                        ('SEND_WHATSAPP', 'Send WhatsApp'),
                        ('ENROLL_SEQUENCE', 'Enroll in Sequence'),
                        ('CREATE_CAMPAIGN', 'Create Campaign'),
                        ('UPDATE_LEAD_STATUS', 'Update Lead Status'),
                        ('LOG_ACTIVITY', 'Log Activity'),
                    ],
                    max_length=30,
                )),
                ('payload_in', models.JSONField()),
                ('payload_out', models.JSONField(blank=True, null=True)),
                ('triggered_by', models.CharField(default='claude-agent', max_length=100)),
                ('status', models.CharField(
                    choices=[('SUCCESS', 'Success'), ('FAILED', 'Failed')],
                    default='SUCCESS', max_length=10,
                )),
                ('error_message', models.TextField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'agent_action_logs',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='agentactionlog',
            index=models.Index(fields=['tenant_id'], name='idx_aal_tenant'),
        ),
        migrations.AddIndex(
            model_name='agentactionlog',
            index=models.Index(fields=['action_type'], name='idx_aal_action_type'),
        ),
        migrations.AddIndex(
            model_name='agentactionlog',
            index=models.Index(fields=['created_at'], name='idx_aal_created_at'),
        ),
    ]
