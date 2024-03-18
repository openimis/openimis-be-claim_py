# Generated by Django 4.2.10 on 2024-02-13 21:47

import core.fields
import datetime
from django.db import migrations, models
import django.db.models.deletion

from claim.models import GeneralClaimAttachmentType


def insert_default_type(apps, schema_editor):
    PredefinedType = apps.get_model("claim", "ClaimAttachmentType")

    # class ClaimAttachmentType(core_models.VersionedModel):
    #     id = models.SmallIntegerField(db_column='ClaimAttachmentTypeId', primary_key=True)
    #     claim_attachment_type = models.CharField(db_column='ClaimAttachmentType', max_length=50)
    #     is_autogenerated = models.BooleanField(default=False)
    #     claim_general_type = models.CharField(max_length=10, default=GeneralClaimAttachmentType.FILE,
    #                                           choices=GeneralClaimAttachmentType.choices)
    pt_dict_file = {'id': 1, 'claim_attachment_type': 'default', 'is_autogenerated': False,
                    'claim_general_type': GeneralClaimAttachmentType.FILE}
    pt_dict_url = {'id': 2, 'claim_attachment_type': 'default', 'is_autogenerated': False,
                   'claim_general_type': GeneralClaimAttachmentType.URL}

    default_type_for_file = PredefinedType.objects.create(**pt_dict_file)
    default_type_for_file.save()

    default_type_for_url = PredefinedType.objects.create(**pt_dict_url)
    default_type_for_url.save()


class Migration(migrations.Migration):
    replaces = [('claim', '0028_claimattachmenttype_claimattachment_predefined_type')]


    dependencies = [
        ('claim', '0029_rename_pcpdate_claimserviceitem_created_date_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='ClaimAttachmentType',
            fields=[
                ('validity_from', core.fields.DateTimeField(db_column='ValidityFrom', default=datetime.datetime.now)),
                ('validity_to', core.fields.DateTimeField(blank=True, db_column='ValidityTo', null=True)),
                ('legacy_id', models.IntegerField(blank=True, db_column='LegacyID', null=True)),
                ('id', models.SmallIntegerField(db_column='ClaimAttachmentTypeId', primary_key=True, serialize=False)),
                ('claim_attachment_type', models.CharField(db_column='ClaimAttachmentType', max_length=50)),
                ('is_autogenerated', models.BooleanField(default=False)),
                ('claim_general_type', models.CharField(choices=[('URL', 'Url'), ('FILE', 'File')], default='FILE', max_length=10)),
            ],
            options={
                'db_table': 'claim_ClaimAttachment_ClaimAttachmentType',
                'managed': True,
            },
        ),
        migrations.AddField(
            model_name='claimattachment',
            name='predefined_type',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='type_dropdown', to='claim.claimattachmenttype'),
        ),
        migrations.RunPython(code=insert_default_type, reverse_code=migrations.RunPython.noop),
    ]
