from users_module.models import Subject
from users_module.school_structure import GRADE_TITLES, grade_title


def normalize_search(value):
    return str(value or '').translate(str.maketrans('يك۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', 'یک01234567890123456789')).lower().strip()


def audience_catalog(students):
    records = list(students)
    field_ids = {student.field_id for student in records}
    subjects = list(Subject.objects.filter(field_id__in=field_ids, is_active=True).order_by('title').values('field_id', 'title'))
    provinces = {student.province.pk: student.province.get_name_display() for student in records}
    rows = []
    for student in records:
        label = f'{student.user.get_full_name() or student.user.username} · {student.field.grade.title} · {student.field.title} · {student.field.grade.school.name} · {student.province}'
        rows.append({
            'id': str(student.pk), 'province': str(student.province.pk),
            'grade': grade_title(student.field.grade), 'field': student.field.title,
            'field_id': student.field_id, 'search': normalize_search(f'{label} {student.user.username}'),
        })
    grades = {row['grade'] for row in rows}
    grade_order = {title: index for index, title in enumerate(GRADE_TITLES)}
    return {
        'students': rows, 'subjects': subjects,
        'provinces': [{'value': str(province_id), 'label': label} for province_id, label in sorted(provinces.items(), key=lambda item: item[1])],
        'grades': sorted(grades, key=lambda title: (grade_order.get(title, len(grade_order)), title)),
        'fields': sorted({row['field'] for row in rows}),
        'subject_titles': sorted({subject['title'] for subject in subjects}),
    }


def matching_student_ids(catalog, filters):
    subject_fields = {subject['field_id'] for subject in catalog['subjects'] if subject['title'] == filters.get('subject')}
    terms = normalize_search(filters.get('student_search')).split()
    matched = []
    for student in catalog['students']:
        if any(filters.get(name) and str(filters[name]) != student[key] for name, key in (
            ('province', 'province'), ('grade', 'grade'), ('field_of_study', 'field'),
        )):
            continue
        if filters.get('subject') and student['field_id'] not in subject_fields:
            continue
        if all(term in student['search'] for term in terms):
            matched.append(student['id'])
    return matched
