from django.db import migrations


GRADES = ('پنجم', 'ششم', 'هفتم', 'هشتم', 'نهم', 'دهم', 'یازدهم', 'دوازدهم')
FIELDS = ('ریاضی', 'تجربی', 'انسانی')


def rebuild_structure(apps, schema_editor):
    alias = schema_editor.connection.alias
    Grade = apps.get_model('users_module', 'Grade')
    Field = apps.get_model('users_module', 'FieldOfStudy')
    School = apps.get_model('users_module', 'School')
    old_grades = list(Grade.objects.using(alias).all())
    old_fields = list(Field.objects.using(alias).all())
    old_by_id = {grade.pk: grade for grade in old_grades}
    translations = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')

    def title_for(value):
        title = value.strip().translate(translations).removeprefix('پایه').strip()
        if title.isdecimal() and 5 <= int(title) <= 12:
            return GRADES[int(title) - 5]
        return title

    def referenced(model, identifier):
        for related in apps.get_models(include_auto_created=True):
            for field in related._meta.local_fields:
                if field.is_relation and field.remote_field.model == model:
                    if related.objects.using(alias).filter(**{field.attname: identifier}).exists():
                        return True
        return False

    for grade in old_grades:
        if title_for(grade.title) not in GRADES:
            raise ValueError('پایه نامعتبر موجود است؛ برای جلوگیری از حذف اطلاعات وابسته، پیش از بازسازی آن را اصلاح کنید.')
    for field in old_fields:
        grade = old_by_id[field.grade_id]
        if title_for(grade.title) in GRADES[5:] and field.title not in FIELDS and referenced(Field, field.pk):
            raise ValueError('رشته قدیمی دارای اطلاعات وابسته است؛ پیش از بازسازی آن را به ریاضی، تجربی یا انسانی تبدیل کنید.')

    grade_targets = {}
    field_targets = {}
    for school in School.objects.using(alias).order_by('pk'):
        for index, title in enumerate(GRADES):
            grade = Grade.objects.using(alias).create(school_id=school.pk, title=title, is_active=True)
            grade_targets[school.pk, title] = grade.pk
            for field_title in (('عمومی',) if index < 5 else FIELDS):
                field = Field.objects.using(alias).create(grade_id=grade.pk, title=field_title, is_active=True)
                field_targets[school.pk, title, field_title] = field.pk

    def replace_references(model, mapping, skip_models=()):
        for related in apps.get_models(include_auto_created=True):
            if related in skip_models:
                continue
            for field in related._meta.local_fields:
                if not field.is_relation or field.remote_field.model != model:
                    continue
                for previous, replacement in mapping.items():
                    queryset = related.objects.using(alias).filter(**{field.attname: previous})
                    if related._meta.auto_created:
                        for item in list(queryset):
                            values = {column.attname: getattr(item, column.attname)
                                      for column in related._meta.local_fields if not column.primary_key}
                            values[field.attname] = replacement
                            related.objects.using(alias).get_or_create(**values)
                        queryset.delete()
                    else:
                        queryset.update(**{field.attname: replacement})

    grade_mapping = {grade.pk: grade_targets[grade.school_id, title_for(grade.title)] for grade in old_grades}
    field_mapping = {}
    for field in old_fields:
        grade = old_by_id[field.grade_id]
        title = title_for(grade.title)
        field_title = 'عمومی' if title in GRADES[:5] else field.title
        if (grade.school_id, title, field_title) in field_targets:
            field_mapping[field.pk] = field_targets[grade.school_id, title, field_title]
    replace_references(Field, field_mapping)
    replace_references(Grade, grade_mapping, skip_models=(Field,))
    Field.objects.using(alias).filter(pk__in=[field.pk for field in old_fields]).delete()
    Grade.objects.using(alias).filter(pk__in=[grade.pk for grade in old_grades]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('users_module', '0007_deactivate_legacy_school_values'),
        ('auth_module', '0009_user_allowed_grades'),
        ('questions_module', '0012_lesson_topic_lesson'),
        ('courses_module', '0001_initial'),
        ('news_module', '0002_article_target_grades_article_target_provinces_and_more'),
        ('quiz_module', '0003_quizevent_alter_quiz_options_and_more'),
    ]
    operations = [migrations.RunPython(rebuild_structure)]
