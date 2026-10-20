from django import template

from users_module.models import FieldOfStudy, Grade
from users_module.school_structure import is_general_grade


register = template.Library()


@register.simple_tag
def curriculum_selection_data():
    return {
        'grades': [{'id': grade.pk, 'general': is_general_grade(grade)}
                   for grade in Grade.objects.filter(is_active=True).only('id', 'title')],
        'fields': list(FieldOfStudy.objects.filter(is_active=True).values('id', 'grade_id', 'title')),
    }
