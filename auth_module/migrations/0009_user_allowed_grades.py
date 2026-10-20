from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('auth_module', '0008_consultant_ticket_student_gender_and_more'),
        ('users_module', '0007_deactivate_legacy_school_values'),
    ]

    operations = [
        migrations.AddField(
            model_name='user', name='allowed_grades',
            field=models.ManyToManyField(blank=True, related_name='authorized_users',
                                        to='users_module.grade', verbose_name='پایه‌های مجاز مدیریت'),
        ),
    ]
