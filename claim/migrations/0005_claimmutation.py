# Generated by Django 2.1.11 on 2019-10-29 18:49

from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_officer_role_roleright_userrole'),
        ('claim', '0004_claimattachment'),
    ]

    operations = [
        migrations.CreateModel(
            name='ClaimMutation',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('claim', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, related_name='mutations', to='claim.Claim')),
                ('mutation', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, related_name='claims', to='core.MutationLog')),
            ],
            options={
                'db_table': 'claim_ClaimMutation',
                'managed': True,
            },
        ),
    ]