# Generated by Django 3.0.3 on 2020-11-26 12:44

import core.fields
import core.utils
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('claim', '0011_auto_20201126_1244'),
    ]

    operations = [
        migrations.RunSQL('ALTER TABLE "tblClaimServices" ADD "JsonExt" TEXT'),
        migrations.RunSQL('ALTER TABLE "tblClaimItems" ADD "JsonExt" TEXT')
    ]
