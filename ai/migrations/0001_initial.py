# Generated migration for AI chat session persistence
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='AIChatSession',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tenant_id', models.UUIDField(db_index=True)),
                ('user_id', models.UUIDField(db_index=True)),
                ('title', models.CharField(blank=True, default='', max_length=200)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'ai_chat_sessions',
                'ordering': ['-updated_at'],
            },
        ),
        migrations.CreateModel(
            name='AIChatMessage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(choices=[('user', 'user'), ('assistant', 'assistant'), ('tool', 'tool')], max_length=16)),
                ('content', models.TextField()),
                ('sequence', models.PositiveIntegerField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('session', models.ForeignKey(db_column='session_id', on_delete=django.db.models.deletion.CASCADE, related_name='messages', to='ai.ai_chat_session')),
            ],
            options={
                'db_table': 'ai_chat_messages',
                'ordering': ['sequence'],
            },
        ),
        migrations.AddIndex(
            model_name='aichatsession',
            index=models.Index(fields=['tenant_id', 'user_id', '-updated_at'], name='ai_chat_ses_tenant_i_f8a7c2_idx'),
        ),
        migrations.AddIndex(
            model_name='aichatsession',
            index=models.Index(fields=['tenant_id', 'user_id', '-created_at'], name='ai_chat_ses_tenant_i_9b3d4e_idx'),
        ),
        migrations.AddIndex(
            model_name='aichatmessage',
            index=models.Index(fields=['session', 'sequence'], name='ai_chat_mes_session__a1b2c3_idx'),
        ),
        migrations.AddConstraint(
            model_name='aichatsession',
            constraint=models.UniqueConstraint(fields=('tenant_id', 'user_id', 'id'), name='unique_ai_chat_session_per_user'),
        ),
        migrations.AddConstraint(
            model_name='aichatmessage',
            constraint=models.UniqueConstraint(fields=('session', 'sequence'), name='unique_sequence_per_session'),
        ),
    ]