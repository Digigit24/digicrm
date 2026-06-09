from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0003_lead_attachments'),
    ]

    operations = [
        # 1. Create LeadGroup table
        migrations.CreateModel(
            name='LeadGroup',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('tenant_id', models.UUIDField(db_index=True)),
                ('name', models.TextField()),
                ('description', models.TextField(blank=True, null=True)),
                ('color_hex', models.TextField(blank=True, null=True)),
                ('created_by', models.UUIDField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'lead_groups',
                'ordering': ['name'],
            },
        ),
        migrations.AddIndex(
            model_name='leadgroup',
            index=models.Index(fields=['tenant_id'], name='idx_lead_groups_tenant_id'),
        ),
        migrations.AddConstraint(
            model_name='leadgroup',
            constraint=models.UniqueConstraint(
                fields=['tenant_id', 'name'],
                name='unique_lead_group_per_tenant'
            ),
        ),

        # 2. Create LeadGroupMembership through table
        migrations.CreateModel(
            name='LeadGroupMembership',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('group', models.ForeignKey(
                    db_column='group_id',
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='memberships',
                    to='crm.leadgroup',
                )),
                ('lead', models.ForeignKey(
                    db_column='lead_id',
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='group_memberships',
                    to='crm.lead',
                )),
                ('added_by', models.UUIDField(blank=True, null=True)),
                ('added_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'lead_group_memberships',
            },
        ),
        migrations.AlterUniqueTogether(
            name='leadgroupmembership',
            unique_together={('group', 'lead')},
        ),
        migrations.AddIndex(
            model_name='leadgroupmembership',
            index=models.Index(fields=['group'], name='idx_lgm_group'),
        ),
        migrations.AddIndex(
            model_name='leadgroupmembership',
            index=models.Index(fields=['lead'], name='idx_lgm_lead'),
        ),

        # 3. Add M2M field to Lead (via through table)
        migrations.AddField(
            model_name='lead',
            name='groups',
            field=models.ManyToManyField(
                blank=True,
                related_name='leads',
                through='crm.LeadGroupMembership',
                to='crm.LeadGroup',
            ),
        ),
    ]
