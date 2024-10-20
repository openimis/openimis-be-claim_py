# Generated by Django 3.2.18 on 2023-04-04 14:11

from django.db import migrations


def forwards_func(apps, schema_editor):
    RoleRight = apps.get_model('core', 'RoleRight')
    Role = apps.get_model('core', 'Role')

    # Delete enrollment officer right to interact with claim data
    # Enrollment officer is predefined system role with id 1
    eo_roles = Role.objects.filter(is_system=1, validity_to__isnull=True)
    # Prohibited Roles ID are gql_query_claims_perms (111001)
    # and gql_mutation_deliver_claim_feedback_perms (111009)
    rights_id = [111001, 111009]
    for right_id in rights_id:
        RoleRight.objects.filter(
            role__in=eo_roles,
            right_id=right_id,
            validity_to__isnull=True
        ).delete()


def reverse_func(apps, schema_editor):
    RoleRight = apps.get_model('core', 'RoleRight')
    Role = apps.get_model('core', 'Role')
    eo_roles = Role.objects.filter(is_system=1, validity_to__isnull=True)
    rights_id = [111001, 111009]
    for eo_role in v:
        for right_id in rights_id:
            RoleRight(
                role_id=eo_role.id,
                right_id=right_id,
                audit_user_id=None,
            ).save()


class Migration(migrations.Migration):

    dependencies = [
        ("claim", "0014_change_code_limit"),
    ]

    operations = [
        migrations.RunPython(forwards_func, reverse_func),
    ]
