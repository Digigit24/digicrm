from django.db import migrations, models


def clear_invalid_recording_flags(apps, schema_editor):
    CallLog = apps.get_model('telephony', 'CallLog')
    CallLog.objects.filter(
        recording_file__in=[
            'true', 'false', 'True', 'False', 'TRUE', 'FALSE',
            '1', '0', 'yes', 'no', 'Yes', 'No', 'YES', 'NO',
        ]
    ).update(recording_file=None, recording_storage_status='telecmi')


class Migration(migrations.Migration):

    dependencies = [
        ('telephony', '0007_telecmicredential_dek_wrapped'),
    ]

    operations = [
        migrations.CreateModel(
            name='ZataStorageCredential',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('tenant_id', models.UUIDField(db_index=True, unique=True)),
                ('endpoint_url', models.URLField(default='https://idr01.zata.ai')),
                ('bucket_name', models.CharField(max_length=255)),
                ('access_key_id', models.CharField(max_length=255)),
                ('secret_access_key_encrypted', models.TextField()),
                ('dek_wrapped', models.TextField(blank=True, default='')),
                ('object_prefix', models.CharField(default='telephony/recordings', help_text='Key prefix inside the private bucket.', max_length=255)),
                ('region_name', models.CharField(default='us-east-1', max_length=64)),
                ('is_active', models.BooleanField(default=True)),
                ('last_tested_at', models.DateTimeField(blank=True, null=True)),
                ('last_test_error', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'telephony_zata_storage_credentials',
            },
        ),
        migrations.AddIndex(
            model_name='zatastoragecredential',
            index=models.Index(fields=['tenant_id'], name='idx_tel_zata_tenant'),
        ),
        migrations.AddField(
            model_name='calllog',
            name='raw_payload',
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='calllog',
            name='recording_archive_attempts',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='calllog',
            name='recording_archive_error',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='calllog',
            name='recording_archived_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='calllog',
            name='recording_content_type',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='calllog',
            name='recording_object_key',
            field=models.CharField(blank=True, max_length=512, null=True),
        ),
        migrations.AddField(
            model_name='calllog',
            name='recording_sha256',
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name='calllog',
            name='recording_size',
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='calllog',
            name='recording_storage_status',
            field=models.CharField(choices=[('telecmi', 'Available from TeleCMI'), ('pending', 'Waiting to archive'), ('archiving', 'Archiving to Zata'), ('archived', 'Archived in Zata'), ('failed', 'Archive failed')], default='telecmi', max_length=20),
        ),
        migrations.AddField(
            model_name='calllog',
            name='request_id',
            field=models.CharField(blank=True, db_index=True, max_length=128, null=True),
        ),
        migrations.RunPython(clear_invalid_recording_flags, migrations.RunPython.noop),
    ]
