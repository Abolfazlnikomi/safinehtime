from django.db import migrations


def seed_access(apps, schema_editor):
    apps.get_model('users_module', 'Access').objects.get_or_create(name='student_record', subject=None)


class Migration(migrations.Migration):
    dependencies = [('student_records', '0001_initial'), ('users_module', '0009_school_city_alter_access_name')]
    operations = [migrations.RunPython(seed_access, migrations.RunPython.noop)]
