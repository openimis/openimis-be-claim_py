# Generated by Django 3.0.14 on 2022-08-22 18:48

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('claim', '0020_alter_claim_code'),
    ]

    operations = [
        migrations.CreateModel(
            name='ClaimServiceService',
            fields=[
                ('idCss', models.AutoField(db_column='idCss', primary_key=True, serialize=False)),
                ('qty_provided', models.IntegerField(blank=True, db_column='qty_provided', null=True)),
                ('qty_displayed', models.IntegerField(blank=True, db_column='qty_displayed', null=True)),
                ('pcpDate', models.DateTimeField(blank=True, db_column='created_date', default=django.utils.timezone.now, null=True)),
                ('price_asked', models.DecimalField(blank=True, db_column='price', decimal_places=2, max_digits=18, null=True)),
                ('claimlinkedService', models.ForeignKey(db_column='claimlinkedService', on_delete=django.db.models.deletion.DO_NOTHING, related_name='claimlinkedService', to='claim.ClaimService')),
                ('service', models.ForeignKey(db_column='ServiceId', on_delete=django.db.models.deletion.DO_NOTHING, related_name='claimServices', to='medical.Service')),
            ],
            options={
                'db_table': 'tblClaimServicesService',
                'managed': True,
            },
        ),
        migrations.CreateModel(
            name='ClaimServiceItem',
            fields=[
                ('idCsi', models.AutoField(db_column='idCsi', primary_key=True, serialize=False)),
                ('qty_provided', models.IntegerField(blank=True, db_column='qty_provided', null=True)),
                ('qty_displayed', models.IntegerField(blank=True, db_column='qty_displayed', null=True)),
                ('pcpDate', models.DateTimeField(blank=True, db_column='created_date', default=django.utils.timezone.now, null=True)),
                ('price_asked', models.DecimalField(blank=True, db_column='price', decimal_places=2, max_digits=18, null=True)),
                ('claimlinkedItem', models.ForeignKey(db_column='ClaimServiceID', on_delete=django.db.models.deletion.DO_NOTHING, related_name='claimlinkedItem', to='claim.ClaimService')),
                ('item', models.ForeignKey(db_column='ItemID', on_delete=django.db.models.deletion.DO_NOTHING, related_name='claimItems', to='medical.Item')),
            ],
            options={
                'db_table': 'tblClaimServicesItems',
                'managed': True,
            },
        ),
    ]