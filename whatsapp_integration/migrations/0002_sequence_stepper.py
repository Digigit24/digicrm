"""
Backing store for the WhatsApp sequence stepper (audit P0-4) and the M5
partial index.

Strictly additive: one new table, four new nullable/defaulted columns, two new
indexes and one new unique constraint. Nothing is dropped, renamed or retyped,
so it is safe to apply to the live database ahead of the code that uses it.

The autodetector also wanted to emit ~25 ``AlterField`` operations (help_text
edits that predate this work) and a ``crm`` migration dropping seven Lead
indexes. Both are pre-existing model/DB drift, unrelated to the stepper, and
the index drops are destructive -- they are deliberately NOT in here.
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0006_leadactivity_add_real_estate_type'),
        ('whatsapp_integration', '0001_initial'),
    ]

    operations = [
        # ── Enrollment: claim marker, retry accounting, re-enroll run counter ──
        migrations.AddField(
            model_name='leadsequenceenrollment',
            name='run_number',
            field=models.IntegerField(default=1, help_text='Incremented every time a stopped enrollment is re-enrolled. Part of the per-step send marker key, so a re-enrolled lead legitimately receives step 1 again while a single run still sends each step at most once.'),
        ),
        migrations.AddField(
            model_name='leadsequenceenrollment',
            name='locked_at',
            field=models.DateTimeField(blank=True, help_text='Set by the Celery stepper when it claims this row. Non-null means a worker owns it; a claim older than WHATSAPP_SEQUENCE_CLAIM_STALE_MINUTES is released.', null=True),
        ),
        migrations.AddField(
            model_name='leadsequenceenrollment',
            name='attempt_count',
            field=models.IntegerField(default=0, help_text='Consecutive failed send attempts for the CURRENT step. Reset to 0 after a successful send.'),
        ),
        migrations.AddField(
            model_name='leadsequenceenrollment',
            name='last_error',
            field=models.TextField(blank=True, default='', help_text='Why the most recent send attempt failed.'),
        ),

        # ── Audit M5: partial index for the every-60s due-enrollment poll ──
        migrations.AddIndex(
            model_name='leadsequenceenrollment',
            index=models.Index(condition=models.Q(('next_step_at__isnull', False), ('status', 'ACTIVE')), fields=['next_step_at'], name='idx_lse_due_active'),
        ),

        # ── Per-(enrollment, step, run) send marker: the idempotency key ──
        migrations.CreateModel(
            name='SequenceStepDelivery',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('tenant_id', models.UUIDField(db_index=True)),
                ('run_number', models.IntegerField(default=1, help_text='LeadSequenceEnrollment.run_number at the time of the send.')),
                ('status', models.CharField(choices=[('SENDING', 'Sending — claimed, outcome not yet known'), ('SENT', 'Sent'), ('FAILED', 'Failed — safe to retry'), ('UNKNOWN', 'Unknown — worker died mid-send, never retried')], default='SENDING', max_length=20)),
                ('attempt', models.IntegerField(default=1)),
                ('template_uid', models.CharField(blank=True, default='', max_length=100)),
                ('wa_message_id', models.TextField(blank=True, default='')),
                ('send_mode', models.CharField(blank=True, default='TEMPLATE', help_text='TEMPLATE -- sequence steps are always template sends, which is what keeps them legal outside the 24h session window.', max_length=16)),
                ('reply_window_open', models.BooleanField(blank=True, help_text='Canonical reply_window.open at send time. Null = unknown (the window lookup itself failed).', null=True)),
                ('reply_window_expires_at', models.TextField(blank=True, default='', help_text='Canonical reply_window.expires_at (ISO-8601) at send time.')),
                ('last_error', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('enrollment', models.ForeignKey(db_column='enrollment_id', on_delete=django.db.models.deletion.CASCADE, related_name='deliveries', to='whatsapp_integration.leadsequenceenrollment')),
                ('step', models.ForeignKey(db_column='step_id', on_delete=django.db.models.deletion.CASCADE, related_name='deliveries', to='whatsapp_integration.whatsappsequencestep')),
            ],
            options={
                'db_table': 'whatsapp_sequence_step_deliveries',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='sequencestepdelivery',
            index=models.Index(fields=['status'], name='idx_wssd_status'),
        ),
        # The hard at-most-once guarantee. Even if the row lock and the
        # locked_at claim both failed, two workers could not both insert.
        migrations.AddConstraint(
            model_name='sequencestepdelivery',
            constraint=models.UniqueConstraint(fields=('enrollment', 'step', 'run_number'), name='unique_delivery_per_enrollment_step_run'),
        ),
    ]
