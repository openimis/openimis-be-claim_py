# Generated by Django 3.2.20 on 2023-08-23 01:17

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('claim', '0022_alter_feedbackprompt_phone_number'),
    ]

    operations = [
        migrations.AddField(
            model_name='claim',
            name='restore',
            field=models.ForeignKey(blank=True, db_column='RestoredClaim', null=True, on_delete=django.db.models.deletion.DO_NOTHING, to='claim.claim'),
        ),
    ]