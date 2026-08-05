from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ('crm', '0006_leadactivity_add_real_estate_type'),
    ]

    operations = [
        migrations.CreateModel(
            name='Reminder',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('tenant_id', models.UUIDField(db_index=True)),
                ('recipient_user_id', models.UUIDField(db_index=True)),
                ('created_by_user_id', models.UUIDField(db_index=True)),
                ('follow_up_at', models.DateTimeField()),
                ('remind_at', models.DateTimeField(db_index=True)),
                ('offset_minutes', models.PositiveIntegerField(default=0)),
                ('status', models.CharField(choices=[('PENDING', 'Pending'), ('PROCESSING', 'Processing'), ('DELIVERED', 'Delivered'), ('CANCELLED', 'Cancelled'), ('MISSED', 'Missed')], db_index=True, default='PENDING', max_length=20)),
                ('attempt_count', models.PositiveIntegerField(default=0)),
                ('locked_at', models.DateTimeField(blank=True, null=True)),
                ('delivered_at', models.DateTimeField(blank=True, null=True)),
                ('cancelled_at', models.DateTimeField(blank=True, null=True)),
                ('last_error', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('lead', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reminders', to='crm.lead')),
            ],
            options={'db_table': 'crm_reminders'},
        ),
        migrations.CreateModel(
            name='Notification',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('tenant_id', models.UUIDField(db_index=True)),
                ('recipient_user_id', models.UUIDField(db_index=True)),
                ('notification_type', models.CharField(default='FOLLOW_UP_REMINDER', max_length=40)),
                ('title', models.CharField(max_length=200)),
                ('body', models.TextField(blank=True, default='')),
                ('lead_name_snapshot', models.CharField(blank=True, default='', max_length=255)),
                ('action_url', models.CharField(blank=True, default='', max_length=500)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('dedupe_key', models.CharField(max_length=160, unique=True)),
                ('seen_at', models.DateTimeField(blank=True, null=True)),
                ('read_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('lead', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='notifications', to='crm.lead')),
                ('reminder', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='notification', to='notifications.reminder')),
            ],
            options={'db_table': 'crm_notifications', 'ordering': ['-created_at']},
        ),
        migrations.AddIndex(model_name='reminder', index=models.Index(fields=['tenant_id', 'status', 'remind_at'], name='idx_reminder_due')),
        migrations.AddIndex(model_name='reminder', index=models.Index(fields=['tenant_id', 'recipient_user_id', 'status'], name='idx_reminder_recipient')),
        migrations.AddConstraint(model_name='reminder', constraint=models.UniqueConstraint(condition=models.Q(('status__in', ['PENDING', 'PROCESSING'])), fields=('tenant_id', 'lead', 'recipient_user_id'), name='uniq_active_lead_reminder')),
        migrations.AddIndex(model_name='notification', index=models.Index(fields=['tenant_id', 'recipient_user_id', 'read_at', '-created_at'], name='idx_notification_inbox')),
    ]

