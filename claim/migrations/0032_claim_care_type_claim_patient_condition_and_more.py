# Generated by Django 4.2.15 on 2024-08-22 10:54

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('location', '0018_auto_20230925_2243'),
        ('claim', '0031_claim_care_type_claim_pre_authorization_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='claim',
            name='patient_condition',
            field=models.CharField(blank=True, max_length=2, null=True),
        )
    ]
