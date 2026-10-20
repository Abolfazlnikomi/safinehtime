from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('questions_module', '0011_merge_20260922_0100')]

    operations = [
        migrations.CreateModel(
            name='Lesson',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200, verbose_name='نام درس')),
                ('chapter', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lessons', to='questions_module.chapter', verbose_name='فصل')),
            ],
            options={'verbose_name': 'درس کتاب', 'verbose_name_plural': 'درس‌های کتاب',
                     'ordering': ['chapter_id', 'name'], 'constraints': [models.UniqueConstraint(fields=('chapter', 'name'), name='unique_lesson_per_chapter')]},
        ),
        migrations.AddField(model_name='topic', name='lesson', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='topics', to='questions_module.lesson', verbose_name='درس کتاب')),
        migrations.RemoveConstraint(model_name='topic', name='unique_topic_per_chapter'),
        migrations.AddConstraint(model_name='topic', constraint=models.UniqueConstraint(fields=('chapter', 'name'), condition=models.Q(lesson__isnull=True), name='unique_topic_per_chapter')),
        migrations.AddConstraint(model_name='topic', constraint=models.UniqueConstraint(fields=('lesson', 'name'), name='unique_topic_per_lesson')),
    ]
