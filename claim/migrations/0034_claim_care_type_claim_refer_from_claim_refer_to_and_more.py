# Generated by Django 4.2.15 on 2024-08-22 15:14

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('location', '0018_auto_20230925_2243'),
        ('claim', '0033_claim_care_type_claim_patient_condition_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='claim',
            name='referral_code',
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
    ]
