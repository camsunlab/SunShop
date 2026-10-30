from django.db import migrations


def create_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for name in ("PI", "Purchaser"):
        Group.objects.get_or_create(name=name)


class Migration(migrations.Migration):
    dependencies = [
        ("purchasing", "0001_initial"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [migrations.RunPython(create_groups, migrations.RunPython.noop)]
