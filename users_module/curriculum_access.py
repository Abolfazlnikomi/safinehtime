from django.db.models import Q

from .models import Access, Grade, Subject


def grades_for_user(user):
    grades = Grade.objects.filter(is_active=True)
    if not user or not user.is_authenticated or not user.is_active:
        return grades.none()
    if user.is_superuser:
        return grades
    if user.role == 'STUDENT':
        return grades.filter(fields__student__user=user, fields__is_active=True).distinct()
    if user.role == 'PROVINCE_TRUSTEE':
        return grades.filter(school__province__trustees__user=user).distinct()
    if user.role in {'ADMIN', 'CONSULTANT'}:
        return grades.filter(authorized_users=user)
    return grades.filter(
        Q(authorized_users=user) | Q(fields__subjects__access__users=user)
        | Q(fields__subjects__access__consultant__consultant=user)
    ).distinct()


def subjects_for_user(user, access_code=Access.Code.BANK):
    subjects = Subject.objects.filter(
        is_active=True, field__is_active=True, field__grade__in=grades_for_user(user),
    )
    if not user or not user.is_authenticated:
        return subjects.none()
    if user.is_superuser or user.role in {'ADMIN', 'PROVINCE_TRUSTEE'}:
        return subjects
    if user.role == 'STUDENT':
        return subjects.filter(field__student__user=user).distinct()
    accesses = Access.objects.filter(name=access_code).filter(
        Q(users=user) | Q(consultant__consultant=user)
    )
    if accesses.filter(subject__isnull=True).exists():
        return subjects
    return subjects.filter(pk__in=accesses.values('subject_id'))
